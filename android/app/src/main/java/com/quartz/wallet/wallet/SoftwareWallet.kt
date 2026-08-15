package com.quartz.wallet.wallet

import android.content.Context
import com.quartz.wallet.crypto.QuartzCrypto
import com.quartz.wallet.data.WalletStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Software wallet — keys on the phone (EncryptedSharedPreferences, Android Keystore),
 * fully standalone: works with or without an ESP32.
 *
 * Node API: https://quartz.preview.saasclaw.ai (testnet)
 */
object SoftwareWallet {

    const val NODE_URL = "https://quartz.preview.saasclaw.ai"
    const val FEE_SATS = 1_000L // node-enforced fee on /api/v1/send

    private val json = "application/json; charset=utf-8".toMediaType()
    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()

    data class NewWallet(
        val words: List<String>,
        val privateKey: ByteArray,
        val publicKey: ByteArray,
        val address: String
    )

    /** Generate a brand-new wallet: 16 bytes entropy → 12 words → keys → address. */
    fun create(): NewWallet {
        val entropy = QuartzCrypto.generateEntropy()
        val words = QuartzCrypto.entropyToWords(entropy)
        val priv = QuartzCrypto.privkeyFromMnemonic(words)
        val pub = QuartzCrypto.publicKeyFromPrivate(priv)
        return NewWallet(words, priv, pub, QuartzCrypto.publicKeyToAddress(pub))
    }

    /** Restore from a 12-word phrase. Throws IllegalArgumentException on bad words/checksum. */
    fun restore(words: List<String>): NewWallet {
        val normalized = words.map { it.trim().lowercase() }
        QuartzCrypto.wordsToEntropy(normalized) // validates words + checksum
        val priv = QuartzCrypto.privkeyFromMnemonic(normalized)
        val pub = QuartzCrypto.publicKeyFromPrivate(priv)
        return NewWallet(normalized, priv, pub, QuartzCrypto.publicKeyToAddress(pub))
    }

    fun save(context: Context, wallet: NewWallet) {
        WalletStore(context).saveSoftwareWallet(
            wallet.words.joinToString(" "),
            wallet.privateKey,
            wallet.publicKey,
            wallet.address
        )
    }

    fun load(context: Context): Triple<ByteArray, ByteArray, String>? { // priv, pub, address
        val store = WalletStore(context)
        val addr = store.getAddress() ?: return null
        val pub = store.getPublicKey() ?: return null
        val priv = store.getPrivateKey() ?: return null
        return Triple(priv, pub, addr)
    }

    fun wipe(context: Context) {
        WalletStore(context).deleteWallet()
    }

    // ------------------------------------------------------------------
    // Node API
    // ------------------------------------------------------------------

    data class BalanceInfo(val balanceSats: Long, val txCount: Int)

    suspend fun fetchBalance(address: String): Result<BalanceInfo> = withContext(Dispatchers.IO) {
        runCatching {
            val req = Request.Builder().url("$NODE_URL/api/v1/address/$address").build()
            http.newCall(req).execute().use { resp ->
                check(resp.isSuccessful) { "node returned HTTP ${resp.code}" }
                val body = JSONObject(resp.body!!.string())
                BalanceInfo(
                    balanceSats = body.optLong("balance_sats", 0L),
                    txCount = body.optJSONArray("transactions")?.length() ?: 0
                )
            }
        }
    }

    /**
     * Send QZ. Signs the exact message the node reconstructs:
     *   "{from}{to}{amount_sats}" ASCII bytes, Ed25519.
     */
    suspend fun send(
        privateKey: ByteArray,
        fromAddress: String,
        toAddress: String,
        amountSats: Long
    ): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            require(amountSats > 0) { "Amount must be positive" }
            require(QuartzCrypto.isValidAddress(toAddress)) { "Recipient address is not a valid Quartz address" }
            val msg = "$fromAddress$toAddress$amountSats".toByteArray(Charsets.US_ASCII)
            val sig = QuartzCrypto.sign(privateKey, msg)
            val payload = JSONObject().apply {
                put("from", fromAddress)
                put("to", toAddress)
                put("amount", amountSats / 1e8)
                put("signature", sig.joinToString("") { "%02x".format(it) })
                put("public_key", QuartzCrypto.publicKeyFromPrivate(privateKey)
                    .joinToString("") { "%02x".format(it) })
                put("message", msg.joinToString("") { "%02x".format(it) })
            }
            val req = Request.Builder()
                .url("$NODE_URL/api/v1/send")
                .post(payload.toString().toRequestBody(json))
                .build()
            http.newCall(req).execute().use { resp ->
                val text = resp.body!!.string()
                val body = JSONObject(text)
                check(resp.isSuccessful) { body.optString("error", "HTTP ${resp.code}") }
                body.optString("txid", "broadcast")
            }
        }
    }

    /** Testnet faucet — drips to any address. */
    suspend fun faucet(address: String): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            val payload = JSONObject().put("address", address)
                .toString().toRequestBody(json)
            val req = Request.Builder().url("$NODE_URL/api/v1/faucet")
                .post(payload).build()
            http.newCall(req).execute().use { resp ->
                val text = resp.body!!.string()
                val body = JSONObject(text)
                check(resp.isSuccessful) { body.optString("error", "HTTP ${resp.code}") }
                body.optString("message", "ok")
            }
        }
    }
}

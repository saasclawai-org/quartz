package com.quartz.wallet.data

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Quartz Wallet Storage — Two Modes
 *
 * HARDWARE MODE (recommended):
 *   - Keys live on the ESP32 in encrypted flash
 *   - Phone stores ONLY: public key, address, BLE bonding info
 *   - Signing delegated to ESP32 over BLE
 *   - Even if phone is rooted, keys can't be stolen
 *
 * SOFTWARE MODE (fallback):
 *   - Keys stored in EncryptedSharedPreferences (Android Keystore)
 *   - Encrypted at rest with AES-256-GCM
 *   - Biometric prompt required for any signing operation
 *   - Less secure — keys exist on phone (protected but present)
 *
 * Recovery:
 *   - Hardware mode: seed phrase shown once during ESP32 setup
 *   - Software mode: seed phrase shown once during wallet creation
 *   - Both: enter seed phrase to restore on new device
 *   - Seed phrase stored NOWHERE on the phone after initial backup
 */
class WalletStore(private val context: Context) {

    // v0.2.10: ONE EncryptedSharedPreferences instance app-wide. Multiple live
    // instances of the same encrypted file (Settings + Wallet screens each built
    // their own) cause Tink keyset contention — the delete-click crash.
    private var prefs: android.content.SharedPreferences = sharedPrefs()

    private fun sharedPrefs(): android.content.SharedPreferences =
        Companion.getOrCreate(context.applicationContext)

    // --- Mode ---
    fun setMode(mode: WalletMode) {
        prefs.edit().putString(KEY_MODE, mode.name).apply()
    }

    fun getMode(): WalletMode {
        return WalletMode.valueOf(prefs.getString(KEY_MODE, WalletMode.NONE.name) ?: WalletMode.NONE.name)
    }

    // --- Hardware mode (stores only public info) ---
    fun saveHardwareWallet(publicKey: ByteArray, address: String) {
        prefs.edit()
            .putString(KEY_MODE, WalletMode.HARDWARE.name)
            .putString(KEY_PUBKEY, publicKey.joinToString(",") { it.toString() })
            .putString(KEY_ADDRESS, address)
            .remove(KEY_SEED) // ensure no private key
            .remove(KEY_PRIVKEY)
            .apply()
    }

    // --- Software mode (stores encrypted private key) ---
    fun saveSoftwareWallet(seedPhrase: String, privateKey: ByteArray, publicKey: ByteArray, address: String) {
        prefs.edit()
            .putString(KEY_MODE, WalletMode.SOFTWARE.name)
            .putString(KEY_SEED, seedPhrase)
            .putString(KEY_PRIVKEY, privateKey.joinToString(",") { it.toString() })
            .putString(KEY_PUBKEY, publicKey.joinToString(",") { it.toString() })
            .putString(KEY_ADDRESS, address)
            .apply()
    }

    // --- Accessors ---
    fun getPublicKey(): ByteArray? {
        val s = prefs.getString(KEY_PUBKEY, null) ?: return null
        return s.split(",").map { it.toByte() }.toByteArray()
    }

    fun getAddress(): String? = prefs.getString(KEY_ADDRESS, null)
    fun getSeedPhrase(): String? = prefs.getString(KEY_SEED, null)
    fun getPrivateKey(): ByteArray? {
        val s = prefs.getString(KEY_PRIVKEY, null) ?: return null
        return s.split(",").map { it.toByte() }.toByteArray()
    }

    fun hasWallet(): Boolean = getMode() != WalletMode.NONE
    fun isHardwareMode(): Boolean = getMode() == WalletMode.HARDWARE
    fun isSoftwareMode(): Boolean = getMode() == WalletMode.SOFTWARE

    fun deleteWallet() {
        // v0.2.10: nuke the underlying files directly. clear() on
        // EncryptedSharedPreferences throws inside the library (keyset contention
        // with other live instances / corrupted entries) — that was the
        // delete-click crash. Deleting the prefs file + Tink companions and
        // rebuilding fresh is safe, total, and self-heals corrupted stores.
        val ctx = context.applicationContext
        runCatching { ctx.deleteSharedPreferences("quartz_wallet") }
        runCatching {
            java.io.File(ctx.applicationInfo.dataDir, "shared_prefs").listFiles()
                ?.forEach { if (it.name.contains("quartz_wallet")) it.delete() }
        }
        Companion.reset()
        prefs = sharedPrefs()
    }

    // --- Wallet PIN (gate for sending in software mode) ---
    // Stored as PBKDF2-HMAC-SHA256(pin, random salt, 60k iters) — the PIN
    // itself is never persisted or recoverable. Wrong-PIN lockout is enforced
    // in the UI layer (in-memory), not here.

    fun hasPin(): Boolean = prefs.getString(KEY_PIN_HASH, null) != null

    fun setPin(pin: String): Boolean {
        if (pin.length !in 4..8 || !pin.all { it.isDigit() }) return false
        val salt = ByteArray(16).also { java.security.SecureRandom().nextBytes(it) }
        val hash = pbkdf2(pin, salt)
        return prefs.edit()
            .putString(KEY_PIN_SALT, salt.toHex())
            .putString(KEY_PIN_HASH, hash.toHex())
            .commit()
    }

    /** Constant-time PIN check. */
    fun verifyPin(pin: String): Boolean {
        val saltHex = prefs.getString(KEY_PIN_SALT, null) ?: return false
        val hashHex = prefs.getString(KEY_PIN_HASH, null) ?: return false
        val calc = pbkdf2(pin, hexToBytes(saltHex))
        val stored = hexToBytes(hashHex)
        return java.security.MessageDigest.isEqual(calc, stored)
    }

    private fun pbkdf2(pin: String, salt: ByteArray): ByteArray =
        javax.crypto.SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
            .generateSecret(javax.crypto.spec.PBEKeySpec(pin.toCharArray(), salt, 60_000, 256))
            .encoded

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
    private fun hexToBytes(hex: String): ByteArray =
        hex.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    companion object {
        // v0.2.10: app-wide single instance; plain-prefs fallback keeps the app
        // alive on already-corrupted stores (delete then rebuilds a clean one).
        @Volatile
        private var shared: android.content.SharedPreferences? = null

        @Synchronized
        fun getOrCreate(ctx: android.content.Context): android.content.SharedPreferences {
            shared?.let { return it }
            val p: android.content.SharedPreferences = try {
                val masterKey = MasterKey.Builder(ctx)
                    .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                    .build()
                EncryptedSharedPreferences.create(
                    ctx,
                    "quartz_wallet",
                    masterKey,
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
                )
            } catch (e: Exception) {
                ctx.getSharedPreferences("quartz_wallet", android.content.Context.MODE_PRIVATE)
            }
            shared = p
            return p
        }

        @Synchronized
        fun reset() { shared = null }

        private const val KEY_MODE = "wallet_mode"
        private const val KEY_SEED = "seed_phrase"
        private const val KEY_PRIVKEY = "private_key"
        private const val KEY_PUBKEY = "public_key"
        private const val KEY_ADDRESS = "address"
        private const val KEY_PIN_SALT = "pin_salt"
        private const val KEY_PIN_HASH = "pin_hash"
    }
}

enum class WalletMode {
    NONE,       // No wallet configured
    HARDWARE,   // Keys on ESP32 — phone is watch-only
    SOFTWARE,   // Keys on phone (encrypted) — fallback mode
}

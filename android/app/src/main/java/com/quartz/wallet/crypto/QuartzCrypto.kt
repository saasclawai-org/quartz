package com.quartz.wallet.crypto

import java.security.MessageDigest
import java.security.SecureRandom
import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer

/**
 * Quartz wallet cryptography — Kotlin port of the reference implementation.
 *
 * Key model (matches quartz/crypto.py on the node):
 *   entropy (16 bytes)  →  12-word BIP-39 seed phrase (standard packing — same as firmware)
 *   privkey  = SLIP-0010 m/44'/789'/0'/0'/0' from BIP-39 PBKDF2 seed (canonical Quartz path)
 *   pubkey   = Ed25519(privkey)
 *   address  = Base58(0x3B || SHA-256(pubkey)[:20] || SHA-256d(payload)[:4])
 *
 * The same seed phrase restores the same wallet anywhere the reference
 * implementation runs (node, PWA). The ESP32 firmware currently uses a raw
 * RNG key with a lossy 12-word backup — to be fixed on the firmware side.
 */
object QuartzCrypto {

    const val ENTROPY_BYTES = 16
    const val PRIVKEY_BYTES = 32
    const val ADDRESS_VERSION: Int = 0x3B // matches node default (public_key_to_address)

    private const val BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

    // ------------------------------------------------------------------
    // Hashing
    // ------------------------------------------------------------------

    fun sha256(data: ByteArray): ByteArray =
        MessageDigest.getInstance("SHA-256").digest(data)

    fun sha256Double(data: ByteArray): ByteArray = sha256(sha256(data))

    // ------------------------------------------------------------------
    // Seed phrase  ↔  entropy
    // ------------------------------------------------------------------

    /** 16 bytes of entropy → 12 words (with 4-bit checksum). */
    fun entropyToWords(entropy: ByteArray): List<String> {
        require(entropy.size == ENTROPY_BYTES) { "entropy must be $ENTROPY_BYTES bytes" }
        val data = entropy + sha256(entropy)[0] // 17 bytes = 132 bits
        return (0 until 12).map { i ->
            val bitOffset = i * 11
            val byteIdx = bitOffset / 8
            val bitIdx = bitOffset % 8
            val index: Int = if (bitIdx <= 5) {
                var v = (data[byteIdx].toInt() and 0xFF) shl 8
                if (byteIdx + 1 < data.size) v = v or (data[byteIdx + 1].toInt() and 0xFF)
                (v ushr (16 - 11 - bitIdx)) and 0x7FF
            } else {
                var v = (data[byteIdx].toInt() and 0xFF) shl 16
                if (byteIdx + 1 < data.size) v = v or ((data[byteIdx + 1].toInt() and 0xFF) shl 8)
                if (byteIdx + 2 < data.size) v = v or (data[byteIdx + 1 + 1].toInt() and 0xFF)
                (v ushr (24 - 11 - bitIdx)) and 0x7FF
            }
            Bip39Wordlist.words[index % 2048]
        }
    }

    /** 12 words → 16 bytes of entropy; throws IllegalArgumentException on bad checksum. */
    fun wordsToEntropy(words: List<String>): ByteArray {
        require(words.size == 12) { "seed phrase must be 12 words" }
        val bits = StringBuilder()
        for (w in words) {
            val idx = Bip39Wordlist.indexOf(w.trim().lowercase())
            require(idx >= 0) { "\"$w\" is not in the BIP-39 wordlist" }
            bits.append(Integer.toBinaryString(idx or 0x800).substring(1)) // 11 bits
        }
        require(bits.length == 132) { "internal: expected 132 bits" }
        val entropy = ByteArray(ENTROPY_BYTES)
        for (i in 0 until ENTROPY_BYTES) {
            var v = 0
            for (k in 0 until 8) v = (v shl 1) or (bits[i * 8 + k] - '0')
            entropy[i] = v.toByte()
        }
        val checksumNibble = bits.substring(128, 132).toInt(2)
        val expected = (sha256(entropy)[0].toInt() and 0xFF) ushr 4
        require(checksumNibble == expected) { "Seed phrase checksum is invalid — check for typos" }
        return entropy
    }

    // ------------------------------------------------------------------
    // Key derivation — canonical Quartz path (quartz/crypto.py)
    //   BIP-39 PBKDF2 → SLIP-0010 m/44'/789'/account'/0'/index'
    // ------------------------------------------------------------------

    fun generateEntropy(): ByteArray =
        ByteArray(ENTROPY_BYTES).also { SecureRandom().nextBytes(it) }

    /** 12 words → Ed25519 private seed via SLIP-0010 (matches derive_quartz_keypair). */
    fun privkeyFromMnemonic(words: List<String>, account: Long = 0, index: Long = 0): ByteArray {
        val mnemonic = words.joinToString(" ")
        val seed = pbkdf2Sha512(
            mnemonic.toByteArray(Charsets.UTF_8),
            "mnemonic".toByteArray(Charsets.UTF_8), 2048, 64
        )
        var (k, c) = slip10Master(seed)
        for (idx in listOf(44L, 789L, account, 0L, index)) {
            val (nk, nc) = slip10Child(k, c, idx or 0x8000_0000L)
            k = nk; c = nc
        }
        return k
    }

    private fun slip10Master(seed: ByteArray): Pair<ByteArray, ByteArray> {
        val i = hmacSha512("ed25519 seed".toByteArray(Charsets.UTF_8), seed)
        return i.copyOfRange(0, 32) to i.copyOfRange(32, 64)
    }

    private fun slip10Child(parentKey: ByteArray, parentChain: ByteArray, index: Long): Pair<ByteArray, ByteArray> {
        val data = ByteArray(1 + 32 + 4)
        data[0] = 0
        parentKey.copyInto(data, 1)
        data[33] = ((index ushr 24) and 0xFF).toByte()
        data[34] = ((index ushr 16) and 0xFF).toByte()
        data[35] = ((index ushr 8) and 0xFF).toByte()
        data[36] = (index and 0xFF).toByte()
        val i = hmacSha512(parentChain, data)
        return i.copyOfRange(0, 32) to i.copyOfRange(32, 64)
    }

    private fun hmacSha512(key: ByteArray, data: ByteArray): ByteArray {
        val mac = org.bouncycastle.crypto.macs.HMac(org.bouncycastle.crypto.digests.SHA512Digest())
        mac.init(org.bouncycastle.crypto.params.KeyParameter(key))
        mac.update(data, 0, data.size)
        val out = ByteArray(64)
        mac.doFinal(out, 0)
        return out
    }

    private fun pbkdf2Sha512(password: ByteArray, salt: ByteArray, iterations: Int, dkLen: Int): ByteArray {
        val gen = org.bouncycastle.crypto.generators.PKCS5S2ParametersGenerator(org.bouncycastle.crypto.digests.SHA512Digest())
        gen.init(password, salt, iterations)
        val params = gen.generateDerivedMacParameters(dkLen * 8) as org.bouncycastle.crypto.params.KeyParameter
        return params.key
    }

    fun publicKeyFromPrivate(privkey: ByteArray): ByteArray =
        Ed25519PrivateKeyParameters(privkey, 0).generatePublicKey().getEncoded()

    fun sign(privkey: ByteArray, message: ByteArray): ByteArray {
        val signer = Ed25519Signer()
        signer.init(true, Ed25519PrivateKeyParameters(privkey, 0))
        signer.update(message, 0, message.size)
        return signer.generateSignature()
    }

    fun verify(publicKey: ByteArray, message: ByteArray, signature: ByteArray): Boolean =
        try {
            val signer = Ed25519Signer()
            signer.init(false, Ed25519PublicKeyParameters(publicKey, 0))
            signer.update(message, 0, message.size)
            signer.verifySignature(signature)
        } catch (e: Exception) {
            false
        }

    // ------------------------------------------------------------------
    // Address (matches quartz/crypto.py public_key_to_address)
    // ------------------------------------------------------------------

    fun publicKeyToAddress(publicKey: ByteArray): String {
        val payload = byteArrayOf(ADDRESS_VERSION.toByte()) + sha256(publicKey).copyOfRange(0, 20)
        val checksum = sha256Double(payload).copyOfRange(0, 4)
        return base58Encode(payload + checksum)
    }

    fun isValidAddress(address: String): Boolean = try {
        val decoded = base58Decode(address)
        decoded.size == 25 &&
                decoded[0] == ADDRESS_VERSION.toByte() &&
                sha256Double(decoded.copyOfRange(0, 21)).copyOfRange(0, 4)
                    .contentEquals(decoded.copyOfRange(21, 25))
    } catch (e: Exception) {
        false
    }

    // ------------------------------------------------------------------
    // Base58
    // ------------------------------------------------------------------

    fun base58Encode(input: ByteArray): String {
        val zeros = input.takeWhile { it == 0.toByte() }.size
        val sb = StringBuilder()
        var i = zeros
        val digits = ArrayList<Byte>(input.size * 138 / 100 + 1)
        digits.add(0)
        while (i < input.size) {
            var carry = input[i].toInt() and 0xFF
            for (j in digits.indices) {
                carry += (digits[j].toInt() and 0xFF) shl 8
                digits[j] = (carry % 58).toByte()
                carry /= 58
            }
            while (carry > 0) {
                digits.add((carry % 58).toByte())
                carry /= 58
            }
            i++
        }
        for (b in digits.reversed()) sb.append(BASE58_ALPHABET[(b.toInt() and 0xFF)])
        repeat(zeros) { sb.insert(0, '1') }
        return sb.toString()
    }

    fun base58Decode(input: String): ByteArray {
        val zeros = input.takeWhile { it == '1' }.length
        val bytes = ArrayList<Byte>(2)
        bytes.add(0)
        for (c in input) {
            val v = BASE58_ALPHABET.indexOf(c)
            require(v >= 0) { "invalid base58 character '$c'" }
            var carry = v
            for (j in bytes.indices) {
                carry += (bytes[j].toInt() and 0xFF) * 58
                bytes[j] = (carry and 0xFF).toByte()
                carry = carry ushr 8
            }
            while (carry > 0) {
                bytes.add((carry and 0xFF).toByte())
                carry = carry ushr 8
            }
        }
        // Big-endian value without leading zeros, plus explicit '1'-encoded leading zero bytes
        val significant = bytes.asReversed().dropWhile { it == 0.toByte() }
        return ByteArray(zeros) + significant
    }
}

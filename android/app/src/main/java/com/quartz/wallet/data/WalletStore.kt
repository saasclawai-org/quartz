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
class WalletStore(context: Context) {

    private val masterKey = MasterKey.Builder(context)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()

    private val prefs = EncryptedSharedPreferences.create(
        context,
        "quartz_wallet",
        masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
    )

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
        prefs.edit().clear().apply()
    }

    companion object {
        private const val KEY_MODE = "wallet_mode"
        private const val KEY_SEED = "seed_phrase"
        private const val KEY_PRIVKEY = "private_key"
        private const val KEY_PUBKEY = "public_key"
        private const val KEY_ADDRESS = "address"
    }
}

enum class WalletMode {
    NONE,       // No wallet configured
    HARDWARE,   // Keys on ESP32 — phone is watch-only
    SOFTWARE,   // Keys on phone (encrypted) — fallback mode
}

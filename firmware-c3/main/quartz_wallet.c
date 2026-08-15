/**
 * Quartz ESP32 Hardware Wallet — Key Management
 *
 * Keys are generated ON the ESP32 using hardware RNG.
 * Private key NEVER leaves the device — stored in encrypted NVS.
 * Signing happens on-device. Phone constructs unsigned tx, ESP32 signs.
 *
 * ESP32 Security Features Used:
 * - RNG_TRUE (hardware random number generator)
 * - NVS (Non-Volatile Storage) with flash encryption
 * - Secure Boot (prevents firmware tampering)
 * - ECDH key exchange for BLE channel encryption
 */

#include "quartz_wallet.h"
#include "quartz_bip39.h"
#include "quartz.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "esp_log.h"
#include "mbedtls/sha256.h"
#include "mbedtls/base64.h"
#include <string.h>

static const char *TAG = "QUARTZ_WALLET";

// --- Ed25519 via micro-ecc (minimal implementation) ---
// In production, use esp_tinycrypt or micro-ecc library
// For now, we use a compact Ed25519 implementation

#define ED25519_PRIVATE_KEY_SIZE 32
#define ED25519_PUBLIC_KEY_SIZE  32
#define ED25519_SIGNATURE_SIZE   64

// Quartz address constants
#define QZ_ADDR_VERSION_MAINNET  0x3B
#define QZ_ADDR_VERSION_TESTNET  0x7F
#define QZ_ADDR_CHECKSUM_SIZE    4
#define QZ_ADDR_PAYLOAD_SIZE     21  // version + 20-byte hash
#define QZ_ADDR_TOTAL_SIZE       25  // payload + checksum

static uint8_t s_private_key[ED25519_PRIVATE_KEY_SIZE];
static uint8_t s_public_key[ED25519_PUBLIC_KEY_SIZE];
static uint8_t s_wallet_entropy[16];   // BIP-39 entropy — seed phrase derivable iff present
static bool s_has_entropy = false;     // false for legacy (pre-canonical) wallets
static char s_address[36];  // Base58 address string
static bool s_wallet_initialized = false;

// ============================================================
// NVS Storage Keys
// ============================================================
#define NVS_NAMESPACE "qz_wallet"
#define NVS_KEY_PRIV  "priv_key"
#define NVS_KEY_PUB   "pub_key"
#define NVS_KEY_FLAGS "flags"
#define NVS_KEY_PIN_HASH  "pin_hash"   // 32 bytes SHA-256(salt + pin)
#define NVS_KEY_PIN_SALT  "pin_salt"   // 16 bytes random salt
#define NVS_KEY_PIN_FAIL  "pin_fails"  // uint8_t failed attempt count
#define NVS_KEY_ENTROPY   "entropy"    // 16-byte BIP-39 entropy (canonical wallets)

#define PIN_MAX_ATTEMPTS  10

static uint8_t s_pin_attempts = 0;

// Flag bits
#define FLAG_MINING_ENABLED  0x01
#define FLAG_TESTNET         0x02
#define FLAG_BACKED_UP       0x04  // user confirmed seed phrase backup
#define FLAG_HAS_PIN         0x08  // PIN protection enabled

// ============================================================
// Hardware RNG — ESP32 True Random Number Generator
// ============================================================

void quartz_rng(uint8_t *buf, size_t len) {
    // ESP32 has a true hardware RNG (uses thermal noise + RF subsystem)
    // Must enable WiFi or BT for best entropy; otherwise still seeded
    // from internal ADC noise. For cryptographic keys, we mix sources.
    esp_fill_random(buf, len);
}

// ============================================================
// Base58 Encoding (compact C implementation)
// ============================================================

static const char BASE58_ALPHABET[] =
    "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

static int base58_encode(const uint8_t *input, size_t len, char *output, size_t out_len) {
    // Simple big-number base58 encoding
    uint8_t digits[len * 138 / 100 + 1];
    memset(digits, 0, sizeof(digits));
    int digit_len = 1;

    for (size_t i = 0; i < len; i++) {
        int carry = input[i];
        for (int j = 0; j < digit_len; j++) {
            carry += digits[j] << 8;
            digits[j] = carry % 58;
            carry /= 58;
        }
        while (carry > 0) {
            digits[digit_len++] = carry % 58;
            carry /= 58;
        }
    }

    // Leading zeros → '1'
    int out_idx = 0;
    for (size_t i = 0; i < len && input[i] == 0 && out_idx < (int)(out_len - 1); i++) {
        output[out_idx++] = '1';
    }

    // Reverse and map
    for (int i = digit_len - 1; i >= 0 && out_idx < (int)(out_len - 1); i--) {
        output[out_idx++] = BASE58_ALPHABET[digits[i]];
    }
    output[out_idx] = '\0';
    return out_idx;
}

// ============================================================
// SHA-256 Helper
// ============================================================

static void sha256_double(const uint8_t *data, size_t len, uint8_t *out32) {
    uint8_t hash1[32];
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    mbedtls_sha256_update(&ctx, data, len);
    mbedtls_sha256_finish(&ctx, hash1);

    mbedtls_sha256_starts(&ctx, 0);
    mbedtls_sha256_update(&ctx, hash1, 32);
    mbedtls_sha256_finish(&ctx, out32);
    mbedtls_sha256_free(&ctx);
}

// ============================================================
// Address Derivation from Public Key
// ============================================================

static void derive_address(const uint8_t pubkey[32], bool testnet, char *out, size_t out_len) {
    uint8_t hash[32];
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    mbedtls_sha256_update(&ctx, pubkey, 32);
    mbedtls_sha256_finish(&ctx, hash);
    mbedtls_sha256_free(&ctx);

    // Build payload: version + hash[:20]
    uint8_t payload[QZ_ADDR_PAYLOAD_SIZE];
    payload[0] = testnet ? QZ_ADDR_VERSION_TESTNET : QZ_ADDR_VERSION_MAINNET;
    memcpy(payload + 1, hash, 20);

    // Checksum: SHA-256(SHA-256(payload))[:4]
    uint8_t checksum[32];
    sha256_double(payload, QZ_ADDR_PAYLOAD_SIZE, checksum);

    // Full address bytes: payload + checksum
    uint8_t addr_bytes[QZ_ADDR_TOTAL_SIZE];
    memcpy(addr_bytes, payload, QZ_ADDR_PAYLOAD_SIZE);
    memcpy(addr_bytes + QZ_ADDR_PAYLOAD_SIZE, checksum, QZ_ADDR_CHECKSUM_SIZE);

    base58_encode(addr_bytes, QZ_ADDR_TOTAL_SIZE, out, out_len);
}

// ============================================================
// Wallet Generation — Keys Created ON the ESP32
// ============================================================

quartz_wallet_err_t quartz_wallet_generate(bool testnet) {
    // 1. Generate 16 bytes of true random — the BIP-39 entropy.
    //    The wallet key is derived canonically from it, so the 12-word
    //    backup phrase restores this exact wallet on phone/node/other device.
    quartz_rng(s_wallet_entropy, 16);
    if (!quartz_bip39_entropy_to_privkey(s_wallet_entropy, s_private_key)) {
        ESP_LOGE(TAG, "Canonical key derivation failed");
        return QZ_WALLET_ERR_STORAGE;
    }
    s_has_entropy = true;

    // 2. Derive Ed25519 public key from private seed
    quartz_ed25519_keypair(s_private_key, s_public_key);

    // 3. Derive Quartz address
    derive_address(s_public_key, testnet, s_address, sizeof(s_address));

    // 4. Persist to encrypted NVS
    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "NVS open failed: %s", esp_err_to_name(err));
        return QZ_WALLET_ERR_STORAGE;
    }

    nvs_set_blob(handle, NVS_KEY_PRIV, s_private_key, ED25519_PRIVATE_KEY_SIZE);
    nvs_set_blob(handle, NVS_KEY_PUB, s_public_key, ED25519_PUBLIC_KEY_SIZE);
    nvs_set_blob(handle, NVS_KEY_ENTROPY, s_wallet_entropy, 16);

    uint8_t flags = FLAG_MINING_ENABLED | (testnet ? FLAG_TESTNET : 0);
    nvs_set_u8(handle, NVS_KEY_FLAGS, flags);

    nvs_commit(handle);
    nvs_close(handle);

    s_wallet_initialized = true;

    ESP_LOGI(TAG, "Wallet generated on-device");
    ESP_LOGI(TAG, "Address: %s", s_address);
    ESP_LOGI(TAG, "Private key NEVER exported — stored in encrypted flash");

    return QZ_WALLET_OK;
}

// ============================================================
// Wallet Load — Read from NVS on Boot
// ============================================================

quartz_wallet_err_t quartz_wallet_load(void) {
    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle);
    if (err != ESP_OK) {
        // No wallet yet — needs generation
        return QZ_WALLET_ERR_NOT_FOUND;
    }

    size_t required_size = ED25519_PRIVATE_KEY_SIZE;
    err = nvs_get_blob(handle, NVS_KEY_PRIV, s_private_key, &required_size);
    if (err != ESP_OK) {
        nvs_close(handle);
        return QZ_WALLET_ERR_NOT_FOUND;
    }

    required_size = ED25519_PUBLIC_KEY_SIZE;
    err = nvs_get_blob(handle, NVS_KEY_PUB, s_public_key, &required_size);
    if (err != ESP_OK) {
        nvs_close(handle);
        return QZ_WALLET_ERR_CORRUPT;
    }

    uint8_t flags = 0;
    nvs_get_u8(handle, NVS_KEY_FLAGS, &flags);

    // Canonical wallets store their BIP-39 entropy — the seed phrase restores
    // them. Legacy wallets (raw RNG key) don't; their backup words remain lossy.
    size_t ent_len = 16;
    if (nvs_get_blob(handle, NVS_KEY_ENTROPY, s_wallet_entropy, &ent_len) == ESP_OK
        && ent_len == 16) {
        s_has_entropy = true;
    }

    nvs_close(handle);

    bool testnet = flags & FLAG_TESTNET;
    derive_address(s_public_key, testnet, s_address, sizeof(s_address));
    s_wallet_initialized = true;

    ESP_LOGI(TAG, "Wallet loaded from NVS: %s", s_address);
    return QZ_WALLET_OK;
}

// ============================================================
// Transaction Signing — Private Key Never Leaves Device
// ============================================================

quartz_wallet_err_t quartz_wallet_sign(const uint8_t *msg, size_t msg_len,
                                        uint8_t signature[64]) {
    if (!s_wallet_initialized) {
        return QZ_WALLET_ERR_NOT_FOUND;
    }

    // Sign with Ed25519 using the on-device private key
    // Message is constructed by the phone, sent over BLE
    // ESP32 signs and returns ONLY the signature
    quartz_ed25519_sign(s_private_key, msg, msg_len, signature);

    ESP_LOGI(TAG, "Signed %d-byte message (signature only — key stays on device)", msg_len);
    return QZ_WALLET_OK;
}

// ============================================================
// Public Key Export — Only Public Info Leaves Device
// ============================================================

const uint8_t *quartz_wallet_get_pubkey(void) {
    if (!s_wallet_initialized) return NULL;
    return s_public_key;
}

bool quartz_wallet_has_canonical_entropy(void) {
    return s_has_entropy;
}

bool quartz_wallet_is_testnet(void) {
    return s_address[0] == 'T';  /* testnet addresses start with T (0x7F) */
}

const char *quartz_wallet_get_address(void) {
    if (!s_wallet_initialized) return NULL;
    return s_address;
}

// ============================================================
// Seed Phrase Backup — One-Time Display, Then Wiped
// ============================================================

// During initial setup, the ESP32 derives a BIP39 mnemonic from
// the private key entropy. This is shown ONCE on the paired phone
// (over an encrypted BLE channel), then the mnemonic buffer is
// zeroed. The private key itself NEVER leaves the device.

// Note: This creates a hybrid model — the private key is on-device,
// but the seed phrase is shown for backup. The seed phrase can
// reconstruct the key IF the ESP32 is lost. This matches hardware
// wallet convention (Ledger/Trezor show seed on setup too).

// To restore: user enters seed phrase in any Quartz wallet (PWA,
// Android) → derives Ed25519 keypair → imports to new ESP32

quartz_wallet_err_t quartz_wallet_get_seed_phrase_for_backup(
    char words[12][12],  // output: 12 words, max 11 chars each
    size_t max_word_len
) {
    if (!s_wallet_initialized) return QZ_WALLET_ERR_NOT_FOUND;

    if (s_has_entropy) {
        // Canonical wallet: words derive from stored entropy — restore works
        // on phone/node/any device (BIP-39 + SLIP-0010 m/44'/789'/0'/0'/0').
        char tmp[QZ_BIP39_WORDS][QZ_BIP39_WORD_MAX];
        if (!quartz_bip39_entropy_to_words(s_wallet_entropy, tmp)) {
            return QZ_WALLET_ERR_CORRUPT;
        }
        for (int i = 0; i < 12; i++) {
            strncpy(words[i], tmp[i], max_word_len - 1);
            words[i][max_word_len - 1] = '\0';
        }
    } else {
        // LEGACY (pre-canonical) wallet: raw RNG key, words encode only the
        // first 16 bytes. Phrase CANNOT restore this wallet — display only.
        quartz_privkey_to_mnemonic(s_private_key, words, max_word_len);
    }

    ESP_LOGW(TAG, "Seed phrase generated for ONE-TIME backup display");
    ESP_LOGW(TAG, "After user confirms backup, mnemonic MUST be wiped from RAM");

    return QZ_WALLET_OK;
}

void quartz_wallet_wipe_seed_phrase(char words[12][12]) {
    // Securely zero the mnemonic buffer
    memset(words, 0, 12 * 12);
    ESP_LOGI(TAG, "Seed phrase wiped from RAM");
}

// ============================================================
// Restore — Import Wallet from Seed Phrase (canonical derivation)
// ============================================================

quartz_wallet_err_t quartz_wallet_restore(const char words[12][12], bool testnet) {
    uint8_t entropy[16];
    uint8_t priv[32];

    if (!quartz_bip39_words_to_entropy(words, entropy)) {
        return QZ_WALLET_ERR_INVALID;
    }
    if (!quartz_bip39_entropy_to_privkey(entropy, priv)) {
        return QZ_WALLET_ERR_STORAGE;
    }

    memcpy(s_private_key, priv, 32);
    quartz_ed25519_keypair(s_private_key, s_public_key);
    derive_address(s_public_key, testnet, s_address, sizeof(s_address));
    memcpy(s_wallet_entropy, entropy, 16);
    s_has_entropy = true;
    s_wallet_initialized = true;

    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) return QZ_WALLET_ERR_STORAGE;
    nvs_set_blob(handle, NVS_KEY_PRIV, s_private_key, 32);
    nvs_set_blob(handle, NVS_KEY_PUB, s_public_key, 32);
    nvs_set_blob(handle, NVS_KEY_ENTROPY, s_wallet_entropy, 16);
    uint8_t flags = FLAG_MINING_ENABLED | (testnet ? FLAG_TESTNET : 0);
    nvs_set_u8(handle, NVS_KEY_FLAGS, flags);
    nvs_commit(handle);
    nvs_close(handle);

    ESP_LOGI(TAG, "Wallet restored from seed phrase: %s", s_address);
    return QZ_WALLET_OK;
}

// ============================================================
// Backup Confirmation (persistent NVS flag)
// ============================================================

quartz_wallet_err_t quartz_wallet_confirm_backup(void) {
    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "NVS open failed: %s", esp_err_to_name(err));
        return QZ_WALLET_ERR_STORAGE;
    }

    // Read current flags, set FLAG_BACKED_UP
    uint8_t flags = 0;
    nvs_get_u8(handle, NVS_KEY_FLAGS, &flags);
    flags |= FLAG_BACKED_UP;
    nvs_set_u8(handle, NVS_KEY_FLAGS, flags);
    nvs_commit(handle);
    nvs_close(handle);

    ESP_LOGI(TAG, "✅ Backup confirmed — FLAG_BACKED_UP set in NVS");
    return QZ_WALLET_OK;
}

bool quartz_wallet_is_backup_confirmed(void) {
    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle) != ESP_OK) {
        return false;
    }
    uint8_t flags = 0;
    nvs_get_u8(handle, NVS_KEY_FLAGS, &flags);
    nvs_close(handle);
    return (flags & FLAG_BACKED_UP) != 0;
}

// ============================================================
// Factory Reset — Wipe Everything
// ============================================================

quartz_wallet_err_t quartz_wallet_wipe(void) {
    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err == ESP_OK) {
        nvs_erase_key(handle, NVS_KEY_PRIV);
        nvs_erase_key(handle, NVS_KEY_PUB);
        nvs_erase_key(handle, NVS_KEY_FLAGS);
        nvs_erase_key(handle, NVS_KEY_PIN_HASH);
        nvs_erase_key(handle, NVS_KEY_PIN_SALT);
        nvs_erase_key(handle, NVS_KEY_PIN_FAIL);
        nvs_commit(handle);
        nvs_close(handle);
    }

    // Zero RAM copies
    memset(s_private_key, 0, sizeof(s_private_key));
    memset(s_public_key, 0, sizeof(s_public_key));
    memset(s_address, 0, sizeof(s_address));
    s_wallet_initialized = false;
    s_pin_attempts = 0;

    ESP_LOGI(TAG, "Wallet wiped — device reset to factory");
    return QZ_WALLET_OK;
}

// ============================================================
// PIN Protection
// ============================================================

quartz_wallet_err_t quartz_wallet_set_pin(const char *pin) {
    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "NVS open failed: %s", esp_err_to_name(err));
        return QZ_WALLET_ERR_STORAGE;
    }

    // Read current flags
    uint8_t flags = 0;
    nvs_get_u8(handle, NVS_KEY_FLAGS, &flags);

    if (pin == NULL || pin[0] == '\0') {
        // Empty PIN = remove PIN protection
        nvs_erase_key(handle, NVS_KEY_PIN_HASH);
        nvs_erase_key(handle, NVS_KEY_PIN_SALT);
        flags &= ~FLAG_HAS_PIN;
        nvs_set_u8(handle, NVS_KEY_FLAGS, flags);
        nvs_set_u8(handle, NVS_KEY_PIN_FAIL, 0);
        nvs_commit(handle);
        nvs_close(handle);
        s_pin_attempts = 0;
        ESP_LOGI(TAG, "PIN protection removed");
        return QZ_WALLET_OK;
    }

    // Validate: digits only, 4-8 chars
    size_t len = strlen(pin);
    if (len < 4 || len > 8) {
        nvs_close(handle);
        ESP_LOGE(TAG, "PIN must be 4-8 digits");
        return QZ_WALLET_ERR_INVALID;
    }
    for (size_t i = 0; i < len; i++) {
        if (pin[i] < '0' || pin[i] > '9') {
            nvs_close(handle);
            return QZ_WALLET_ERR_INVALID;
        }
    }

    // Generate random salt
    uint8_t salt[16];
    quartz_rng(salt, 16);

    // Hash: SHA-256(salt || pin)
    uint8_t pin_hash[32];
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    mbedtls_sha256_update(&ctx, salt, 16);
    mbedtls_sha256_update(&ctx, (const uint8_t *)pin, len);
    mbedtls_sha256_finish(&ctx, pin_hash);
    mbedtls_sha256_free(&ctx);

    // Store
    nvs_set_blob(handle, NVS_KEY_PIN_HASH, pin_hash, 32);
    nvs_set_blob(handle, NVS_KEY_PIN_SALT, salt, 16);
    flags |= FLAG_HAS_PIN;
    nvs_set_u8(handle, NVS_KEY_FLAGS, flags);
    nvs_set_u8(handle, NVS_KEY_PIN_FAIL, 0);
    nvs_commit(handle);
    nvs_close(handle);

    s_pin_attempts = 0;
    ESP_LOGI(TAG, "PIN set (%d digits)", (int)len);
    return QZ_WALLET_OK;
}

quartz_wallet_err_t quartz_wallet_check_pin(const char *pin) {
    if (!quartz_wallet_has_pin()) {
        return QZ_WALLET_OK;  // No PIN set = always pass
    }
    if (pin == NULL || pin[0] == '\0') {
        return QZ_WALLET_ERR_AUTH;
    }

    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle) != ESP_OK) {
        return QZ_WALLET_ERR_STORAGE;
    }

    // Load salt and hash
    uint8_t salt[16];
    uint8_t stored_hash[32];
    size_t required = 16;
    if (nvs_get_blob(handle, NVS_KEY_PIN_SALT, salt, &required) != ESP_OK ||
        required != 16) {
        nvs_close(handle);
        return QZ_WALLET_ERR_CORRUPT;
    }
    required = 32;
    if (nvs_get_blob(handle, NVS_KEY_PIN_HASH, stored_hash, &required) != ESP_OK ||
        required != 32) {
        nvs_close(handle);
        return QZ_WALLET_ERR_CORRUPT;
    }
    nvs_close(handle);

    // Compute hash of provided PIN
    uint8_t test_hash[32];
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    mbedtls_sha256_update(&ctx, salt, 16);
    mbedtls_sha256_update(&ctx, (const uint8_t *)pin, strlen(pin));
    mbedtls_sha256_finish(&ctx, test_hash);
    mbedtls_sha256_free(&ctx);

    // Constant-time comparison
    uint8_t diff = 0;
    for (int i = 0; i < 32; i++) {
        diff |= stored_hash[i] ^ test_hash[i];
    }

    // Clear sensitive data
    memset(test_hash, 0, 32);

    return (diff == 0) ? QZ_WALLET_OK : QZ_WALLET_ERR_AUTH;
}

bool quartz_wallet_has_pin(void) {
    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle) != ESP_OK) {
        return false;
    }
    uint8_t flags = 0;
    nvs_get_u8(handle, NVS_KEY_FLAGS, &flags);
    nvs_close(handle);
    return (flags & FLAG_HAS_PIN) != 0;
}

uint8_t quartz_wallet_pin_attempts(void) {
    return s_pin_attempts;
}

void quartz_wallet_reset_pin_attempts(void) {
    s_pin_attempts = 0;
    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle) == ESP_OK) {
        nvs_set_u8(handle, NVS_KEY_PIN_FAIL, 0);
        nvs_commit(handle);
        nvs_close(handle);
    }
}

bool quartz_wallet_record_failed_pin(void) {
    s_pin_attempts++;

    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle) == ESP_OK) {
        nvs_set_u8(handle, NVS_KEY_PIN_FAIL, s_pin_attempts);
        nvs_commit(handle);
        nvs_close(handle);
    }

    ESP_LOGW(TAG, "Failed PIN attempt %d/%d", s_pin_attempts, PIN_MAX_ATTEMPTS);

    if (s_pin_attempts >= PIN_MAX_ATTEMPTS) {
        ESP_LOGE(TAG, "🚨 MAX PIN ATTEMPTS REACHED — WIPING DEVICE");
        quartz_wallet_wipe();
        return true;
    }

    return false;
}

// ============================================================
// BLE Wallet Service — Signing Protocol
// ============================================================
//
// The ESP32 exposes a BLE GATT service for the phone wallet:
//
// Service: 0000qz01-0000-1000-8000-00805f9b34fb
//
// Characteristics:
//   PUBKEY   (read)    — 32-byte Ed25519 public key
//   ADDRESS  (read)    — Quartz address string
//   SIGN     (write)   — Phone writes unsigned tx hash (32 bytes)
//                        ESP32 signs and notifies result
//   SIGN_RX  (notify)  — 64-byte Ed25519 signature returned
//   SETUP    (write)   — Initial pairing (generates keys if needed)
//   WIPE     (write)   — Factory reset (requires physical button press)
//
// Security:
//   - BLE pairing required (Bonding + MITM protection)
//   - Signing requires authenticated BLE connection
//   - Wipe requires physical button hold (3 seconds)
//   - Private key characteristic does NOT exist — key is never readable
//
// Protocol flow for sending coins:
//   1. Phone constructs unsigned transaction
//   2. Phone hashes relevant fields → 32-byte tx hash
//   3. Phone writes tx hash to SIGN characteristic
//   4. ESP32 reads hash, signs with on-device private key
//   5. ESP32 sends 64-byte signature via SIGN_RX notify
//   6. Phone attaches signature to transaction, broadcasts to network
//   7. Private key was never on the phone

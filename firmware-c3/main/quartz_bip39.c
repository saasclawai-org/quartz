/*
 * Quartz canonical key derivation — BIP-39 + SLIP-0010 for ESP32 firmware.
 *
 * Byte-compatible with quartz/crypto.py (derive_quartz_keypair) and
 * QuartzCrypto.kt (privkeyFromMnemonic). See quartz_bip39.h for the path.
 */

#include "quartz_bip39.h"
#include "bip39_wordlist.h"

#include <string.h>

#include "mbedtls/sha512.h"
#include "mbedtls/sha256.h"
#define QZ_SHA256(data, len, out)  mbedtls_sha256(data, len, out, 0)

/* ------------------------------------------------------------------ */
/* HMAC-SHA512 (mbedtls HMAC needs md API; keep it dependency-light)  */
/* ------------------------------------------------------------------ */

static void hmac_sha512(const uint8_t *key, size_t key_len,
                        const uint8_t *data, size_t data_len,
                        uint8_t out[64])
{
    /* RFC 2104: H(K' ^ opad || H(K' ^ ipad || message)) with SHA-512 blocks = 128B */
    static const uint8_t ipad = 0x36;
    static const uint8_t opad = 0x5c;
    uint8_t k[128];
    uint8_t kx[128];
    mbedtls_sha512_context ctx;

    memset(k, 0, sizeof(k));
    if (key_len <= 128) {
        memcpy(k, key, key_len);
    } else {
        /* Keys longer than block size are hashed first */
        mbedtls_sha512(key, key_len, k, 0);
    }

    mbedtls_sha512_init(&ctx);

    /* inner */
    for (int i = 0; i < 128; i++) kx[i] = k[i] ^ ipad;
    mbedtls_sha512_starts(&ctx, 0);
    mbedtls_sha512_update(&ctx, kx, 128);
    mbedtls_sha512_update(&ctx, data, data_len);
    mbedtls_sha512_finish(&ctx, out);

    /* outer */
    for (int i = 0; i < 128; i++) kx[i] = k[i] ^ opad;
    mbedtls_sha512_starts(&ctx, 0);
    mbedtls_sha512_update(&ctx, kx, 128);
    mbedtls_sha512_update(&ctx, out, 64);
    mbedtls_sha512_finish(&ctx, out);

    mbedtls_sha512_free(&ctx);
}

/* ------------------------------------------------------------------ */
/* PBKDF2-HMAC-SHA512 (F = U1 ^ U2 ^ ... ^ Uc) — dkLen 64 for BIP-39  */
/* ------------------------------------------------------------------ */

static void pbkdf2_hmac_sha512(const uint8_t *pw, size_t pw_len,
                               const uint8_t *salt, size_t salt_len,
                               int iterations, uint8_t out[64])
{
    uint8_t block[salt_len + 4];
    uint8_t u[64], t[64];

    memcpy(block, salt, salt_len);
    /* BIP-39 uses a single block (dkLen == hLen == 64), i = 1 */
    block[salt_len + 0] = 0;
    block[salt_len + 1] = 0;
    block[salt_len + 2] = 0;
    block[salt_len + 3] = 1;

    hmac_sha512(pw, pw_len, block, salt_len + 4, u);
    memcpy(t, u, 64);
    for (int c = 1; c < iterations; c++) {
        hmac_sha512(pw, pw_len, u, 64, u);
        for (int i = 0; i < 64; i++) t[i] ^= u[i];
    }
    memcpy(out, t, 64);
}

/* ------------------------------------------------------------------ */
/* SLIP-0010 Ed25519 hardened derivation                              */
/* ------------------------------------------------------------------ */

static void slip10_master(const uint8_t seed[64], uint8_t key[32], uint8_t chain[32])
{
    uint8_t i[64];
    hmac_sha512((const uint8_t *)"ed25519 seed", 12, seed, 64, i);
    memcpy(key, i, 32);
    memcpy(chain, i + 32, 32);
}

static void slip10_child_hardened(const uint8_t key[32], const uint8_t chain[32],
                                  uint32_t index, uint8_t out_key[32], uint8_t out_chain[32])
{
    /* data = 0x00 || parent_key(32) || index(BE 4) */
    uint8_t data[37];
    uint8_t i[64];
    data[0] = 0;
    memcpy(data + 1, key, 32);
    data[33] = (index >> 24) & 0xFF;
    data[34] = (index >> 16) & 0xFF;
    data[35] = (index >> 8) & 0xFF;
    data[36] = index & 0xFF;
    hmac_sha512(chain, 32, data, sizeof(data), i);
    memcpy(out_key, i, 32);
    memcpy(out_chain, i + 32, 32);
}

/* ------------------------------------------------------------------ */
/* Word packing (standard BIP-39 MSB-first 11-bit indices)            */
/* ------------------------------------------------------------------ */

bool quartz_bip39_entropy_to_words(const uint8_t entropy[QZ_BIP39_ENTROPY_LEN],
                                   char words[QZ_BIP39_WORDS][QZ_BIP39_WORD_MAX])
{
    uint8_t data[17];
    uint8_t hash[32];

    if (QZ_SHA256(entropy, QZ_BIP39_ENTROPY_LEN, hash) != 0) return false;
    memcpy(data, entropy, 16);
    data[16] = hash[0]; /* first 4 bits land in word 12 */

    for (int i = 0; i < 12; i++) {
        int bit_offset = i * 11;
        int byte_idx = bit_offset / 8;
        int bit_idx = bit_offset % 8;
        uint32_t index;

        if (bit_idx <= 5) {
            uint32_t v = ((uint32_t)data[byte_idx] << 8);
            if (byte_idx + 1 < 17) v |= data[byte_idx + 1];
            index = (v >> (16 - 11 - bit_idx)) & 0x7FF;
        } else {
            uint32_t v = ((uint32_t)data[byte_idx] << 16);
            if (byte_idx + 1 < 17) v |= ((uint32_t)data[byte_idx + 1] << 8);
            if (byte_idx + 2 < 17) v |= data[byte_idx + 2];
            index = (v >> (24 - 11 - bit_idx)) & 0x7FF;
        }

        index %= 2048;
        strncpy(words[i], bip39_wordlist[index], QZ_BIP39_WORD_MAX - 1);
        words[i][QZ_BIP39_WORD_MAX - 1] = '\0';
    }
    return true;
}

bool quartz_bip39_words_to_entropy(const char words[QZ_BIP39_WORDS][QZ_BIP39_WORD_MAX],
                                   uint8_t entropy[QZ_BIP39_ENTROPY_LEN])
{
    /* 132 bits: 128 entropy + 4 checksum */
    uint8_t bits[132];
    int n = 0;
    uint8_t hash[32];
    int nbits = 0;

    for (int w = 0; w < 12; w++) {
        /* linear search — wordlist is 2048 entries, fine on device */
        int idx = -1;
        for (int i = 0; i < 2048; i++) {
            if (strncmp(bip39_wordlist[i], words[w], QZ_BIP39_WORD_MAX) == 0) {
                idx = i;
                break;
            }
        }
        if (idx < 0) return false; /* not in wordlist */
        for (int b = 10; b >= 0; b--) {
            bits[n++] = (idx >> b) & 1;
        }
    }

    for (int i = 0; i < 16; i++) {
        uint8_t v = 0;
        for (int k = 0; k < 8; k++) v = (v << 1) | bits[i * 8 + k];
        entropy[i] = v;
    }

    if (QZ_SHA256(entropy, 16, hash) != 0) return false;
    uint8_t checksum_nibble = 0;
    for (int k = 0; k < 4; k++) checksum_nibble = (checksum_nibble << 1) | bits[128 + k];
    (void)nbits;
    if (checksum_nibble != (hash[0] >> 4)) return false;

    return true;
}

/* ------------------------------------------------------------------ */
/* Canonical path                                                      */
/* ------------------------------------------------------------------ */

bool quartz_bip39_entropy_to_privkey(const uint8_t entropy[QZ_BIP39_ENTROPY_LEN],
                                     uint8_t privkey[32])
{
    char words[QZ_BIP39_WORDS][QZ_BIP39_WORD_MAX];
    if (!quartz_bip39_entropy_to_words(entropy, words)) return false;

    /* mnemonic string (space-separated) */
    char mnemonic[160];
    size_t off = 0;
    for (int i = 0; i < 12; i++) {
        size_t wl = strlen(words[i]);
        if (off + wl + 1 >= sizeof(mnemonic)) return false;
        memcpy(mnemonic + off, words[i], wl);
        off += wl;
        if (i < 11) mnemonic[off++] = ' ';
    }
    mnemonic[off] = '\0';

    /* BIP-39 seed */
    uint8_t seed[64];
    pbkdf2_hmac_sha512((const uint8_t *)mnemonic, off,
                       (const uint8_t *)"mnemonic", 8, 2048, seed);

    /* SLIP-0010 m/44'/789'/0'/0'/0' */
    uint8_t key[32], chain[32];
    slip10_master(seed, key, chain);
    static const uint32_t path[5] = { 44, 789, 0, 0, 0 };
    for (int i = 0; i < 5; i++) {
        slip10_child_hardened(key, chain, 0x80000000u | path[i], key, chain);
    }
    memcpy(privkey, key, 32);
    return true;
}

bool quartz_bip39_words_to_privkey(const char words[QZ_BIP39_WORDS][QZ_BIP39_WORD_MAX],
                                   uint8_t privkey[32])
{
    uint8_t entropy[QZ_BIP39_ENTROPY_LEN];
    if (!quartz_bip39_words_to_entropy(words, entropy)) return false;
    return quartz_bip39_entropy_to_privkey(entropy, privkey);
}

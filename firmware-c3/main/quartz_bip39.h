#ifndef QUARTZ_BIP39_H
#define QUARTZ_BIP39_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/*
 * Quartz canonical key derivation — firmware side.
 *
 * Matches quartz/crypto.py (reference node) and QuartzCrypto.kt (Android):
 *
 *   entropy (16 bytes)
 *     → BIP-39 12-word phrase (standard packing, 4-bit checksum)
 *     → BIP-39 seed = PBKDF2-HMAC-SHA512(mnemonic, "mnemonic", 2048 iters, 64B)
 *     → SLIP-0010 Ed25519 master = HMAC-SHA512("ed25519 seed", seed)
 *     → m/44'/789'/0'/0'/0'  (Quartz coin 789)
 *     → 32-byte Ed25519 private seed
 *
 * The same words restore the same wallet on node, Android, and device.
 */

#define QZ_BIP39_ENTROPY_LEN 16
#define QZ_BIP39_WORDS       12
#define QZ_BIP39_WORD_MAX    12   /* longest BIP-39 word + NUL ("difficulty" = 10) */

bool quartz_bip39_entropy_to_words(const uint8_t entropy[QZ_BIP39_ENTROPY_LEN],
                                   char words[QZ_BIP39_WORDS][QZ_BIP39_WORD_MAX]);

/* Words → entropy with checksum validation. Returns false on bad words/checksum. */
bool quartz_bip39_words_to_entropy(const char words[QZ_BIP39_WORDS][QZ_BIP39_WORD_MAX],
                                   uint8_t entropy[QZ_BIP39_ENTROPY_LEN]);

/* Canonical private key from entropy (BIP-39 + SLIP-0010 m/44'/789'/0'/0'/0'). */
bool quartz_bip39_entropy_to_privkey(const uint8_t entropy[QZ_BIP39_ENTROPY_LEN],
                                     uint8_t privkey[32]);

/* Canonical private key directly from the word list. */
bool quartz_bip39_words_to_privkey(const char words[QZ_BIP39_WORDS][QZ_BIP39_WORD_MAX],
                                   uint8_t privkey[32]);

#endif /* QUARTZ_BIP39_H */

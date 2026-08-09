#include "quartz.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "esp_cpu.h"
#include "string.h"

static const char *TAG = "QUARTZ";

/* --- CrystalHash Implementation --- */

void crystal_hash(const uint8_t *header, uint64_t nonce,
                  uint8_t out[32], uint8_t *scratchpad)
{
    /* Phase 1: Initialize scratchpad from header using AES-256-CTR
     * The header + nonce form the key/IV for filling the scratchpad.
     * This binds the scratchpad to the specific block being mined.
     */
    mbedtls_aes_context aes;
    mbedtls_aes_init(&aes);

    /* Use first 32 bytes of header as AES key, nonce+header[32:48] as IV */
    uint8_t iv[16];
    memcpy(iv, &nonce, 8);
    memcpy(iv + 8, header + 32, 8);

    uint8_t key[32];
    memcpy(key, header, 32);

    /* Generate 256KB of pseudo-random data via AES-CTR */
    memset(scratchpad, 0, QUARTZ_SCRATCHPAD_SIZE);
    mbedtls_aes_setkey_enc(&aes, key, 256);

    /* CTR mode to fill scratchpad */
    uint8_t stream_block[16];
    uint8_t ctr[16];
    memcpy(ctr, iv, 16);

    for (size_t offset = 0; offset < QUARTZ_SCRATCHPAD_SIZE; offset += 16) {
        mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_ENCRYPT, ctr, stream_block);
        memcpy(scratchpad + offset, stream_block, 16);

        /* Increment counter */
        for (int i = 15; i >= 0; i--) {
            if (++ctr[i] != 0) break;
        }
    }
    mbedtls_aes_free(&aes);

    /* Phase 2: Memory-hard mixing (64 rounds of random reads)
     * Each read pulls 32 bytes from a pseudo-random offset.
     * This is the anti-GPU/anti-ASIC core — the access pattern depends
     * on the data itself, making it latency-bound.
     */
    uint8_t state[32];
    memcpy(state, header, 32);

    uint32_t state_seed = (uint32_t)(nonce & 0xFFFFFFFF) ^ ((uint32_t)(nonce >> 32));

    for (int round = 0; round < 64; round++) {
        /* Derive a scratchpad index from current state */
        uint32_t idx = (*(uint32_t *)state ^ state_seed ^ (round * 0x9E3779B9));
        idx %= (QUARTZ_SCRATCHPAD_SIZE / 32);

        /* XOR state with scratchpad data */
        for (int i = 0; i < 32; i++) {
            state[i] ^= scratchpad[idx * 32 + i];
        }

        /* Mix with ESP32 hardware SHA for diffusion */
        uint8_t tmp[32];
        mbedtls_sha256(state, 32, tmp, 0);
        memcpy(state, tmp, 32);
    }

    /* Phase 3: PUF — incorporate flash cache timing
     * Read the cycle counter before/after a flash access.
     * This varies per-chip due to silicon and temperature.
     * Multiple samples are mixed to reduce noise.
     */
    uint32_t puf_samples[4];
    for (int i = 0; i < 4; i++) {
        volatile uint32_t *flash_ptr = (volatile uint32_t *)(0x3F400000 + (i * 0x1000));
        uint32_t t0 = esp_cpu_get_cycle_count();
        volatile uint32_t val = *flash_ptr;
        (void)val;
        uint32_t t1 = esp_cpu_get_cycle_count();
        puf_samples[i] = t1 - t0;
    }

    /* Mix PUF samples into state */
    for (int i = 0; i < 4; i++) {
        state[i % 32] ^= (puf_samples[i] & 0xFF);
    }

    /* Phase 4: Final SHA-256 via hardware accelerator */
    uint8_t final_input[40];
    memcpy(final_input, state, 32);
    memcpy(final_input + 32, &nonce, 8);

    mbedtls_sha256(final_input, 40, out, 0);
}

/* --- Difficulty checking --- */

bool quartz_check_difficulty(const uint8_t hash[32], uint32_t target_bits)
{
    uint8_t target[32];
    quartz_bits_to_target(target_bits, target);

    /* Compare hash <= target (big-endian byte comparison) */
    for (int i = 0; i < 32; i++) {
        if (hash[i] < target[i]) return true;
        if (hash[i] > target[i]) return false;
    }
    return true; /* equal */
}

void quartz_bits_to_target(uint32_t bits, uint8_t target[32])
{
    /* Bitcoin-style compact difficulty */
    memset(target, 0, 32);

    uint32_t exponent = bits >> 24;
    uint32_t mantissa = bits & 0x007FFFFF;

    if (exponent <= 3) {
        mantissa >>= (8 * (3 - exponent));
        target[28] = (mantissa >> 24) & 0xFF;
        target[29] = (mantissa >> 16) & 0xFF;
        target[30] = (mantissa >> 8) & 0xFF;
        target[31] = mantissa & 0xFF;
    } else {
        /* Shift mantissa into position */
        uint32_t pos = exponent - 3;
        if (pos < 29) {
            target[32 - exponent]     = (mantissa >> 16) & 0xFF;
            target[32 - exponent + 1] = (mantissa >> 8) & 0xFF;
            target[32 - exponent + 2] = mantissa & 0xFF;
        }
    }
}

/* --- Merkle root --- */

void quartz_merkle_root(const uint8_t (*tx_hashes)[32], size_t count,
                        uint8_t root[32])
{
    if (count == 0) {
        memset(root, 0, 32);
        return;
    }

    uint8_t (*level)[32] = malloc(count * 32);
    if (!level) {
        ESP_LOGE(TAG, "Failed to allocate merkle level");
        memset(root, 0, 32);
        return;
    }

    memcpy(level, tx_hashes, count * 32);

    while (count > 1) {
        size_t next_count = (count + 1) / 2;
        for (size_t i = 0; i < next_count; i++) {
            uint8_t concat[64];
            memcpy(concat, level[i * 2], 32);
            if (i * 2 + 1 < count) {
                memcpy(concat + 32, level[i * 2 + 1], 32);
            } else {
                memcpy(concat + 32, level[i * 2], 32); /* duplicate last */
            }
            mbedtls_sha256(concat, 64, level[i], 0);
        }
        count = next_count;
    }

    memcpy(root, level[0], 32);
    free(level);
}

/* --- Serialization --- */

void quartz_header_serialize(const quartz_header_t *hdr, uint8_t out[QUARTZ_HEADER_SIZE])
{
    memcpy(out, hdr, QUARTZ_HEADER_SIZE);
}

void quartz_header_deserialize(const uint8_t *in, quartz_header_t *hdr)
{
    memcpy(hdr, in, QUARTZ_HEADER_SIZE);
}

/* --- Miner ID --- */

void quartz_get_miner_id(uint8_t miner_id[6])
{
    esp_read_mac(miner_id, ESP_MAC_WIFI_STA);
}

/* --- Cycle counter --- */

uint32_t quartz_get_cycle_count(void)
{
    return esp_cpu_get_cycle_count();
}

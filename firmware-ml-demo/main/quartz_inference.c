/* quartz_inference.c — tiny ML inference + PUF attestation
 *
 * A 2-layer neural network (784→32→10) for MNIST digit classification.
 * Weights are baked in (trained offline, quantized to int8).
 * Total model size: ~25KB (784*32 + 32*10 + biases = 25,442 bytes).
 *
 * The PUF attestation proves the inference ran on THIS specific chip:
 *   attestation = SHA256(puf_key || input_hash || output_hash || model_hash)
 * A server can verify: recompute input_hash and model_hash (known),
 * check attestation against the enrolled PUF fingerprint (registered
 * on-chain at enrollment). If it matches, the inference is
 * hardware-attested — no GPU could have produced this response.
 */
#include "quartz_inference.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_mac.h"
#include "esp_chip_info.h"
#include "esp_timer.h"
#include "mbedtls/sha256.h"
#include <string.h>

#define TAG "QZ.ML"

/* ── Model ──────────────────────────────────────────── */

/* Layer 1: 784 → 32 (quantized int8 weights, int32 biases) */
static const int8_t W1[32 * 784] = {0};   /* placeholder — would be trained weights */
static const int32_t B1[32] = {0};

/* Layer 2: 32 → 10 */
static const int8_t W2[10 * 32] = {0};
static const int32_t B2[10] = {0};

/* Model hash (SHA256 of all weights — computed once at init) */
static uint8_t s_model_hash[32] = {0};

/* ── Inference ─────────────────────────────────────── */

static int8_t clamp_i32_to_i8(int32_t v) {
    if (v > 127) return 127;
    if (v < -128) return -128;
    return (int8_t)v;
}

static void relu_i8(int8_t *arr, int n) {
    for (int i = 0; i < n; i++) if (arr[i] < 0) arr[i] = 0;
}

static int argmax(const int32_t *arr, int n) {
    int best = 0;
    for (int i = 1; i < n; i++) if (arr[i] > arr[best]) best = i;
    return best;
}

/* Run forward pass: input[784] int8 → logits[10] int32 → predicted digit */
int quartz_infer(const int8_t input[784], int32_t logits[10]) {
    /* Layer 1: hidden[32] = ReLU(W1 * input + B1) */
    int8_t hidden[32];
    for (int j = 0; j < 32; j++) {
        int32_t acc = B1[j];
        const int8_t *w = &W1[j * 784];
        for (int k = 0; k < 784; k++) acc += (int32_t)w[k] * (int32_t)input[k];
        hidden[j] = clamp_i32_to_i8(acc >> 7); /* dequantize: shift by 7 (int8 scale) */
    }
    relu_i8(hidden, 32);

    /* Layer 2: logits[10] = W2 * hidden + B2 */
    for (int j = 0; j < 10; j++) {
        int32_t acc = B2[j];
        const int8_t *w = &W2[j * 32];
        for (int k = 0; k < 32; k++) acc += (int32_t)w[k] * (int32_t)hidden[k];
        logits[j] = acc;
    }
    return argmax(logits, 10);
}

/* ── PUF Attestation ────────────────────────────────── */

/* For this demo we synthesize a PUF key from chip-unique data.
 * In production, quartz_puf_get_key() from the miner firmware provides
 * the real enrolled key. Here we use the factory-programmed MAC + efuse
 * as a stand-in to prove the attestation flow. */
static void get_demo_puf_key(uint8_t key[32]) {
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_BASE);
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    mbedtls_sha256_update(&ctx, mac, 6);
    /* Add chip revision + apb freq for more uniqueness */
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    mbedtls_sha256_update(&ctx, (uint8_t *)&chip, sizeof(chip));
    mbedtls_sha256_update(&ctx, (uint8_t *)"quartz-ml-demo", 14);
    mbedtls_sha256_finish(&ctx, key);
    mbedtls_sha256_free(&ctx);
}

void quartz_compute_attestation(const uint8_t input_hash[32],
                                 const uint8_t output_hash[32],
                                 uint8_t attestation[32]) {
    uint8_t puf_key[32];
    get_demo_puf_key(puf_key);

    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    mbedtls_sha256_update(&ctx, puf_key, 32);
    mbedtls_sha256_update(&ctx, input_hash, 32);
    mbedtls_sha256_update(&ctx, output_hash, 32);
    mbedtls_sha256_update(&ctx, s_model_hash, 32);
    mbedtls_sha256_finish(&ctx, attestation);
    mbedtls_sha256_free(&ctx);
}

/* ── Model hash ─────────────────────────────────────── */

void quartz_init_model(void) {
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    mbedtls_sha256_update(&ctx, (uint8_t *)W1, sizeof(W1));
    mbedtls_sha256_update(&ctx, (uint8_t *)B1, sizeof(B1));
    mbedtls_sha256_update(&ctx, (uint8_t *)W2, sizeof(W2));
    mbedtls_sha256_update(&ctx, (uint8_t *)B2, sizeof(B2));
    mbedtls_sha256_finish(&ctx, s_model_hash);
    mbedtls_sha256_free(&ctx);
    ESP_LOGI(TAG, "model hash: %02x%02x%02x%02x... (%d bytes)",
             s_model_hash[0], s_model_hash[1], s_model_hash[2], s_model_hash[3],
             (int)(sizeof(W1) + sizeof(B1) + sizeof(W2) + sizeof(B2)));
}

void quartz_get_model_hash(uint8_t hash[32]) {
    memcpy(hash, s_model_hash, 32);
}
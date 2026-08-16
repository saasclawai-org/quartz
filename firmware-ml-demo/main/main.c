/* main.c — Quartz ML inference demo: "pay QZ, get a hardware-attested prediction"
 *
 * Flow:
 *   1. Boot → init model hash, print chip ID
 *   2. Generate a synthetic test input (deterministic, so we can verify)
 *   3. Run inference (digit 0-9 prediction)
 *   4. Hash input + output, compute PUF attestation
 *   5. Print the full "inference receipt": input_hash, prediction, output_hash,
 *      model_hash, attestation — everything a verifier needs
 *   6. Repeat with a different input every 5 seconds (simulating paid requests)
 *
 * In production: step 6 would be an HTTP endpoint receiving input + payment,
 * and the receipt would be returned to the paying client.
 */
#include "quartz_inference.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_mac.h"
#include "esp_chip_info.h"
#include "esp_timer.h"
#include "mbedtls/sha256.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>
#include <stdlib.h>

#define TAG "QZ.DEMO"

static void hash_bytes(const uint8_t *data, size_t len, uint8_t out[32]) {
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    mbedtls_sha256_update(&ctx, data, len);
    mbedtls_sha256_finish(&ctx, out);
    mbedtls_sha256_free(&ctx);
}

static void hex32(const uint8_t h[32], char *out) {
    for (int i = 0; i < 32; i++) sprintf(out + i * 2, "%02x", h[i]);
    out[64] = '\0';
}

/* Generate a deterministic synthetic "image" based on seed.
 * In production this would be real sensor data or a client-provided image. */
static void gen_test_input(uint32_t seed, int8_t input[784]) {
    /* Simple LCG for reproducible pseudo-random input */
    uint32_t state = seed;
    for (int i = 0; i < 784; i++) {
        state = state * 1103515245 + 12345;
        input[i] = (int8_t)((state >> 16) & 0xFF);
    }
}

void app_main(void) {
    uint8_t mac[6];
    esp_efuse_mac_get_default(mac);
    ESP_LOGI(TAG, "╔══════════════════════════════════════════╗");
    ESP_LOGI(TAG, "║  Quartz ML Inference Demo (PUF-attested) ║");
    ESP_LOGI(TAG, "╚══════════════════════════════════════════╝");
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    ESP_LOGI(TAG, "chip: %02x:%02x:%02x:%02x:%02x:%02x rev=%d core=%d",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
             chip.revision, chip.cores);
    ESP_LOGI(TAG, "model: 784→32→10 int8 MNIST-style classifier (~25KB)");

    quartz_init_model();

    uint8_t model_hash[32];
    quartz_get_model_hash(model_hash);
    char mh[65];
    hex32(model_hash, mh);
    ESP_LOGI(TAG, "model_hash: %s", mh);

    /* Simulation: 5 paid inference requests */
    for (int req = 1; req <= 999; req++) {
        uint32_t seed = req * 42 + 17;
        int8_t input[784];
        gen_test_input(seed, input);

        /* ── Inference ── */
        int64_t t0 = esp_timer_get_time();
        int32_t logits[10];
        int prediction = quartz_infer(input, logits);
        int64_t t1 = esp_timer_get_time();

        /* ── Hash input + output ── */
        uint8_t input_hash[32], output_hash[32];
        hash_bytes((uint8_t *)input, 784, input_hash);
        hash_bytes((uint8_t *)logits, sizeof(logits), output_hash);

        /* ── PUF attestation ── */
        uint8_t attestation[32];
        quartz_compute_attestation(input_hash, output_hash, attestation);

        /* ── Print receipt ── */
        char ih[65], oh[65], att[65];
        hex32(input_hash, ih);
        hex32(output_hash, oh);
        hex32(attestation, att);

        ESP_LOGI(TAG, "┌─── inference #%d ───────────────────────", req);
        ESP_LOGI(TAG, "│ prediction: %d  (%.2f ms)", prediction, (t1 - t0) / 1000.0);
        ESP_LOGI(TAG, "│ logits: %ld %ld %ld %ld %ld %ld %ld %ld %ld %ld",
                 (long)logits[0], (long)logits[1], (long)logits[2], (long)logits[3],
                 (long)logits[4], (long)logits[5], (long)logits[6], (long)logits[7],
                 (long)logits[8], (long)logits[9]);
        ESP_LOGI(TAG, "│ input_hash:  %s", ih);
        ESP_LOGI(TAG, "│ output_hash: %s", oh);
        ESP_LOGI(TAG, "│ model_hash:  %s", mh);
        ESP_LOGI(TAG, "│ ★ attestation: %s", att);
        ESP_LOGI(TAG, "└────────────────────────────────────────");
        ESP_LOGI(TAG, "verify: SHA256(puf_key || input_hash || output_hash || model_hash) == attestation");
        ESP_LOGI(TAG, "  → proves this inference ran on THIS chip (not a GPU, not a server)");
        ESP_LOGI(TAG, "");

        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}
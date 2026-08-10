/**
 * main.c — Quartz ESP32-S3 Miner (Headless Build)
 *
 * Minimal main for first flashable binary.
 * Mining loop + WiFi connection to testnet node.
 * Display/button support added per-board in full build.
 */

#include "quartz.h"
#include "quartz_attest.h"
#include "quartz_pool.h"
#include "quartz_entropy.h"
#include "quartz_supply_chain.h"
#include <string.h>
#include <stdio.h>

#ifdef ESP_PLATFORM
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "esp_mac.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "lwip/sockets.h"
#else
#define ESP_LOGI(tag, fmt, ...)
#define ESP_LOGE(tag, fmt, ...)
#define ESP_LOGW(tag, fmt, ...)
#define vTaskDelay(x)
#define portTICK_PERIOD_MS 1
#endif

static const char *TAG = "MAIN";

/* === Testnet Configuration === */
#define QUARTZ_NODE_HOST "167.233.19.85"
#define QUARTZ_NODE_PORT 21100

/* === Mining State === */
static volatile bool s_mining = false;
/* mining stats now in quartz.c */
static uint32_t s_start_time = 0;
static uint32_t s_hash_count = 0;
static uint32_t s_blocks_found = 0;

#ifdef ESP_PLATFORM
/* === NVS Init === */
static void init_nvs(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }
}

/* === WiFi Init (required for hardware RNG entropy) === */
static void event_handler(void *arg, esp_event_base_t base,
                          int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        esp_wifi_connect();
        ESP_LOGW(TAG, "WiFi disconnected, retrying...");
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ESP_LOGI(TAG, "WiFi connected — IP assigned");
    }
}

static void init_wifi(void) {
    /* Initialize WiFi in station mode for entropy (radio noise for RNG).
     * Even without a valid AP, the radio subsystem seeds the hardware RNG. */
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);

    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL);

    /* TODO: WiFi credentials via BLE provisioning or smart config.
     * For now, just start the radio for RNG entropy. */
    wifi_config_t wifi_config = {0};
    /* Leave SSID empty — radio still initializes and provides entropy */
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    esp_wifi_start();

    ESP_LOGI(TAG, "WiFi radio started (entropy source active)");
}

/* === Mining Loop === */
static void mining_task(void *pvParameters) {
    ESP_LOGI(TAG, "========================================");
    ESP_LOGI(TAG, "  Quartz (QZ) — ESP32 Cryptocurrency Miner");
    ESP_LOGI(TAG, "  Version: %d", QUARTZ_VERSION);
    ESP_LOGI(TAG, "  Target: ESP32-S3 (generic)");
    ESP_LOGI(TAG, "  Node: %s:%d", QUARTZ_NODE_HOST, QUARTZ_NODE_PORT);
    ESP_LOGI(TAG, "========================================");

    /* Initialize entropy subsystem */
    ESP_LOGI(TAG, "Waiting for hardware RNG...");
    qz_err_t err = quartz_entropy_wait_for_ready();
    if (err != QZ_OK) {
        ESP_LOGE(TAG, "RNG not ready — cannot mine safely");
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "Hardware RNG ready (%.2f bits/byte entropy)",
             quartz_entropy_last_estimate());

    /* Initialize supply chain (birth certificate) */
    qz_birth_certificate_t cert;
    err = quartz_supply_chain_init(&cert);
    if (err != QZ_OK) {
        ESP_LOGE(TAG, "Supply chain init failed (code %d)", err);
        ESP_LOGE(TAG, "Device may be tampered — refusing to mine");
        vTaskDelete(NULL);
        return;
    }

    /* Show device identity */
    char short_hash[9];
    quartz_cert_short_hash(&cert, short_hash);
    ESP_LOGI(TAG, "Device: %02x:%02x:%02x:%02x:%02x:%02x  Key: %s",
             cert.chip_id[0], cert.chip_id[1], cert.chip_id[2],
             cert.chip_id[3], cert.chip_id[4], cert.chip_id[5],
             short_hash);

    /* Initialize pool (solo mode by default) */
    quartz_pool_init();
    ESP_LOGI(TAG, "Mining mode: SOLO");

    /* Allocate scratchpad in PSRAM */
    ESP_LOGI(TAG, "Allocating 256KB scratchpad...");
    uint8_t *scratchpad = heap_caps_malloc(QUARTZ_SCRATCHPAD_SIZE, MALLOC_CAP_SPIRAM);
    if (!scratchpad) {
        ESP_LOGE(TAG, "Failed to allocate scratchpad — no PSRAM?");
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "Scratchpad allocated (%d KB)", QUARTZ_SCRATCHPAD_SIZE / 1024);

    /* Mining loop */
    s_mining = true;
    s_start_time = esp_timer_get_time() / 1000000;
    s_hash_count = 0;

    ESP_LOGI(TAG, "⚡ Mining started!");

    /* Dummy header for now — real implementation fetches from node */
    uint8_t header[80] = {0};
    uint64_t nonce = 0;
    uint8_t hash[32];

    while (s_mining) {
        crystal_hash_v2(header, nonce, hash, scratchpad, true);

        s_hash_count++;

        /* Log hashrate every 1000 hashes */
        if (s_hash_count % 1000 == 0) {
            uint32_t uptime = (esp_timer_get_time() / 1000000) - s_start_time;
            uint32_t hps = (uptime > 0) ? (s_hash_count / uptime) : 0;
            ESP_LOGI(TAG, "Mining... %lu H/s, %lu total, nonce %llu",
                     hps, s_hash_count, nonce);
        }

        nonce++;

        /* Yield to other tasks (BLE, WiFi, LoRa) */
        if (s_hash_count % 100 == 0) {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }

    free(scratchpad);
    vTaskDelete(NULL);
}
#endif /* ESP_PLATFORM */

/* === Main Entry Point === */
#ifdef ESP_PLATFORM
void app_main(void) {
    ESP_LOGI(TAG, "Quartz ESP32 Miner starting...");

    /* Initialize NVS */
    init_nvs();

    /* Initialize WiFi (required for RNG entropy) */
    init_wifi();

    /* Wait a moment for radio to warm up */
    vTaskDelay(pdMS_TO_TICKS(2000));

    /* Start mining task on Core 1 (Core 0 handles WiFi/BLE) */
    xTaskCreatePinnedToCore(
        mining_task,
        "quartz_miner",
        32768,       /* 32KB stack */
        NULL,
        5,           /* Priority */
        NULL,
        1            /* Core 1 */
    );
}
#else
int main(void) {
    printf("This firmware must be built with ESP-IDF for ESP32-S3.\n");
    return 0;
}
#endif

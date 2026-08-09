/**
 * Quartz Main Application — T-Display S3
 *
 * State machine:
 *   1. Boot → show logo
 *   2. First run → generate keys → show seed on screen → confirm
 *   3. Normal → mining screen (stats updated every 2s)
 *   4. BLE signing → show tx details → button confirms → sign → return to mining
 *   5. Button short press → cycle: mining → address → mining
 *   6. Button long hold (3s) → wipe confirmation → wipe → reboot
 */

#include "quartz.h"
#include "quartz_wallet.h"
#include "quartz_display.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "nvs_flash.h"
#include <string.h>

static const char *TAG = "QUARTZ_MAIN";

// BLE signing request (populated by BLE task)
typedef struct {
    char to_address[40];
    uint64_t amount_sats;
    uint64_t fee_sats;
    uint8_t tx_hash[32];
    bool pending;
} sign_request_t;

static sign_request_t s_sign_req = {0};
static QueueHandle_t s_sign_queue = NULL;

// ============================================================
// BLE Callback — Signing Request Received
// ============================================================

// Called from BLE GATT handler when phone writes to SIGN characteristic
void on_ble_sign_request(const uint8_t *tx_hash, size_t len) {
    if (len != 32) {
        ESP_LOGW(TAG, "Invalid tx hash length: %d", len);
        return;
    }

    // Parse tx details from the hash payload (phone sends prefix before hash)
    // In production: phone sends CBOR-encoded tx details, ESP32 parses + hashes
    // For now: queue the hash for the main UI to confirm
    memcpy(s_sign_req.tx_hash, tx_hash, 32);
    s_sign_req.pending = true;

    // Send to main task for user confirmation
    if (s_sign_queue) {
        xQueueSend(s_sign_queue, &s_sign_req, 0);
    }
}

// ============================================================
// Seed Backup Flow (On-Device)
// ============================================================

static void show_seed_flow(void) {
    char words[12][12];
    quartz_wallet_err_t err = quartz_wallet_get_seed_phrase_for_backup(words, 12);
    if (err != QZ_WALLET_OK) {
        ESP_LOGE(TAG, "Failed to get seed phrase: %d", err);
        return;
    }

    // Show 3 pages, 4 words each
    for (int page = 0; page < 3; page++) {
        quartz_display_show_seed(words, page);

        // Wait for button press to advance
        while (true) {
            quartz_button_t btn = quartz_button_poll();
            if (btn == QZ_BTN_SHORT) break;  // next page
            if (btn == QZ_BTN_LONG) goto done;  // user finished
            vTaskDelay(pdMS_TO_TICKS(20));
        }
    }

done:
    // Wipe seed from RAM immediately
    quartz_wallet_wipe_seed_phrase(words);
    ESP_LOGI(TAG, "Seed backup complete — wiped from RAM");

    // Show confirmation
    quartz_display_show_boot();
    vTaskDelay(pdMS_TO_TICKS(1500));
}

// ============================================================
// Signing Confirmation Flow (On-Device)
// ============================================================

static void handle_sign_request(const sign_request_t *req) {
    // Show transaction details on device screen
    quartz_display_show_sign_request(
        req->to_address,
        req->amount_sats,
        req->fee_sats
    );

    // Wait for user decision
    uint32_t start = millis();
    bool approved = false;
    bool decided = false;

    while (!decided) {
        quartz_button_t btn = quartz_button_poll();

        if (btn == QZ_BTN_SHORT) {
            approved = true;
            decided = true;
        } else if (btn == QZ_BTN_LONG) {
            approved = false;
            decided = true;
        }

        // Auto-reject after 60 seconds
        if (millis() - start > 60000) {
            approved = false;
            decided = true;
        }

        vTaskDelay(pdMS_TO_TICKS(20));
    }

    if (approved) {
        // Show "SIGNING..." state
        quartz_display_show_signing_result(true);

        // Sign on-device — private key never leaves ESP32
        uint8_t signature[64];
        quartz_wallet_sign(req->tx_hash, 32, signature);

        // Send signature back via BLE notify
        // (BLE task picks this up)
        on_ble_signature_ready(signature, 64);

        ESP_LOGI(TAG, "Transaction approved and signed on-device");

        // Show success briefly
        vTaskDelay(pdMS_TO_TICKS(2000));
    } else {
        quartz_display_show_signing_result(false);
        // Notify phone of rejection
        on_ble_signature_rejected();
        ESP_LOGI(TAG, "Signing request rejected by user");
        vTaskDelay(pdMS_TO_TICKS(1500));
    }
}

// ============================================================
// Main State Machine
// ============================================================

void app_main(void) {
    ESP_LOGI(TAG, "Quartz ESP32 Miner starting...");
    ESP_LOGI(TAG "Target: LilyGO T-Display S3 (ESP32-S3, 8MB PSRAM)");

    // --- NVS Init ---
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    // --- Display Init ---
    quartz_display_init();
    quartz_display_show_boot();
    vTaskDelay(pdMS_TO_TICKS(2000));

    // --- Wallet Init ---
    quartz_wallet_err_t wallet_err = quartz_wallet_load();
    bool first_run = false;

    if (wallet_err == QZ_WALLET_ERR_NOT_FOUND) {
        ESP_LOGI(TAG, "No wallet found — generating new keys on-device");
        quartz_display_show_boot();
        tft_fillRect(0, 60, SCREEN_W, 20, COLOR_BG);
        // Show "Generating keys..." on screen
        quartz_wallet_generate(true);  // testnet for now
        first_run = true;
    }

    // --- First-run seed backup ---
    if (first_run) {
        show_seed_flow();
    }

    // --- BLE Init ---
    quartz_ble_init();
    s_sign_queue = xQueueCreate(4, sizeof(sign_request_t));

    // --- WiFi Init (for P2P networking) ---
    quartz_wifi_init();

    // --- Start Mining Task ---
    quartz_mining_start();

    // --- Main UI Loop ---
    bool showing_address = false;
    sign_request_t received_req;

    while (1) {
        // Check for BLE signing requests
        if (xQueueReceive(s_sign_queue, &received_req, pdMS_TO_TICKS(100)) == pdTRUE) {
            // Pause mining display, handle signing
            handle_sign_request(&received_req);
            showing_address = false;
        }

        // Handle button presses
        quartz_button_t btn = quartz_button_poll();
        if (btn == QZ_BTN_SHORT) {
            // Toggle between mining and address display
            if (showing_address) {
                showing_address = false;
            } else {
                const char *addr = quartz_wallet_get_address();
                if (addr) {
                    quartz_display_show_address(addr);
                    showing_address = true;
                }
            }
        } else if (btn == QZ_BTN_LONG && current_screen != QZ_SCREEN_SIGN) {
            // Wipe confirmation
            quartz_display_show_wiped();
            vTaskDelay(pdMS_TO_TICKS(500));
            // Require second long press to confirm
            quartz_button_t btn2 = quartz_button_poll();
            uint32_t hold_start = millis();
            while (digitalRead(BUTTON_GPIO) == LOW && millis() - hold_start < 3000) {
                vTaskDelay(pdMS_TO_TICKS(50));
            }
            if (millis() - hold_start >= 3000) {
                // Confirmed — wipe everything
                quartz_wallet_wipe();
                quartz_mining_stop();
                vTaskDelay(pdMS_TO_TICKS(2000));
                esp_restart();
            }
            // Not confirmed — go back
        }

        // Update mining stats display (every 2 seconds)
        if (!showing_address && current_screen != QZ_SCREEN_SIGN) {
            static uint32_t last_update = 0;
            if (millis() - last_update > 2000) {
                last_update = millis();

                quartz_mining_stats_t stats;
                quartz_mining_get_stats(&stats);

                quartz_display_show_mining(
                    stats.hashrate,
                    stats.blocks_found,
                    stats.uptime_sec,
                    stats.temp_c
                );
            }
        }

        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

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
#include "quartz_wallet.h"
#include "quartz_display.h"
#include "quartz_wifi.h"
#include "quartz_puf.h"
#include "quartz_pay.h"
#include "quartz_agent.h"
#include "quartz_ble.h"
#include "quartz_qr.h"
#include <string.h>
#include <stdio.h>
#include <strings.h>
#include <ctype.h>
#include <unistd.h>

#ifdef ESP_PLATFORM
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "esp_mac.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/adc.h"
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

/* Runtime scratchpad size — set during init based on available RAM */
int g_scratchpad_size = QUARTZ_SCRATCHPAD_SIZE;

static const char *TAG = "MAIN";
uint32_t g_last_hps = 0;  /* current hashrate, read by mining_submit */

/* === Testnet Configuration === */
#define QUARTZ_NODE_HOST "167.233.19.85"
#define QUARTZ_NODE_PORT 21100

/* === M5Stack Core Buttons === */
#define BTN_A_PIN   39   /* Left button (SENSOR_VN) */
#define BTN_B_PIN   38   /* Middle button */
#define BTN_C_PIN   37   /* Right button */

/* Button state */
static bool btn_a_pressed = false;
static bool btn_b_pressed = false;
static bool btn_c_pressed = false;
static float s_payment_amount = 0.1f;  /* default QR amount */
static uint32_t btn_last_read_sec = 0;
static uint32_t btn_debounce_count = 0;
static int64_t btn_a_low_since_us = 0;  /* timestamp when A first read low */
static int64_t btn_b_low_since_us = 0;
static int64_t btn_c_low_since_us = 0;

#define BTN_DEBOUNCE_US       200000  /* 200ms debounce */
#define BTN_COOLDOWN_SEC       1       /* min 1s between button actions */
#define BTN_STARTUP_GRACE_SEC  10      /* ignore button presses in first 10s after boot */
#define BTN_FLOAT_CHECK_COUNT  5       /* samples for floating-pin detection */
#define BTN_FLOAT_CHECK_US     1000    /* 1ms between samples */

static void init_buttons(void) {
    /* GPIO39 (BTN_A) is shared with light sensor ADC1_CH3.
     * GPIO38/37 are ADC1_CH2/CH1 but work as digital (agent doesn't
     * reconfigure them). We'll read BTN_A via ADC instead. */
    gpio_config_t btn_conf = {
        .pin_bit_mask = (1ULL << BTN_B_PIN) | (1ULL << BTN_C_PIN),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&btn_conf);
    /* GPIO39 is configured as ADC by quartz_agent_init — no digital config needed */
}

/* Distinguish a real button press from a floating pin.
 * For GPIO38/37: digital read with multi-sample.
 * For GPIO39: ADC read (pin is in ADC mode for light sensor).
 * When button pressed → GND → ADC ~0. When unpressed → floats higher. */
static bool btn_is_pressed_digital(int pin) {
    for (int i = 0; i < BTN_FLOAT_CHECK_COUNT; i++) {
        if (gpio_get_level(pin) != 0) return false;
        ets_delay_us(BTN_FLOAT_CHECK_US);
    }
    return true;
}

static bool btn_a_is_pressed(void) {
    /* GPIO39 is ADC1_CHANNEL_3. Read ADC: 0 = pressed (GND), >200 = not pressed */
    int val = adc1_get_raw(ADC1_CHANNEL_3);
    return (val >= 0 && val < 100);  /* threshold: pressed = near 0V */
}

static void poll_buttons(void) {
    uint32_t now = esp_timer_get_time() / 1000000;

    /* Ignore buttons during startup grace period */
    if (now < BTN_STARTUP_GRACE_SEC) return;

    /* Cooldown check (applies to all buttons) */
    if (btn_last_read_sec > 0 && (now - btn_last_read_sec) < BTN_COOLDOWN_SEC) return;

    int64_t now_us = esp_timer_get_time();

    /* Read button states.
     * A: via ADC (GPIO39 is in ADC mode for light sensor)
     * B/C: via digital GPIO (work fine) */
    bool a_raw = btn_a_is_pressed();
    bool b_raw = (gpio_get_level(BTN_B_PIN) == 0);
    bool c_raw = (gpio_get_level(BTN_C_PIN) == 0);

    /* Track when each button first goes active */
    if (a_raw) { if (btn_a_low_since_us == 0) btn_a_low_since_us = now_us; }
    else { btn_a_low_since_us = 0; btn_a_pressed = false; }

    if (b_raw) { if (btn_b_low_since_us == 0) btn_b_low_since_us = now_us; }
    else { btn_b_low_since_us = 0; btn_b_pressed = false; }

    if (c_raw) { if (btn_c_low_since_us == 0) btn_c_low_since_us = now_us; }
    else { btn_c_low_since_us = 0; btn_c_pressed = false; }

    /* BTN A: toggle mining <-> payment screen */
    if (a_raw && !btn_a_pressed && btn_a_low_since_us > 0 &&
        (now_us - btn_a_low_since_us) >= BTN_DEBOUNCE_US) {
        btn_a_pressed = true;
        btn_last_read_sec = now;
        if (quartz_display_get_screen() == QZ_SCREEN_PAYMENT) {
            quartz_display_set_screen(QZ_SCREEN_MINING);
        } else {
            quartz_display_set_screen(QZ_SCREEN_PAYMENT);
            quartz_display_qr_payment(quartz_wallet_get_address(), s_payment_amount);
        }
        return;
    }

    /* BTN B: increase payment amount */
    if (b_raw && !btn_b_pressed && btn_b_low_since_us > 0 &&
        (now_us - btn_b_low_since_us) >= BTN_DEBOUNCE_US) {
        if (!btn_is_pressed_digital(BTN_B_PIN)) {
            btn_b_low_since_us = 0;
            return;
        }
        btn_b_pressed = true;
        btn_last_read_sec = now;
        if (quartz_display_get_screen() == QZ_SCREEN_PAYMENT) {
            s_payment_amount += 0.1f;
            quartz_display_qr_payment(quartz_wallet_get_address(), s_payment_amount);
        }
        return;
    }

    /* BTN C: decrease payment amount */
    if (c_raw && !btn_c_pressed && btn_c_low_since_us > 0 &&
        (now_us - btn_c_low_since_us) >= BTN_DEBOUNCE_US) {
        if (!btn_is_pressed_digital(BTN_C_PIN)) {
            btn_c_low_since_us = 0;
            return;
        }
        btn_c_pressed = true;
        btn_last_read_sec = now;
        if (quartz_display_get_screen() == QZ_SCREEN_PAYMENT) {
            if (s_payment_amount > 0.1f) s_payment_amount -= 0.1f;
            quartz_display_qr_payment(quartz_wallet_get_address(), s_payment_amount);
        }
        return;
    }
}

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

/* === Mining Task === */
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

    /* Initialize wallet */
    quartz_wallet_err_t werr = quartz_wallet_load();
    bool seed_needs_display = false;

    if (werr == QZ_WALLET_ERR_NOT_FOUND) {
        ESP_LOGI(TAG, "========================================");
        ESP_LOGI(TAG, "  Creating new Quartz wallet...");
        ESP_LOGI(TAG, "========================================");

        werr = quartz_wallet_generate(false);  /* mainnet */
        if (werr != QZ_WALLET_OK) {
            ESP_LOGE(TAG, "Wallet generation failed (code %d)", werr);
            vTaskDelete(NULL);
            return;
        }
        seed_needs_display = true;
    } else if (werr == QZ_WALLET_OK) {
        /* Wallet exists — check if seed was ever confirmed */
        if (!quartz_wallet_is_backup_confirmed()) {
            ESP_LOGW(TAG, "⚠️ Wallet exists but seed phrase was NEVER confirmed!");
            ESP_LOGW(TAG, "⚠️ Re-displaying seed for backup...");
            seed_needs_display = true;
        } else {
            ESP_LOGI(TAG, "Wallet loaded: %s", quartz_wallet_get_address());
        }
    } else {
        ESP_LOGE(TAG, "Wallet load failed (code %d)", werr);
        quartz_display_error("Wallet load failed");
    }

    if (seed_needs_display) {
        char words[12][12];
        werr = quartz_wallet_get_seed_phrase_for_backup(words, 12);
        if (werr == QZ_WALLET_OK) {
            /* Build seed QR payload: JSON with words + address */
            char qr_payload[256];
            int qlen = snprintf(qr_payload, sizeof(qr_payload),
                "{\"v\":1,\"words\":[");
            for (int i = 0; i < 12; i++) {
                qlen += snprintf(qr_payload + qlen, sizeof(qr_payload) - qlen,
                    "\"%s\"%s", words[i], (i < 11) ? "," : "");
            }
            qlen += snprintf(qr_payload + qlen, sizeof(qr_payload) - qlen,
                "],\"addr\":\"%s\"}", quartz_wallet_get_address());

            /* === SECURE CHANNELS ONLY === */
            /* NO captive portal — seed never goes over WiFi */
            /* NO unbonded BLE — seed char only readable after pairing */

            /* Channel 1: Serial QR code (scan with phone app camera) */
            ESP_LOGI(TAG, "");
            ESP_LOGI(TAG, "╔══════════════════════════════════════╗");
            ESP_LOGI(TAG, "║   🔮 QUARTZ WALLET — SEED QR CODE    ║");
            ESP_LOGI(TAG, "║   Scan with Quartz app camera        ║");
            ESP_LOGI(TAG, "╚══════════════════════════════════════╝");
            ESP_LOGI(TAG, "");
            ESP_LOGI(TAG, "Address: %s", quartz_wallet_get_address());
            ESP_LOGI(TAG, "");
            ESP_LOGI(TAG, "Open the Quartz app → Scan Seed QR → point camera at the QR below:");
            ESP_LOGI(TAG, "");

            /* Output QR code as ASCII art on serial terminal */
            quartz_qr_serial(qr_payload, QR_ECC_HIGH);

            ESP_LOGI(TAG, "");
            ESP_LOGI(TAG, "Or scan the QR on the device screen with your phone camera.");
            ESP_LOGI(TAG, "After writing down your seed, confirm in the app or type 'confirm' + Enter.");
            ESP_LOGI(TAG, "");

            /* Channel 2: Display QR code (M5Stack with screen) */
#ifdef QUARTZ_HAS_DISPLAY
            /* Show QR on display — phone scans directly */
            quartz_display_clear(0xFFFF);  /* white background */
            quartz_qr_display(qr_payload, QR_ECC_HIGH,
                             (320 - 200) / 2, 30, 3,
                             0x0000, 0xFFFF);  /* black on white */
            quartz_display_draw_text(40, 250, "Scan with Quartz app", 0x0000, 0xFFFF);
#endif

            /* Channel 3: BLE (bonded only — app must pair first) */
            /* Seed characteristic returns empty unless bonded */
            quartz_ble_set_seed_phrase((const char (*)[12])words);

            ESP_LOGI(TAG, "Waiting for confirmation...");
            ESP_LOGI(TAG, "  - Quartz app (BLE): pair device, then confirm in app");
            ESP_LOGI(TAG, "  - Serial: type 'confirm' + Enter");

            /* Serial confirmation input state */
            char serial_buf[32] = {0};
            int serial_pos = 0;
            bool serial_confirmed = false;

            /* Wait for confirmation from ANY source — NO TIMEOUT */
            while (!quartz_ble_is_seed_confirmed() &&
                   !serial_confirmed) {
                /* Check serial input via non-blocking read */
                char ch;
                int n = read(STDIN_FILENO, &ch, 1);
                if (n == 1) {
                    if (ch == '\n' || ch == '\r') {
                        serial_buf[serial_pos] = '\0';
                        if (strcasecmp(serial_buf, "confirm") == 0) {
                            serial_confirmed = true;
                            ESP_LOGI(TAG, "✅ Seed confirmed via serial input");
                        }
                        serial_pos = 0;
                        serial_buf[0] = '\0';
                    } else if (serial_pos < (int)sizeof(serial_buf) - 1) {
                        serial_buf[serial_pos++] = ch;
                    }
                }
                vTaskDelay(pdMS_TO_TICKS(50));
            }

            /* Confirm in NVS so we never show seed again */
            quartz_wallet_confirm_backup();

            if (quartz_ble_is_seed_confirmed()) {
                ESP_LOGI(TAG, "✅ Seed confirmed via BLE app");
            } else if (serial_confirmed) {
                ESP_LOGI(TAG, "✅ Seed confirmed via serial");
            }

            /* Wipe seed phrase from RAM */
            quartz_wallet_wipe_seed_phrase(words);
#ifdef QUARTZ_HAS_DISPLAY
            quartz_display_clear(QZ_COLOR_BLACK);
#endif
        }
    }

    /* Initialize PUF (hardware binding) — NO FALLBACK.
     * If PUF fails, device refuses to mine. Period. */
    int puf_rc = quartz_puf_init();
    if (puf_rc != 0) {
        ESP_LOGE(TAG, "PUF initialization FAILED — refusing to mine.");
        ESP_LOGE(TAG, "Device halting. Power-cycle to retry (unplug, wait 5s, replug).");
        /* Display error on screen if available */
        quartz_display_clear(0x0843);
        quartz_display_fill_rect(0, 0, 320, 22, 0xF800);  /* red header */
        quartz_display_draw_text(8, 3, "PUF FAILED - HALTED", 0xFFFF, 0xF800);
        quartz_display_draw_text(8, 40, "Hardware binding required.", 0xFFFF, 0x0843);
        quartz_display_draw_text(8, 60, "No fallback key.", 0xF800, 0x0843);
        quartz_display_draw_text(8, 90, "Power-cycle to retry:", 0x8888, 0x0843);
        quartz_display_draw_text(8, 110, "1. Unplug USB", 0xFFFF, 0x0843);
        quartz_display_draw_text(8, 130, "2. Wait 5 seconds", 0xFFFF, 0x0843);
        quartz_display_draw_text(8, 150, "3. Replug", 0xFFFF, 0x0843);
        /* Infinite loop — device is bricked until power-cycle */
        while (1) {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }
    ESP_LOGI(TAG, "PUF fingerprint: %s", quartz_puf_get_fingerprint());

    /* Initialize pool (solo mode by default) */
    quartz_pool_init();
    ESP_LOGI(TAG, "Mining mode: SOLO");

    /* Initialize autonomous agent */
    quartz_agent_init(quartz_wallet_get_address());
    ESP_LOGI(TAG, "Device agent ready — autonomous rules active");

    /* Allocate scratchpad — try PSRAM first, fall back to internal RAM */
    ESP_LOGI(TAG, "Allocating scratchpad...");
    uint8_t *scratchpad = heap_caps_malloc(QUARTZ_SCRATCHPAD_SIZE, MALLOC_CAP_SPIRAM);
    int scratchpad_size = QUARTZ_SCRATCHPAD_SIZE;
    if (!scratchpad) {
        ESP_LOGW(TAG, "No PSRAM — using 32KB internal RAM scratchpad");
        scratchpad = heap_caps_malloc(QUARTZ_SCRATCHPAD_SIZE_LITE, MALLOC_CAP_8BIT);
        scratchpad_size = QUARTZ_SCRATCHPAD_SIZE_LITE;
    }
    if (!scratchpad) {
        ESP_LOGE(TAG, "Failed to allocate scratchpad — out of memory");
        vTaskDelete(NULL);
        return;
    }
    g_scratchpad_size = scratchpad_size;
    ESP_LOGI(TAG, "Scratchpad allocated (%d KB)", scratchpad_size / 1024);

    /* Initialize BLE GATT server for phone app (after scratchpad) */
    quartz_ble_set_address(quartz_wallet_get_address());
    quartz_ble_init();
    ESP_LOGI(TAG, "BLE ready — pair as \"Quartz-Miner\"");

    /* Mining loop */
    s_mining = true;
    s_start_time = esp_timer_get_time() / 1000000;
    s_hash_count = 0;

    ESP_LOGI(TAG, "Mining started!");

    /* Draw initial mining screen immediately */
#ifdef QUARTZ_HAS_DISPLAY
    quartz_display_mining_stats(0, 0, 0, 0, quartz_wallet_get_address());
#endif

    uint8_t header[80] = {0};
    uint64_t nonce = 0;
    uint8_t hash[32];

    /* Try to fetch work from node */
    qz_block_template_t tmpl;
    bool have_work = false;
    uint32_t last_work_fetch = 0;

    while (s_mining) {
        /* Fetch new work every 30 seconds or on first iteration */
        uint32_t now = esp_timer_get_time() / 1000000;
        if (quartz_wifi_is_connected() && (!have_work || (now - last_work_fetch) > 30)) {
            int rc = quartz_mining_get_work(&tmpl);
            if (rc == 0) {
                memcpy(header, tmpl.header, 80);
                nonce = 0;
                have_work = true;
                last_work_fetch = now;
                ESP_LOGI(TAG, "📡 Got work: block %d, target %d",
                         tmpl.height, tmpl.target_bits);
            } else {
                /* Fallback to local mining if node unreachable */
                if (!have_work) {
                    ESP_LOGW(TAG, "Node unreachable, mining locally");
                }
            }
        }

        crystal_hash_v2(header, nonce, hash, scratchpad, true);

        s_hash_count++;

        /* Check difficulty target (simple: top N bits zero) */
        if (have_work) {
            /* Check if hash meets target */
            uint32_t target = tmpl.target_bits;
            if (target > 0 && target <= 32) {
                uint32_t *hash_words = (uint32_t *)hash;
                /* Check if top 'target' bits are zero */
                bool meets = true;
                int bits_checked = 0;
                for (int w = 0; w < 8 && bits_checked < (int)target; w++) {
                    uint32_t word = __builtin_bswap32(hash_words[w]);
                    int bits_in_word = target - bits_checked;
                    if (bits_in_word > 32) bits_in_word = 32;
                    uint32_t mask = 0xFFFFFFFF << (32 - bits_in_word);
                    if (word & mask) { meets = false; break; }
                    bits_checked += bits_in_word;
                }
                if (meets) {
                    ESP_LOGI(TAG, "BLOCK FOUND! Nonce %llu", nonce);
                    s_blocks_found++;

                    /* Generate PUF attestation for this block */
                    uint8_t puf_resp[32];
                    quartz_puf_mining_response(header, nonce, puf_resp);
                    ESP_LOGI(TAG, "PUF attestation: %02x%02x%02x%02x...",
                             puf_resp[0], puf_resp[1], puf_resp[2], puf_resp[3]);

                    /* Submit to node */
                    if (quartz_wifi_is_connected()) {
                        int rc = quartz_mining_submit(tmpl.job_id, nonce, header);
                        if (rc == 0) {
                            ESP_LOGI(TAG, "Block submitted and accepted!");
                        } else {
                            ESP_LOGW(TAG, "Block submit failed (%d)", rc);
                        }
                    }

                    /* Fetch new work immediately */
                    have_work = false;
                }
            }
        }

        /* Update display (only on mining screen) */
        if (s_hash_count % 56 == 0) {
            uint32_t uptime = (esp_timer_get_time() / 1000000) - s_start_time;
            uint32_t hps = (uptime > 0) ? (s_hash_count / uptime) : 0;
            g_last_hps = hps;
            ESP_LOGI(TAG, "Mining... %lu H/s, %lu total, nonce %llu",
                     hps, s_hash_count, nonce);

#ifdef QUARTZ_HAS_DISPLAY
            if (quartz_display_get_screen() == QZ_SCREEN_MINING) {
                quartz_display_mining_stats(
                    s_hash_count,
                    hps,
                    s_blocks_found,
                    uptime,
                    quartz_wallet_get_address()
                );
            }
#endif

            /* Update BLE stats for phone app */
            quartz_ble_update_stats(s_hash_count, hps, s_blocks_found, uptime);

            /* Poll for messages every 10 seconds */
            static uint32_t last_msg_check = 0;
            if (quartz_wifi_is_connected() && (uptime - last_msg_check) > 10) {
                last_msg_check = uptime;
                qz_message_t msg;
                if (quartz_messages_get_latest(&msg) == 0) {
                    ESP_LOGI(TAG, "MAIL from %s: %s", msg.from, msg.text);
#ifdef QUARTZ_HAS_DISPLAY
                    if (quartz_display_get_screen() == QZ_SCREEN_MESSAGES) {
                        quartz_display_message(msg.from, msg.text, msg.block_height);
                    }
#endif
                }
            }

            /* Run autonomous agent rules */
            quartz_agent_step(1);
        }

        nonce++;

#ifdef QUARTZ_HAS_DISPLAY
        /* Poll buttons every iteration */
        poll_buttons();

        /* If on payment screen, don't redraw mining stats */
        if (quartz_display_get_screen() == QZ_SCREEN_PAYMENT) {
            /* QR stays on screen — just yield to other tasks */
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
#endif

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

    /* Initialize display FIRST so we can show portal/splash */
    /* Initialize display (skip on headless boards like LilyGO T3) */
#ifdef QUARTZ_HAS_DISPLAY
    quartz_display_init();
    quartz_display_splash();
    init_buttons();
#else
    ESP_LOGI(TAG, "Headless mode — no display/buttons");
#endif

    /* Initialize WiFi (provisioning or connect) */
    quartz_wifi_init();

    /* Wait for radio to warm up */
    vTaskDelay(pdMS_TO_TICKS(2000));

    /* If in portal mode, show instructions on display */
    if (g_wifi_state == QZ_WIFI_PORTAL_ACTIVE) {
        uint8_t mac[6];
        esp_wifi_get_mac(WIFI_IF_AP, mac);
        char ap_name[32];
        snprintf(ap_name, sizeof(ap_name), "Quartz-%02X%02X", mac[4], mac[5]);
#ifdef QUARTZ_HAS_DISPLAY
        quartz_display_portal(ap_name);
#else
        ESP_LOGI(TAG, "WiFi Portal: Connect to \"%s\", open 192.168.4.1", ap_name);
#endif
    }

    /* Wait for WiFi (or stay in portal mode forever) */
    if (g_wifi_state != QZ_WIFI_PORTAL_ACTIVE) {
#ifdef QUARTZ_HAS_DISPLAY
        quartz_display_connecting();
#endif
        quartz_wifi_wait_connected(15000);
    }

    /* Start mining task on Core 1 (Core 0 handles WiFi/BLE/display) */
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

/* Quartz ESP-NOW radio bench — v100
 *
 * Two boards, same firmware, zero interaction (works with dead serial tx):
 *   - Both broadcast HELLO until they hear each other
 *   - Lower MAC becomes PING (streams 250B DATA packets), higher = PONG (listener)
 *   - Per-second serial stats on both sides: throughput, loss, RSSI
 *   - Live block math: how long a 100KB block takes over this link → TPS ceiling
 *
 * Range test: put PING on a power bank, walk away, watch RSSI/loss on the
 * tethered side. Serial output only — no buttons, no typing.
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_wifi.h"
#include "esp_now.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "esp_log.h"

#define TAG "QZ.ESPNOW"
#define CHAN 1
#define LEN ESP_NOW_MAX_DATA_LEN /* 250 */
#define BLOCK_BYTES (100 * 1024)
#define TX_BYTES 220 /* typical quartz tx size, for TPS math */
#define CONSENSUS_CAP 454 /* 100KB/220B per 30s block */

static uint8_t s_mymac[6];
static uint8_t s_peer[6];
static volatile bool s_peer_known = false;
static volatile bool s_im_ping = false;
static bool s_hello_added = false;

/* counters (ISR/task-safe enough: single writer each, stats read races harmless) */
static volatile uint32_t s_tx_ok, s_tx_fail, s_tx_nomem;
static volatile uint32_t s_rx_n, s_rx_bytes, s_rx_gap;
static volatile int32_t s_rssi_min = 0, s_rssi_max = -128, s_rssi_sum = 0, s_rssi_n = 0;
static volatile TickType_t s_last_rx_tick = 0;

static void on_sent(const uint8_t *mac, esp_now_send_status_t st) {
    if (st == ESP_NOW_SEND_SUCCESS) s_tx_ok++;
    else s_tx_fail++;
}

static void add_unicast_peer(const uint8_t *mac) {
    esp_now_peer_info_t p = {0};
    p.channel = 0;
    p.ifidx = WIFI_IF_STA;
    p.encrypt = false;
    memcpy(p.peer_addr, mac, 6);
    esp_now_add_peer(&p);
}

static void on_recv(const esp_now_recv_info_t *info, const uint8_t *d, int len) {
    if (!info || !d || len < 1) return;
    const uint8_t *src = info->src_addr;

    if (!s_peer_known) {
        memcpy(s_peer, src, 6);
        s_peer_known = true;
        if (d[0] == 'H') {
            s_im_ping = (memcmp(s_mymac, src, 6) < 0);
        } else {
            s_im_ping = false; /* already receiving data: I'm the listener */
        }
        add_unicast_peer(src);
        ESP_LOGI(TAG, "peer %02x%02x%02x%02x%02x%02x → role: %s",
                 src[0], src[1], src[2], src[3], src[4], src[5],
                 s_im_ping ? "PING (streamer)" : "PONG (listener)");
    }
    if (memcmp(src, s_peer, 6) != 0) return;

    if (d[0] == 'H') {
        /* Help the other side finish discovery: unicast a HELLO back */
        if (!s_im_ping) {
            uint8_t h[7] = {'H'};
            memcpy(h + 1, s_mymac, 6);
            esp_now_send(s_peer, h, 7);
        }
        return;
    }
    if (d[0] != 'D' || len < 5) return;

    /* data packet: seq at byte 1..4 */
    uint32_t seq;
    memcpy(&seq, d + 1, 4);
    static uint32_t last_seq = 0;
    static bool have_last = false;
    if (have_last && seq > last_seq && seq - last_seq > 1) s_rx_gap += seq - last_seq - 1;
    last_seq = seq;
    have_last = true;

    s_rx_n++;
    s_rx_bytes += len;
    s_last_rx_tick = xTaskGetTickCount();
    if (info->rx_ctrl) {
        int r = info->rx_ctrl->rssi;
        if (r < s_rssi_min) s_rssi_min = r;
        if (r > s_rssi_max) s_rssi_max = r;
        s_rssi_sum += r;
        s_rssi_n++;
    }
}

static void wifi_now_init(void) {
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_channel(CHAN, WIFI_SECOND_CHAN_NONE));
    ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_STA, s_mymac));

    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_send_cb(on_sent));
    ESP_ERROR_CHECK(esp_now_register_recv_cb(on_recv));

    /* broadcast peer for discovery */
    uint8_t bcast[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    esp_now_peer_info_t p = {0};
    p.channel = 0;
    p.ifidx = WIFI_IF_STA;
    p.encrypt = false;
    memcpy(p.peer_addr, bcast, 6);
    esp_now_add_peer(&p);
    s_hello_added = true;
}

static void print_block_math(float bps) {
    if (bps < 1) {
        ESP_LOGI(TAG, "  (no link yet — block math unavailable)");
        return;
    }
    float block_s = BLOCK_BYTES / bps;
    float tps = bps * 30.0f / TX_BYTES;
    if (tps > CONSENSUS_CAP) tps = CONSENSUS_CAP;
    ESP_LOGI(TAG, "⚡ 100KB block ≈ %.1fs over this link → TPS ceiling ≈ %.0f (consensus cap %d)",
             block_s, tps, CONSENSUS_CAP);
}

void app_main(void) {
    ESP_LOGI(TAG, "🔮 Quartz ESP-NOW radio bench v100 (" CONFIG_IDF_TARGET ")");
    wifi_now_init();
    ESP_LOGI(TAG, "my MAC %02x%02x%02x%02x%02x%02x, channel %d",
             s_mymac[0], s_mymac[1], s_mymac[2], s_mymac[3], s_mymac[4], s_mymac[5], CHAN);
    ESP_LOGI(TAG, "waiting for peer... (power both boards)");

    uint8_t hello[7] = {'H'};
    memcpy(hello + 1, s_mymac, 6);
    uint8_t bcast[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

    uint32_t seq = 1;
    uint8_t buf[LEN];
    memset(buf, 0xAA, sizeof(buf));
    buf[0] = 'D';

    uint32_t w_tx_ok = 0, w_tx_fail = 0, w_bytes = 0;
    uint32_t w_rx_n = 0, w_rx_bytes = 0, w_rx_gap = 0;
    TickType_t last_stats = xTaskGetTickCount();
    int quiet_secs = 0;

    while (1) {
        if (!s_peer_known) {
            if (s_hello_added) esp_now_send(bcast, hello, 7);
            vTaskDelay(pdMS_TO_TICKS(400));
            continue;
        }

        if (s_im_ping) {
            memcpy(buf + 1, &seq, 4);
            esp_err_t r = esp_now_send(s_peer, buf, LEN);
            if (r == ESP_OK) {
                seq++;
                w_bytes += LEN;
                if ((seq & 0x3F) == 0) vTaskDelay(pdMS_TO_TICKS(1)); /* breathe */
            } else if (r == ESP_ERR_ESPNOW_NO_MEM) {
                s_tx_nomem++;
                vTaskDelay(pdMS_TO_TICKS(2));
            } else {
                vTaskDelay(pdMS_TO_TICKS(2));
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(50));
        }

        /* per-second stats on both sides */
        if (xTaskGetTickCount() - last_stats >= pdMS_TO_TICKS(1000)) {
            last_stats = xTaskGetTickCount();
            if (s_im_ping) {
                uint32_t ok = s_tx_ok, fail = s_tx_fail;
                float bps = (ok - w_tx_ok) * (float)LEN;
                ESP_LOGI(TAG, "TX seq=%lu | ok/s=%lu fail/s=%lu | %.1f KB/s delivered",
                         (unsigned long)seq,
                         (unsigned long)(ok - w_tx_ok), (unsigned long)(fail - w_tx_fail),
                         bps / 1024.0f);
                if ((fail - w_tx_fail) > (ok - w_tx_ok) / 2 && (ok - w_tx_ok) + (fail - w_tx_fail) > 10) {
                    ESP_LOGW(TAG, "⚠ high ack-failure — peer out of range or offline");
                }
                print_block_math(bps);
                w_tx_ok = ok; w_tx_fail = fail;
            } else {
                uint32_t n = s_rx_n, b = s_rx_bytes, g = s_rx_gap;
                int32_t rn = s_rssi_n;
                float bps = (b - w_rx_bytes);
                float loss = (n + g > w_rx_n + w_rx_gap)
                             ? 100.0f * ((g - w_rx_gap)) / (float)(n - w_rx_n + g - w_rx_gap + 0.001f)
                             : 0.0f;
                if (n == w_rx_n) quiet_secs++; else quiet_secs = 0;
                ESP_LOGI(TAG, "RX %lu pkt/s | %.1f KB/s | loss %.1f%% | RSSI %ld/%ld/%ld dBm%s",
                         (unsigned long)(n - w_rx_n), bps / 1024.0f, loss,
                         (long)(rn > 0 ? s_rssi_min : 0),
                         (long)(rn > 0 ? s_rssi_sum / rn : 0),
                         (long)(rn > 0 ? s_rssi_max : 0),
                         quiet_secs >= 5 ? " | ⚠ LINK SILENT (out of range?)" : "");
                if ((n - w_rx_n) > 0) print_block_math(bps);
                else print_block_math(0);
                w_rx_n = n; w_rx_bytes = b; w_rx_gap = g;
            }
        }
    }
}

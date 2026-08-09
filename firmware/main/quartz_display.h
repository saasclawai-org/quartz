/**
 * Quartz Display UI — LilyGO T-Display S3
 * 1.14" TFT 240×135 ST7789V
 *
 * Screen states:
 *   BOOT      → Quartz logo + version
 *   MINING    → Hashrate, blocks, temp, uptime
 *   SEED      → One-time seed phrase display (3 screens, button to advance)
 *   SIGN      → Transaction confirmation (show amount/recipient, button to approve)
 *   ADDRESS   → QR-friendly address display
 *   WIPED     → Factory reset confirmation
 */

#ifndef QUARTZ_DISPLAY_H
#define QUARTZ_DISPLAY_H

#include "quartz_wallet.h"
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    QZ_SCREEN_BOOT,
    QZ_SCREEN_MINING,
    QZ_SCREEN_SEED,
    QZ_SCREEN_SIGN,
    QZ_SCREEN_ADDRESS,
    QZ_SCREEN_WIPED,
    QZ_SCREEN_LOCKED,
} quartz_screen_t;

// --- Init ---
void quartz_display_init(void);

// --- Screen updates ---
void quartz_display_show_boot(void);
void quartz_display_show_mining(uint32_t hashrate, uint32_t blocks_found,
                                 uint32_t uptime_sec, int8_t temp_c);
void quartz_display_show_seed(const char words[12][12], int page);
void quartz_display_show_sign_request(const char *to_address, uint64_t amount_sats,
                                       uint64_t fee_sats);
void quartz_display_show_address(const char *address);
void quartz_display_show_wiped(void);
void quartz_display_show_locked(void);
void quartz_display_show_signing_result(bool success);

// --- Button handling ---
typedef enum {
    QZ_BTN_NONE = 0,
    QZ_BTN_SHORT,   // < 1 second
    QZ_BTN_LONG,    // > 3 seconds (for wipe/sign confirm)
} quartz_button_t;

quartz_button_t quartz_button_poll(void);
void quartz_button_enable_irq(void);

// --- Backlight ---
void quartz_display_backlight_on(void);
void quartz_display_backlight_off(void);

// --- Screen rotation (T-Display S3 can be mounted either way) ---
void quartz_display_set_rotation(uint8_t rotation);

#ifdef __cplusplus
}
#endif

#endif // QUARTZ_DISPLAY_H

#ifndef QUARTZ_BLE_H
#define QUARTZ_BLE_H

#include <stdint.h>
#include <stdbool.h>

/* Quartz BLE Service UUID (custom 128-bit) */
/* 0000QZ01-0000-1000-8000-00805F9B34FB — simplified to 16-bit for NimBLE */
#define QUARTZ_BLE_SERVICE_UUID     0x0A01
#define QUARTZ_CHAR_STATS_UUID      0x0A02
#define QUARTZ_CHAR_ADDRESS_UUID    0x0A03

/* Initialize BLE GATT server with mining stats */
void quartz_ble_init(void);

/* Update mining stats (called from mining loop) */
void quartz_ble_update_stats(uint32_t hash_count, uint32_t hash_rate,
                             uint32_t blocks_found, uint32_t uptime);

/* Set wallet address for BLE read */
void quartz_ble_set_address(const char *address);

/* Check if BLE is connected */
bool quartz_ble_is_connected(void);

#endif /* QUARTZ_BLE_H */
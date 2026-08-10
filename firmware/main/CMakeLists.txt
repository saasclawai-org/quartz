# Quartz Miner Firmware — Build System
# Targets:
#   LilyGO T-Display S3    (ESP32-S3 + ST7789V display, no LoRa)
#   Heltec WiFi LoRa 32 V3 (ESP32-S3 + SX1262 LoRa + SSD1306 OLED)
#   TTGO LoRa32 V2.1       (ESP32 + SX1276 LoRa + SSD1306 OLED)
#   M5Stack Core2          (ESP32 + ILI9342 display, no LoRa)

idf_component_register(
    SRCS
        "main.c"
        "quartz.c"
        "quartz_wallet.c"
        "quartz_display.cpp"
        "quartz_lora.c"
        "quartz_storage.c"
        "quartz_attest.c"
        "quartz_pool.c"
        "quartz_supply_chain.c"
    INCLUDE_DIRS
        "."
    REQUIRES
        nvs_flash
        esp_hw_support
        esp_driver_gpio
        esp_driver_spi
        esp_timer
        mbedtls
        fatfs
)

# Note: RadioLib and TFT_eSPI are managed components via idf_component.yml
# They are fetched automatically by ESP-IDF during build.

idf_component_register(
    SRCS
        "quartz.c"
        "main.c"
        "quartz_attest.c"
        "quartz_pool.c"
        "quartz_supply_chain.c"
        "quartz_entropy.c"
        "quartz_storage.c"
        "quartz_wallet.c"
    INCLUDE_DIRS "."
    REQUIRES mbedtls fatfs nvs_flash esp_driver_spi esp_timer esp_event esp_wifi bt esp_adc efuse
)

# Relax warnings for development build
target_compile_options(${COMPONENT_LIB} PRIVATE
    -Wno-error=all
    -Wno-implicit-function-declaration
    -Wno-incompatible-pointer-types
    -Wno-int-conversion
    -Wno-return-type
    -Wno-format
    -Wno-unused-variable
    -Wno-unused-function
    -Wno-unused-but-set-variable
)

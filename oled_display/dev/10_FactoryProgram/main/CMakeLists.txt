idf_component_register(
    SRCS "example_qspi_with_ram.c" "robot_face.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_wifi esp_http_client nvs_flash json esp_event esp_netif mbedtls
                   esp_wifi_bsp i2c_bsp user_app qmi8658c driver)

set_source_files_properties(
    ${LV_DEMOS_SOURCES}
    PROPERTIES COMPILE_OPTIONS
    -DLV_LVGL_H_INCLUDE_SIMPLE)
idf_component_register(
  SRCS "user_app.c"
  PRIV_REQUIRES sd_card_bsp lvgl ui_bsp adc_bsp esp_wifi_bsp ble_scan_bsp gpio_bsp button_bsp
  INCLUDE_DIRS "./")

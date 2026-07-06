file(GLOB_RECURSE scrld ./*.c)
idf_component_register(
  SRCS ${scrld}
  REQUIRES lvgl esp_wifi_bsp adc_bsp sd_card_bsp qmi8658c pcf85063
  INCLUDE_DIRS "custom" "generated" "generated/guider_customer_fonts")

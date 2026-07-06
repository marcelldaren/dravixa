idf_component_register(
  SRCS "multi_button.c" "button_bsp.c"
  PRIV_REQUIRES esp_timer gpio_bsp
  INCLUDE_DIRS "./")

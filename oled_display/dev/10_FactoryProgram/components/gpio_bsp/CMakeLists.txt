idf_component_register(
  SRCS "gpio_bsp.c"
  PRIV_REQUIRES driver i2c_bsp
  INCLUDE_DIRS "./")

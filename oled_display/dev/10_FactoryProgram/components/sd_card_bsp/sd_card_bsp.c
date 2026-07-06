#include <stdio.h>
#include <string.h>
#include <sys/unistd.h>
#include <sys/stat.h>
#include "esp_vfs_fat.h"
#include "sdmmc_cmd.h"
#include "driver/sdmmc_host.h"
#include "sd_card_bsp.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define SDMMC_U

#define SD_D0    6
#define SD_CMD   5
#define SD_CLK   4
#define SDlist "/sd_card" // Directory, similar to a standard
#ifndef SDMMC_U
#define SPI_MISO  6
#define SPI_MOSI  5
#define SPI_CLK   4
#define SPI_CS    2
#define SD_SPI SPI3_HOST
#endif

sdmmc_card_t *card = NULL; // Handle

void sd_card_Init(void)
{
  #ifdef SDMMC_U
    esp_vfs_fat_sdmmc_mount_config_t mount_config = 
    {
      .format_if_mount_failed = true,     // If mounting fails, create a partition table and format the SD card
      .max_files = 5,                     // Maximum number of open files
      .allocation_unit_size = 512,        // Similar to sector size
      .disk_status_check_enable = 1,
    };

    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    //host.max_freq_khz = SDMMC_FREQ_HIGHSPEED; // High speed

    sdmmc_slot_config_t slot_config = SDMMC_SLOT_CONFIG_DEFAULT();
    slot_config.width = 1;           // 1-line
    slot_config.clk = SD_CLK;
    slot_config.cmd = SD_CMD;
    slot_config.d0 = SD_D0;
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_vfs_fat_sdmmc_mount(SDlist, &host, &slot_config, &mount_config, &card));

    /*if(card != NULL)
    {
      sdmmc_card_print_info(stdout, card); // Print card information
      printf("Size:%.2f\n",(float)(card->csd.capacity)/2048/1024); // GB
    }*/
  #endif

  #ifndef SDMMC_U
    esp_vfs_fat_sdmmc_mount_config_t mount_config = 
    {
      .format_if_mount_failed = true,    // If mounting fails, create a partition table and format the SD card
      .max_files = 5,                    // Maximum number of open files
      .allocation_unit_size = 512        // Similar to sector size
    };
    spi_bus_config_t bus_cfg = 
    {
      .mosi_io_num = SPI_MOSI,
      .miso_io_num = SPI_MISO,
      .sclk_io_num = SPI_CLK,
      .quadwp_io_num = -1,
      .quadhd_io_num = -1,
      .max_transfer_sz = 4000,   // Maximum transfer size   
    };
    ESP_ERROR_CHECK_WITHOUT_ABORT(spi_bus_initialize(SD_SPI, &bus_cfg, SDSPI_DEFAULT_DMA));
    sdspi_device_config_t slot_config = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot_config.gpio_cs = SPI_CS;
    slot_config.host_id = SD_SPI;
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = SD_SPI;
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_vfs_fat_sdspi_mount(SDlist, &host, &slot_config, &mount_config, &card)); // Mount SD card
    if(card != NULL)
    {
      sdmmc_card_print_info(stdout, card); // Print card information
      printf("Size:%.2f\n",(float)(card->csd.capacity)/2048/1024); // GB
    }
  #endif
}

float sd_card_get_value(void)
{
  if(card != NULL)
  {
    return (float)(card->csd.capacity)/2048/1024; // GB
  }
  else
    return 0;
}

/* Write data
   path: Path
   data: Data
*/
esp_err_t s_example_write_file(const char *path, char *data)
{
  esp_err_t err;
  if(card == NULL)
  {
    return ESP_ERR_NOT_FOUND;
  }
  err = sdmmc_get_status(card); // First, check if there is an SD card
  if(err != ESP_OK)
  {
    return err;
  }
  FILE *f = fopen(path, "w"); // Get path address
  if(f == NULL)
  {
    printf("path:Write Wrong path\n");
    return ESP_ERR_NOT_FOUND;
  }
  fprintf(f, data); // Write
  fclose(f);
  return ESP_OK;
}

/*
   Read data
   path: Path
*/
esp_err_t s_example_read_file(const char *path, uint8_t *pxbuf, uint32_t *outLen)
{
  esp_err_t err;
  if(card == NULL)
  {
    printf("path:card == NULL\n");
    return ESP_ERR_NOT_FOUND;
  }
  err = sdmmc_get_status(card); // First, check if there is an SD card
  if(err != ESP_OK)
  {
    printf("path:card == NO\n");
    return err;
  }
  FILE *f = fopen(path, "rb");
  if (f == NULL)
  {
    printf("path:Read Wrong path\n");
    return ESP_ERR_NOT_FOUND;
  }
  fseek(f, 0, SEEK_END);     // Move the pointer to the end
  uint32_t unlen = ftell(f);
  //fgets(pxbuf, unlen, f); // Read text
  fseek(f, 0, SEEK_SET); // Move the pointer to the beginning
  uint32_t poutLen = fread((void *)pxbuf,1,unlen,f);
  printf("pxlen: %ld,outLen: %ld\n",unlen,poutLen);
  *outLen = poutLen;
  fclose(f);
  return ESP_OK;
}

/*
  struct stat st;
  stat(file_foo, &st); // Get file information, success returns 0, file_foo is a string, the file name needs a suffix, can indicate whether there is such a file
  unlink(file_foo); // Delete file, success returns 0
  rename(char*,char*); // Rename file
  esp_vfs_fat_sdcard_format(); // Format
  esp_vfs_fat_sdcard_unmount(mount_point, card); // Unmount vfs
  FILE *fopen(const char *filename, const char *mode);
  "w": Open the file in write mode, if the file exists, clear the file content; if the file does not exist, create a new file.
  "a": Open the file in append mode, if the file does not exist, create a new file.
  mkdir(dirname, mode); Create folder

  For reading other non-text type data, use "rb" mode to open the file in a read-only and binary manner, which is suitable for binary files such as images.
  If you only use "r", the file will be opened in text mode, which may lead to data corruption or errors when reading binary files.
  Therefore, for image files (such as JPEG, PNG, etc.), you should use "rb" mode to ensure that the file content is read correctly.
  b represents binary
  The following two functions can be used to return the file size
    fseek(file, 0, SEEK_END): Move the file pointer to the end of the file.
    ftell(file); Return the current position of the file pointer, which is the file size, in bytes.
*/
#include <stdio.h>
#include "esp_wifi_bsp.h"
#include "esp_event.h" // Event handling
#include "nvs_flash.h" // Non-Volatile Storage (NVS) Flash

/* Event handler function */
static void event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data);

/* Task handle and Queue handle (commented out)
// TaskHandle_t pxWIFIreadTask;
// QueueHandle_t WIFI_QueueHandle;
*/

/* Function to initialize the NVS (Non-Volatile Storage) Flash */
void nvs_flash_Init(void)
{
  nvs_flash_init();                    // Initialize the default NVS partition
  esp_netif_init();                    // Initialize the TCP/IP stack
  esp_event_loop_create_default();     // Create the default event loop
  esp_netif_create_default_wifi_sta(); // Attach the TCP/IP stack to the default event loop
}

/* Function to initialize the ESP Wi-Fi */
void espwifi_Init(void)
{
  wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT(); // Default configuration for Wi-Fi
  esp_wifi_init(&cfg);                                 // Initialize the Wi-Fi driver
  esp_event_handler_instance_t Instance_WIFI_IP;
  // Register event handlers for WIFI_EVENT and IP_EVENT
  esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL, &Instance_WIFI_IP);
  esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL, &Instance_WIFI_IP);
  
  // Configure the Wi-Fi station (STA) mode with the specified SSID and password
  wifi_config_t wifi_config = {
      .sta = {
          .ssid = "bsp_esp_demo",
          .password = "emqx123456",
      },
  };
  esp_wifi_set_mode(WIFI_MODE_STA);               // Set the Wi-Fi mode to station (STA)
  esp_wifi_set_config(WIFI_IF_STA, &wifi_config); // Set the Wi-Fi configuration
  esp_wifi_start();                               // Start the Wi-Fi driver
  // WIFI_QueueHandle = xQueueCreate(30,sizeof(wifi_scan_config_t)); // Create a queue with 30 items, each of size wifi_scan_config_t
  // printf("wifi_scan_config_t:%d\n",sizeof(wifi_scan_config_t));
}

/* Function to deinitialize the ESP Wi-Fi */
void espwifi_Deinit(void)
{
  esp_wifi_stop(); // Stop the Wi-Fi driver
  // Unregister the event handlers for WIFI_EVENT and IP_EVENT
  esp_event_handler_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler);
  esp_event_handler_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler);
  esp_wifi_deinit(); // Deinitialize the Wi-Fi driver
}

/* Event handler function definition */
static void event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
  if (event_id == WIFI_EVENT_STA_START)
  {
    // esp_wifi_connect(); // Connect to the Wi-Fi network
    // esp_wifi_scan_u();
  }
  else if (event_id == IP_EVENT_STA_GOT_IP)
  {
    ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data; // Cast the event data to the appropriate type
    char ip[25];
    uint32_t pxip = event->ip_info.ip.addr; // Get the IP address
    // Convert the IP address from integer to string format
    sprintf(ip, "%d.%d.%d.%d", (uint8_t)(pxip), (uint8_t)(pxip >> 8), (uint8_t)(pxip >> 16), (uint8_t)(pxip >> 24));
    printf("IP:%s\n", ip); // Print the IP address
  }
  else if(event_id == WIFI_EVENT_STA_DISCONNECTED)
  {
    printf("diconst\n"); // Print a disconnection message
  }
}
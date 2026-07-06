// robot_face.c
// Canvas drawing (ported verbatim from Arduino display.cpp) +
// WiFi connect + Firestore field-masked poll, ported from the same
// fetch() logic, using esp_wifi / esp_http_client / cJSON.

#include <string.h>
#include <stdlib.h>
#include <assert.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "driver/i2c.h"
#include "driver/uart.h"
#include "qmi8658c.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include "cJSON.h"
#include "lvgl.h"
#include "robot_face.h"

static const char *TAG = "robot_face";

// ═══════════════════════════ USER CONFIG ═════════════════════════════
#define WIFI_SSID        "matt"
#define WIFI_PASSWORD    "12345678"
#define FIREBASE_PROJECT "toyota-avatar-design"
#define FIREBASE_API_KEY "AIzaSyDISmyahQprPfqT9_kk46Ertw09U2CgCZw"
#define USER_UID         "VqzX4LGwIRVaMXN1BVQwY5qQKEj1"
#define POLL_MS          10000
// ═════════════════════════════════════════════════════════════════════

// ── Face coordinates at 600×450 (matching Flutter _RobotFacePainter) ──
#define LCD_W 600
#define LCD_H 450
#define CX 300
#define CY 225
#define EYE_SPACING 132
#define EYE_W 108
#define EYE_H 126
#define EYE_Y 198
#define LEFT_EX  (CX - EYE_SPACING)
#define RIGHT_EX (CX + EYE_SPACING)
#define BROW_Y 107
#define MOUTH_Y 337
#define MOUTH_W 168

// ── Config (updated by fetch_task, read by draw_face_) ────────────────
static int      g_eyeStyle   = 0;
static int      g_browStyle  = 2;
static int      g_mouthStyle = 2;
static uint32_t g_eyeColor   = 0xB2EBF2;
static uint32_t g_browColor  = 0xB2EBF2;
static uint32_t g_mouthColor = 0x80CBC4;
static uint32_t g_bgColor    = 0x001F3F;

static lv_obj_t   *g_canvas    = NULL;
static lv_color_t *g_canvasBuf = NULL;

// ── Reaction-face (IMU) config ─────────────────────────────────────
// Same I2C bus as touch (SDA=47/SCL=48) but inited directly here via
// the plain driver/i2c.h API - avoids the esp_lcd_panel_io_i2c bug
// that forced EXAMPLE_USE_TOUCH=0.
#define IMU_I2C_PORT     I2C_NUM_0
#define IMU_SDA_GPIO     47
#define IMU_SCL_GPIO     48

// ── Jetson UART command channel ────────────────────────────────────
// Wiring: Jetson pin 8 (TX) → ESP32 GPIO39 (RX)
//         Jetson pin 10 (RX) → ESP32 GPIO38 (TX)
//         Shared GND
// Commands (single byte + '\n'): 'W' = warning face, 'C' = confused face
#define FACE_UART_PORT   UART_NUM_1
#define FACE_UART_TX     38
#define FACE_UART_RX     39
#define FACE_UART_BAUD   115200
#define FACE_CMD_HOLD_MS 3000   // how long to hold W/C before reverting

#define BRAKE_THRESHOLD  4.0f   // az < -BRAKE_THRESHOLD -> BRAKE face
#define ACCEL_THRESHOLD  3.0f   // az >  ACCEL_THRESHOLD -> ACCEL face
#define SAMPLE_INTERVAL_MS 20   // IMU sample rate (50Hz)
#define FACE_HOLD_MS       800  // hold reaction face this long after trigger
#define REACT_FRAME_MS      80  // reaction-face animation frame interval (~12fps)

typedef enum { FACE_IDLE, FACE_BRAKE, FACE_ACCEL } FaceMode;
static volatile FaceMode g_faceMode = FACE_IDLE;

// Idle-state cycling: while in FACE_IDLE, the screen rotates through
// these every IDLE_CYCLE_MS. IMU reactions (BRAKE/ACCEL) always
// override this, and a fresh Firestore selection resets it to NORMAL.
typedef enum { IDLE_NORMAL, IDLE_CONFUSED, IDLE_WARNING, IDLE_VARIANT_COUNT } IdleVariant;
static volatile IdleVariant g_idleVariant = IDLE_NORMAL;
#define IDLE_CYCLE_MS 5000

// When non-zero, idle_cycle_task skips its update until this tick passes.
// Set by face_uart_task when a W/C command arrives so the cycle doesn't
// immediately overwrite the commanded face.
static volatile TickType_t g_face_lock_until = 0;

// Colors for reaction overlays (high-contrast "alert" accents,
// independent of the Firestore-selected idle palette)
#define REACT_WHITE  0xFFFFFF
#define REACT_RED    0xFF3B30
#define REACT_YELLOW 0xFFD60A

// ════════════════════════════════════════════════════════════════════
//  CANVAS DRAW HELPERS (ported verbatim from Arduino display.cpp)
// ════════════════════════════════════════════════════════════════════
static void fillRect_(int x,int y,int w,int h,uint32_t col,int radius){
  lv_draw_rect_dsc_t d; lv_draw_rect_dsc_init(&d);
  d.bg_color = lv_color_hex(col); d.bg_opa = LV_OPA_COVER;
  d.radius = radius; d.border_width = 0;
  lv_canvas_draw_rect(g_canvas, x, y, w, h, &d);
}
static void fillCircle_(int cx,int cy,int r,uint32_t col){
  fillRect_(cx-r, cy-r, r*2, r*2, col, LV_RADIUS_CIRCLE);
}
static void drawLine_(int x1,int y1,int x2,int y2,uint32_t col,int width){
  lv_point_t p[2] = {{(lv_coord_t)x1,(lv_coord_t)y1},{(lv_coord_t)x2,(lv_coord_t)y2}};
  lv_draw_line_dsc_t d; lv_draw_line_dsc_init(&d);
  d.color = lv_color_hex(col); d.width = width; d.round_start = 1; d.round_end = 1;
  lv_canvas_draw_line(g_canvas, p, 2, &d);
}
static void drawArc_(int cx,int cy,int r,int start,int end,uint32_t col,int width){
  lv_draw_arc_dsc_t d; lv_draw_arc_dsc_init(&d);
  d.color = lv_color_hex(col); d.width = width; d.rounded = 1;
  lv_canvas_draw_arc(g_canvas, cx, cy, r, start, end, &d);
}
static void drawEye_(int cx, int cy){
  switch(g_eyeStyle){
    case 0: fillCircle_(cx, cy, EYE_W/2, g_eyeColor); break;
    case 1: fillRect_(cx-EYE_W/2, cy-EYE_H/4, EYE_W, EYE_H/2, g_eyeColor, 8); break;
    case 2:
      for(int dy=-1; dy<=1; dy++) for(int dx=-1; dx<=1; dx++)
        fillRect_(cx+dx*22-10, cy+dy*22-10, 20, 20, g_eyeColor, 0);
      break;
    case 3: drawArc_(cx, cy, EYE_W/2, 180, 360, g_eyeColor, 8); break;
    case 4: drawLine_(cx-EYE_W/2, cy, cx+EYE_W/2, cy, g_eyeColor, 10); break;
  }
}
static void drawBrows_(void){
  if(g_browStyle==3) return;
  int w=EYE_W, h=10;
  switch(g_browStyle){
    case 0:
      fillRect_(LEFT_EX -w/2, BROW_Y, w, h, g_browColor, 4);
      fillRect_(RIGHT_EX-w/2, BROW_Y, w, h, g_browColor, 4);
      break;
    case 1:
      drawLine_(LEFT_EX -w/2, BROW_Y-10, LEFT_EX +w/2, BROW_Y+10, g_browColor, h);
      drawLine_(RIGHT_EX-w/2, BROW_Y+10, RIGHT_EX+w/2, BROW_Y-10, g_browColor, h);
      break;
    case 2:
      drawArc_(LEFT_EX,  BROW_Y+20, 40, 200, 340, g_browColor, h);
      drawArc_(RIGHT_EX, BROW_Y+20, 40, 200, 340, g_browColor, h);
      break;
  }
}
static void drawMouth_(void){
  int x=CX-MOUTH_W/2, y=MOUTH_Y;
  switch(g_mouthStyle){
    case 0:
      for(int i=0;i<5;i++) fillRect_(x+i*(MOUTH_W/5)+4, y, MOUTH_W/5-8, 20, g_mouthColor, 0);
      break;
    case 1: drawArc_(CX, y-30, 70, 20, 160, g_mouthColor, 10); break;
    case 2:
      for(int i=0;i<3;i++) fillRect_(x, y+i*12, MOUTH_W, 6, g_mouthColor, 3);
      break;
    case 3: fillRect_(x, y, MOUTH_W, 16, g_mouthColor, 8); break;
    case 4: fillCircle_(CX, y+10, 24, g_mouthColor); break;
  }
}
static void draw_face_(void){
  if(!g_canvas) return;
  lv_canvas_fill_bg(g_canvas, lv_color_hex(g_bgColor), LV_OPA_COVER);
  drawBrows_();
  drawEye_(LEFT_EX,  EYE_Y);
  drawEye_(RIGHT_EX, EYE_Y);
  drawMouth_();
  lv_obj_invalidate(g_canvas);
}

// ════════════════════════════════════════════════════════════════════
//  REACTION FACES (BRAKE / ACCEL) - ported from robot_face.ino
//  Drawn over the same bg color as the idle face, using high-contrast
//  accent colors so they read as "alert overlays" regardless of the
//  Firestore-selected palette.
// ════════════════════════════════════════════════════════════════════

// "Angry/determined" brow pair - same V-shape as browStyle==1 (Alert),
// but forced (used by both reaction faces) and in a fixed accent color.
static void drawBrowsReact_(uint32_t col){
  int w=EYE_W, h=14;
  drawLine_(LEFT_EX -w/2, BROW_Y+12, LEFT_EX +w/2, BROW_Y-12, col, h);
  drawLine_(RIGHT_EX-w/2, BROW_Y-12, RIGHT_EX+w/2, BROW_Y+12, col, h);
}

// Eye drawn as a filled "stadium" (rect w/ LV_RADIUS_CIRCLE) so it can
// be squeezed vertically for a squint, with an offset pupil.
static void drawEyeReact_(int cx, int cy, int w, int h, int pupilDx, uint32_t col){
  fillRect_(cx-w/2, cy-h/2, w, h, REACT_WHITE, LV_RADIUS_CIRCLE);
  int pr = (w<h?w:h)/3;
  int px = cx + pupilDx;
  if(px < cx-w/2+pr) px = cx-w/2+pr;
  if(px > cx+w/2-pr) px = cx+w/2-pr;
  fillCircle_(px, cy, pr, col);
}

// Hollow "O" mouth: white ring on bg color.
static void drawMouthO_(int cx, int cy, int r){
  fillCircle_(cx, cy, r, REACT_WHITE);
  fillCircle_(cx, cy, r - r/3, g_bgColor);
}

// ── BRAKE: shocked - squinting eyes, angry brows, O mouth, "!" ────────
static void draw_face_brake_(uint32_t tick){
  if(!g_canvas) return;
  lv_canvas_fill_bg(g_canvas, lv_color_hex(g_bgColor), LV_OPA_COVER);

  drawBrowsReact_(REACT_WHITE);

  // squint animation: eye height pulses
  int eyeH = 40 + (int)(fabsf(sinf(tick * 0.3f)) * 70.0f); // 40..110
  drawEyeReact_(LEFT_EX,  EYE_Y, EYE_W, eyeH, 0, g_bgColor);
  drawEyeReact_(RIGHT_EX, EYE_Y, EYE_W, eyeH, 0, g_bgColor);

  drawMouthO_(CX, MOUTH_Y, 50);

  // exclamation mark, top-right
  fillRect_(LCD_W-60, 30, 18, 70, REACT_RED, 9);
  fillRect_(LCD_W-60, 116, 18, 18, REACT_RED, 9);

  // "BRAKE!" label, bottom-left
  static lv_draw_label_dsc_t label_dsc;
  lv_draw_label_dsc_init(&label_dsc);
  label_dsc.font  = &lv_font_montserrat_16;
  label_dsc.color = lv_color_hex(REACT_RED);
  lv_canvas_draw_text(g_canvas, 20, LCD_H-40, 200, &label_dsc, "BRAKE!");

  lv_obj_invalidate(g_canvas);
}

// ── ACCEL: excited - forward pupils, big grin, speed lines, "ZOOM!" ───
static void draw_face_accel_(uint32_t tick){
  if(!g_canvas) return;
  lv_canvas_fill_bg(g_canvas, lv_color_hex(g_bgColor), LV_OPA_COVER);

  drawBrowsReact_(REACT_WHITE);

  // eyes looking forward (pupil shifted toward center/forward)
  drawEyeReact_(LEFT_EX,  EYE_Y, EYE_W, 100, 18, g_bgColor);
  drawEyeReact_(RIGHT_EX, EYE_Y, EYE_W, 100, 18, g_bgColor);

  // big grin
  drawArc_(CX, MOUTH_Y-60, 140, 20, 160, REACT_YELLOW, 16);

  // speed lines, left edge, animated
  int offset = (tick / 3) % 6;
  for(int i=0;i<3;i++){
    int lx = 10 + ((offset + i*8) % 40);
    int ly = 100 + i * 90;
    drawLine_(lx, ly, lx+60, ly, REACT_YELLOW, 8);
  }

  // "ZOOM!" label, bottom-right
  static lv_draw_label_dsc_t label_dsc;
  lv_draw_label_dsc_init(&label_dsc);
  label_dsc.font  = &lv_font_montserrat_16;
  label_dsc.color = lv_color_hex(REACT_YELLOW);
  lv_canvas_draw_text(g_canvas, LCD_W-120, LCD_H-40, 110, &label_dsc, "ZOOM!");

  lv_obj_invalidate(g_canvas);
}

// ════════════════════════════════════════════════════════════════════
//  IDLE VARIANTS: CONFUSED / WARNING - cycled in with the normal
//  (Firestore-selected) idle face via g_idleVariant.
// ════════════════════════════════════════════════════════════════════

// ── CONFUSED: asymmetric brows, both eyes circular w/ glance, arch mouth ─
static void draw_face_confused_(void){
  if(!g_canvas) return;
  lv_canvas_fill_bg(g_canvas, lv_color_hex(g_bgColor), LV_OPA_COVER);

  // Asymmetric brows: left raised (questioning), right flat
  drawArc_(LEFT_EX, BROW_Y-10, 40, 200, 340, g_eyeColor, 10);
  fillRect_(RIGHT_EX-EYE_W/2, BROW_Y+10, EYE_W, 10, g_eyeColor, 4);

  // Both eyes circular, with a small offset pupil-dot (same direction
  // on both = "thinking" glance)
  fillCircle_(LEFT_EX,  EYE_Y, EYE_W/2, g_eyeColor);
  fillCircle_(RIGHT_EX, EYE_Y, EYE_W/2, g_eyeColor);
  fillCircle_(LEFT_EX -15, EYE_Y-15, 14, g_bgColor);
  fillCircle_(RIGHT_EX-15, EYE_Y-15, 14, g_bgColor);

  // Arch-shaped "uncertain" mouth: rounded top, ends curving down
  drawArc_(CX, MOUTH_Y+30, 70, 200, 340, g_mouthColor, 10);

  // "?" above head
  static lv_draw_label_dsc_t label_dsc;
  lv_draw_label_dsc_init(&label_dsc);
  label_dsc.font  = &lv_font_montserrat_16;
  label_dsc.color = lv_color_hex(g_eyeColor);
  lv_canvas_draw_text(g_canvas, RIGHT_EX+30, BROW_Y-50, 60, &label_dsc, "?");

  lv_obj_invalidate(g_canvas);
}

// ── WARNING: yellow triangle outline with "!" - full-screen alert ────
static void draw_face_warning_(void){
  if(!g_canvas) return;
  lv_canvas_fill_bg(g_canvas, lv_color_hex(g_bgColor), LV_OPA_COVER);

  // Triangle outline (3 thick rounded edges)
  drawLine_(CX,     CY-110, CX-130, CY+110, REACT_YELLOW, 14);
  drawLine_(CX-130, CY+110, CX+130, CY+110, REACT_YELLOW, 14);
  drawLine_(CX+130, CY+110, CX,     CY-110, REACT_YELLOW, 14);

  // "!" mark
  fillRect_(CX-9, CY-60, 18, 95, REACT_YELLOW, 6);
  fillCircle_(CX, CY+70, 13, REACT_YELLOW);

  lv_obj_invalidate(g_canvas);
}

// ════════════════════════════════════════════════════════════════════
//  PUBLIC: canvas init (called from app_main under the LVGL lock)
// ════════════════════════════════════════════════════════════════════
void robot_face_init(void){
  g_canvasBuf = (lv_color_t*)heap_caps_malloc(
      (uint32_t)LCD_W * LCD_H * sizeof(lv_color_t), MALLOC_CAP_SPIRAM);
  assert(g_canvasBuf);

  lv_obj_t *scr = lv_scr_act();
  lv_obj_set_style_bg_color(scr, lv_color_black(), 0);
  lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

  g_canvas = lv_canvas_create(scr);
  lv_canvas_set_buffer(g_canvas, g_canvasBuf, LCD_W, LCD_H, LV_IMG_CF_TRUE_COLOR);
  lv_obj_set_pos(g_canvas, 0, 0);

  draw_face_();
}

// ════════════════════════════════════════════════════════════════════
//  WIFI
// ════════════════════════════════════════════════════════════════════
static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0

static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                                int32_t event_id, void* event_data){
  if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
    esp_wifi_connect();
  } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
    ESP_LOGW(TAG, "WiFi disconnected, retrying...");
    xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    esp_wifi_connect();
  } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
    ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
    ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
    xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
  }
}

static void wifi_init_sta(void){
  esp_err_t ret = nvs_flash_init();
  if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_ERROR_CHECK(nvs_flash_erase());
    ret = nvs_flash_init();
  }
  ESP_ERROR_CHECK(ret);

  ESP_ERROR_CHECK(esp_netif_init());
  ESP_ERROR_CHECK(esp_event_loop_create_default());
  esp_netif_create_default_wifi_sta();

  s_wifi_event_group = xEventGroupCreate();

  wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&cfg));

  ESP_ERROR_CHECK(esp_event_handler_instance_register(
      WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
  ESP_ERROR_CHECK(esp_event_handler_instance_register(
      IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));

  wifi_config_t wifi_config = { 0 };
  strncpy((char*)wifi_config.sta.ssid, WIFI_SSID, sizeof(wifi_config.sta.ssid)-1);
  strncpy((char*)wifi_config.sta.password, WIFI_PASSWORD, sizeof(wifi_config.sta.password)-1);
  wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
  ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
  ESP_ERROR_CHECK(esp_wifi_start());

  ESP_LOGI(TAG, "Connecting to %s ...", WIFI_SSID);
  xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
  ESP_LOGI(TAG, "WiFi connected");
}

// ════════════════════════════════════════════════════════════════════
//  FIRESTORE FETCH  (field-masked: featured + timestamp + config only,
//  same query as the Arduino version — never pulls the PNG `data`)
// ════════════════════════════════════════════════════════════════════
static uint32_t hexU32(const char *s){
  if(!s || s[0]!='#') return 0x00BFFF;
  return (uint32_t)strtol(s+1, NULL, 16);
}

// HTTP event handler: accumulates response body into a growable buffer
// passed via evt->user_data (a struct { char *buf; int len; int cap; }).
typedef struct { char *buf; int len; int cap; } http_resp_t;

static esp_err_t http_event_handler(esp_http_client_event_t *evt){
  if (evt->event_id == HTTP_EVENT_ON_DATA) {
    http_resp_t *r = (http_resp_t*)evt->user_data;
    if (r->len + evt->data_len + 1 > r->cap) {
      int new_cap = (r->len + evt->data_len + 1) * 2;
      char *nb = realloc(r->buf, new_cap);
      if (!nb) return ESP_FAIL;
      r->buf = nb; r->cap = new_cap;
    }
    memcpy(r->buf + r->len, evt->data, evt->data_len);
    r->len += evt->data_len;
    r->buf[r->len] = '\0';
  }
  return ESP_OK;
}

// Returns true if a featured doc was found and parsed into the g_* globals.
// outTs receives its timestamp (millis since epoch - needs 64-bit, a 32-bit
// long overflows to LONG_MAX on ESP32 and breaks change-detection forever).
static bool fetch_once(int64_t *outTs){
  ESP_LOGI(TAG, "[Heap] free=%u largest=%u",
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));

  char url[320];
  snprintf(url, sizeof(url),
    "https://firestore.googleapis.com/v1/projects/%s/databases/(default)/documents/users/%s/drawings"
    "?mask.fieldPaths=featured&mask.fieldPaths=timestamp&mask.fieldPaths=config&key=%s",
    FIREBASE_PROJECT, USER_UID, FIREBASE_API_KEY);

  http_resp_t resp = { .buf = malloc(1024), .len = 0, .cap = 1024 };
  if (!resp.buf) { ESP_LOGE(TAG, "malloc failed"); return false; }
  resp.buf[0] = '\0';

  esp_http_client_config_t config = {
    .url = url,
    .crt_bundle_attach = esp_crt_bundle_attach,
    .event_handler = http_event_handler,
    .user_data = &resp,
    .timeout_ms = 15000,
  };
  esp_http_client_handle_t client = esp_http_client_init(&config);
  esp_err_t err = esp_http_client_perform(client);

  int status = -1;
  if (err == ESP_OK) {
    status = esp_http_client_get_status_code(client);
  }
  ESP_LOGI(TAG, "[HTTP] err=%s status=%d body=%d bytes",
           esp_err_to_name(err), status, resp.len);

  esp_http_client_cleanup(client);

  bool found = false;
  if (err == ESP_OK && status == 200 && resp.len > 10) {
    cJSON *root = cJSON_Parse(resp.buf);
    if (root) {
      cJSON *docs = cJSON_GetObjectItem(root, "documents");
      cJSON *doc = NULL;
      cJSON_ArrayForEach(doc, docs){
        cJSON *fields = cJSON_GetObjectItem(doc, "fields");
        if (!fields) continue;
        cJSON *featured = cJSON_GetObjectItem(fields, "featured");
        cJSON *featBool = featured ? cJSON_GetObjectItem(featured, "booleanValue") : NULL;
        if (!featBool || !cJSON_IsTrue(featBool)) continue;

        cJSON *tsObj = cJSON_GetObjectItem(fields, "timestamp");
        cJSON *tsVal = tsObj ? cJSON_GetObjectItem(tsObj, "integerValue") : NULL;
        int64_t ts = (tsVal && tsVal->valuestring) ? strtoll(tsVal->valuestring, NULL, 10) : 0;

        cJSON *cfgWrap = cJSON_GetObjectItem(fields, "config");
        cJSON *cfgMap  = cfgWrap ? cJSON_GetObjectItem(cfgWrap, "mapValue") : NULL;
        cJSON *cfg     = cfgMap ? cJSON_GetObjectItem(cfgMap, "fields") : NULL;
        if (!cfg) { ESP_LOGW(TAG, "no config field on featured doc"); continue; }

        #define GET_INT(name, dflt) ({ \
          cJSON *o = cJSON_GetObjectItem(cfg, name); \
          cJSON *v = o ? cJSON_GetObjectItem(o, "integerValue") : NULL; \
          (v && v->valuestring) ? atoi(v->valuestring) : (dflt); })
        #define GET_HEX(name, dflt) ({ \
          cJSON *o = cJSON_GetObjectItem(cfg, name); \
          cJSON *v = o ? cJSON_GetObjectItem(o, "stringValue") : NULL; \
          (v && v->valuestring) ? hexU32(v->valuestring) : (dflt); })

        g_eyeStyle   = GET_INT("eyeStyle", 0);
        g_browStyle  = GET_INT("eyebrowStyle", 0);
        g_mouthStyle = GET_INT("mouthStyle", 0);
        g_eyeColor   = GET_HEX("eyeColor",   0xB2EBF2);
        g_browColor  = GET_HEX("eyebrowColor", 0xB2EBF2);
        g_mouthColor = GET_HEX("mouthColor", 0x80CBC4);
        g_bgColor    = GET_HEX("bgColor",    0x0D1B2A);

        ESP_LOGI(TAG, "[Config] eye=%d brow=%d mouth=%d eyeC=%06X bgC=%06X ts=%lld",
                 g_eyeStyle, g_browStyle, g_mouthStyle, (unsigned)g_eyeColor, (unsigned)g_bgColor, (long long)ts);

        *outTs = ts;
        found = true;
        break;
      }
      if (!found) ESP_LOGW(TAG, "[JSON] no featured doc");
      cJSON_Delete(root);
    } else {
      ESP_LOGE(TAG, "[JSON] parse failed");
    }
  }

  free(resp.buf);
  return found;
}

// ════════════════════════════════════════════════════════════════════
//  POLL TASK
// ════════════════════════════════════════════════════════════════════
static void fetch_task(void *arg){
  int64_t last_ts = -1;
  while (1) {
    int64_t ts = 0;
    if (fetch_once(&ts) && ts != last_ts) {
      last_ts = ts;
      g_idleVariant = IDLE_NORMAL;
      if (robot_face_lvgl_lock(5000)) {
        draw_face_();
        robot_face_lvgl_unlock();
      }
      ESP_LOGI(TAG, "[Poll] updated");
    } else {
      ESP_LOGI(TAG, "[Poll] no change / failed");
    }
    vTaskDelay(pdMS_TO_TICKS(POLL_MS));
  }
}

// ════════════════════════════════════════════════════════════════════
//  IMU (QMI8658C) - I2C_NUM_0, SDA=47/SCL=48, same bus touch would use
// ════════════════════════════════════════════════════════════════════
static void imu_i2c_init(void){
  i2c_config_t conf = {
    .mode = I2C_MODE_MASTER,
    .sda_io_num = IMU_SDA_GPIO,
    .sda_pullup_en = GPIO_PULLUP_ENABLE,
    .scl_io_num = IMU_SCL_GPIO,
    .scl_pullup_en = GPIO_PULLUP_ENABLE,
    .master.clk_speed = 300000,
  };
  esp_err_t err = i2c_param_config(IMU_I2C_PORT, &conf);
  if(err != ESP_OK){
    ESP_LOGW(TAG, "i2c_param_config: %s (continuing - port may already be configured)", esp_err_to_name(err));
  }
  err = i2c_driver_install(IMU_I2C_PORT, conf.mode, 0, 0, 0);
  if(err != ESP_OK){
    // Some other component (e.g. pcf85063 RTC BSP) installs I2C_NUM_0
    // during early startup, before app_main runs. That's fine - the
    // port is already usable, just don't reinstall over it.
    ESP_LOGW(TAG, "i2c_driver_install: %s (assuming I2C_NUM_0 already installed)", esp_err_to_name(err));
  }
}

// Redraw whichever face matches the current mode. Uses a short lock
// timeout so a busy LVGL task just causes a dropped animation frame
// rather than blocking the IMU sample loop.
static void redraw_face(uint32_t tick){
  if(!robot_face_lvgl_lock(50)) return;
  switch(g_faceMode){
    case FACE_BRAKE: draw_face_brake_(tick); break;
    case FACE_ACCEL: draw_face_accel_(tick); break;
    default:
      switch(g_idleVariant){
        case IDLE_CONFUSED: draw_face_confused_(); break;
        case IDLE_WARNING:  draw_face_warning_();  break;
        default:            draw_face_();          break;
      }
      break;
  }
  robot_face_lvgl_unlock();
}

static void imu_task(void *arg){
  imu_i2c_init();

  if(!qmi8658_init()){
    ESP_LOGW(TAG, "QMI8658C init failed - reaction faces disabled");
    vTaskDelete(NULL);
    return;
  }
  qmi8658_config_reg(0);
  qmi8658_enableSensors(QMI8658_ACC_ENABLE);
  ESP_LOGI(TAG, "QMI8658C ready - reaction faces active");

  TickType_t lastSample = xTaskGetTickCount();
  TickType_t lastFrame  = xTaskGetTickCount();
  TickType_t holdUntil  = 0;
  uint32_t   tick       = 0;
  FaceMode   prevMode   = FACE_IDLE;

  while(1){
    TickType_t now = xTaskGetTickCount();

    if((now - lastSample) >= pdMS_TO_TICKS(SAMPLE_INTERVAL_MS)){
      lastSample = now;
      float acc[3] = {0}, gyro[3] = {0};
      qmi8658_read_xyz(acc, gyro);
      float az = acc[2];

      FaceMode detected = FACE_IDLE;
      if      (az < -BRAKE_THRESHOLD) detected = FACE_BRAKE;
      else if (az >  ACCEL_THRESHOLD) detected = FACE_ACCEL;

      if(detected != FACE_IDLE){
        g_faceMode = detected;
        holdUntil  = now + pdMS_TO_TICKS(FACE_HOLD_MS);
      } else if(now >= holdUntil){
        g_faceMode = FACE_IDLE;
      }
    }

    if(g_faceMode != FACE_IDLE){
      if((now - lastFrame) >= pdMS_TO_TICKS(REACT_FRAME_MS)){
        lastFrame = now;
        tick++;
        redraw_face(tick);
      }
    } else if(prevMode != FACE_IDLE){
      // just returned to idle - redraw the normal (Firestore) face once
      redraw_face(0);
    }
    prevMode = g_faceMode;

    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

// ════════════════════════════════════════════════════════════════════
//  UART COMMAND LISTENER (Jetson → ESP32)
//  Receives single-byte commands from Dravixa on the Jetson:
//    'W' → show warning face for FACE_CMD_HOLD_MS then revert to
//           the Firestore-selected face
//    'C' → show confused face for FACE_CMD_HOLD_MS then revert
//  Confused and warning are event-driven only — no automatic cycling.
// ════════════════════════════════════════════════════════════════════
static void face_uart_task(void *arg){
  // Init UART1 on GPIO38 (TX) / GPIO39 (RX)
  const uart_config_t uart_cfg = {
    .baud_rate  = FACE_UART_BAUD,
    .data_bits  = UART_DATA_8_BITS,
    .parity     = UART_PARITY_DISABLE,
    .stop_bits  = UART_STOP_BITS_1,
    .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
  };
  esp_err_t err = uart_param_config(FACE_UART_PORT, &uart_cfg);
  if(err != ESP_OK){
    ESP_LOGE(TAG, "face_uart param_config: %s", esp_err_to_name(err));
    vTaskDelete(NULL); return;
  }
  err = uart_set_pin(FACE_UART_PORT, FACE_UART_TX, FACE_UART_RX,
                     UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
  if(err != ESP_OK){
    ESP_LOGE(TAG, "face_uart set_pin: %s", esp_err_to_name(err));
    vTaskDelete(NULL); return;
  }
  err = uart_driver_install(FACE_UART_PORT, 256, 0, 0, NULL, 0);
  if(err != ESP_OK){
    ESP_LOGE(TAG, "face_uart driver_install: %s", esp_err_to_name(err));
    vTaskDelete(NULL); return;
  }
  ESP_LOGI(TAG, "Face UART ready (TX=%d RX=%d baud=%d)",
           FACE_UART_TX, FACE_UART_RX, FACE_UART_BAUD);

  uint8_t buf[4];
  while(1){
    int len = uart_read_bytes(FACE_UART_PORT, buf, sizeof(buf),
                              pdMS_TO_TICKS(100));
    for(int i = 0; i < len; i++){
      char cmd = (char)buf[i];
      if(cmd == 'W' || cmd == 'C'){
        IdleVariant target = (cmd == 'W') ? IDLE_WARNING : IDLE_CONFUSED;
        g_idleVariant      = target;
        // Lock the idle cycle for the full hold duration + a small buffer
        g_face_lock_until  = xTaskGetTickCount() + pdMS_TO_TICKS(FACE_CMD_HOLD_MS + 200);
        ESP_LOGI(TAG, "[UART] face cmd '%c'", cmd);
        // Draw immediately
        if(g_faceMode == FACE_IDLE) redraw_face(0);
        // Hold for FACE_CMD_HOLD_MS, then revert
        vTaskDelay(pdMS_TO_TICKS(FACE_CMD_HOLD_MS));
        // Only revert if nothing else changed it in the meantime
        if(g_idleVariant == target){
          g_idleVariant = IDLE_NORMAL;
          if(g_faceMode == FACE_IDLE) redraw_face(0);
        }
      }
    }
  }
}

// ════════════════════════════════════════════════════════════════════
//  PUBLIC: start networking (call after releasing the LVGL lock)
// ════════════════════════════════════════════════════════════════════
void robot_face_start_network(void){
  wifi_init_sta();
  xTaskCreatePinnedToCore(fetch_task,     "face_fetch", 8192, NULL, 3, NULL, 0);
  xTaskCreatePinnedToCore(imu_task,       "imu",        4096, NULL, 4, NULL, 1);
  xTaskCreatePinnedToCore(face_uart_task, "face_uart",  4096, NULL, 3, NULL, 0);
}

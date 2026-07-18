/*
  ESP32 Devkit 30-pin (NO PSRAM) + OV2640
  --------------------------------------
  Because there is no PSRAM, the frame buffer MUST fit in internal DRAM (~300KB
  usable). This sketch has a toggle mode via CAMERA_COLOR_MODE (see below,
  before initCamera):
    - CAMERA_COLOR_MODE = 1 -> color (RGB565, QQVGA 160x120 = 38,400 bytes/frame)
    - CAMERA_COLOR_MODE = 0 -> grayscale (QVGA 320x240 = 76,800 bytes/frame)
    - CAMERA_COLOR_MODE = 2 (or other) -> custom
  Change the value and re-upload to switch modes.

  Serial protocol (sent from ESP32 -> PC for each frame):
    [0xAA][0x55][width_L][width_H][height_L][height_H][format][payload...]
    format: 0 = GRAYSCALE (1 byte/pixel), 1 = RGB565 (2 bytes/pixel)
    The Python code (receive_image.py & stream_gui.py) reads width,
    height, and format from this header automatically -- NO need to edit it
    when you change CAMERA_COLOR_MODE in Arduino.

  Capture trigger:
    The PC sends 1 byte character 'c' to ESP32, and ESP32 captures & sends a frame.

  NOTE: this camera module version has a built-in 12MHz oscillator, so ESP32
  does not need to drive XCLK. Set pin_xclk = -1 so esp_camera does not
  enable LEDC for that pin. If your camera module does not have an internal
  oscillator (and requires XCLK from the MCU), connect the sensor XCLK pin to
  ESP32 GPIO 0 and set XCLK_GPIO_NUM to 0.

  Used library: esp_camera.h (included in Arduino-ESP32 core,
  no manual install required). Make sure the selected board in Arduino IDE
  is one of the "ESP32 Dev Module" variants.
*/

#include "esp_camera.h"

// ==================== PIN MAPPING ====================
// Adjust if you wire to different pins. D4-D7 intentionally use
// GPIO 34/35/36/39 because those pins are input-only (good for camera data).
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1   // unused, OV2640 RESET is tied to 3V3 via resistor / left default

// This camera module has a built-in 12MHz oscillator, so ESP32
// does not need to generate XCLK. Set -1 so esp_camera does not
// enable LEDC for this pin. GPIO 0 remains available for other uses
// (and safer because GPIO 0 is a boot-mode strapping pin).
#define XCLK_GPIO_NUM     -1
#define SIOD_GPIO_NUM     26   // I2C SDA (SCCB)
#define SIOC_GPIO_NUM     27   // I2C SCL (SCCB)

#define Y9_GPIO_NUM       35   // D7
#define Y8_GPIO_NUM       34   // D6
#define Y7_GPIO_NUM       39   // D5
#define Y6_GPIO_NUM       36   // D4
#define Y5_GPIO_NUM       21   // D3
#define Y4_GPIO_NUM       19   // D2
#define Y3_GPIO_NUM       18   // D1
#define Y2_GPIO_NUM        5   // D0

#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// ==================== CONFIGURATION ====================
#define SERIAL_BAUD       921600   // use high baud, frame size is fairly large

// ==== COLOR MODE TOGGLE ====
// Change this value BEFORE upload to select mode:
//   1 = color (RGB565, QQVGA 160x120 -> 38,400 bytes/frame)
//   0 = grayscale (1 byte/pixel, QVGA 320x240 -> 76,800 bytes/frame)
// After changing, re-upload the sketch to ESP32.
#define CAMERA_COLOR_MODE 0

bool cameraReady = false;
uint8_t currentFormatCode = 0;   // 0 = grayscale, 1 = RGB565 (diisi otomatis di initCamera)

bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;

  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;

  config.pin_xclk  = XCLK_GPIO_NUM;
  config.pin_pclk  = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href  = HREF_GPIO_NUM;

  // Note: in some Arduino-ESP32 core versions this field is named
  // "pin_sscb_sda/scl", in others it is "pin_sccb_sda/scl".
  // If you get a compile error here, swap sscb <-> sccb.
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;

  config.pin_pwdn  = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;

  // xclk_freq_hz is ignored because pin_xclk = -1 (camera uses internal
  // 12MHz oscillator). The value is still set because the field is required
  // by the struct, but it is not used to generate the clock.
  config.xclk_freq_hz = 12000000;

  // ==== SELECT FORMAT & RESOLUTION BASED ON CAMERA_COLOR_MODE ====
  // RGB565 = 2 bytes/pixel (4x heavier than grayscale), so resolution is
  // lowered to QQVGA to stay safe without PSRAM.
  if (CAMERA_COLOR_MODE == 1) {
    config.pixel_format = PIXFORMAT_RGB565;
    config.frame_size   = FRAMESIZE_QQVGA;   // 160x120 -> 38,400 bytes/frame
    currentFormatCode   = 1;                 // 1 = RGB565
    Serial.println("Mode: COLOR (RGB565, QQVGA 160x120)");
  } 
  else if (CAMERA_COLOR_MODE == 0){
    config.pixel_format = PIXFORMAT_GRAYSCALE;
    config.frame_size   = FRAMESIZE_QVGA;    // 320x240 -> 76,800 bytes/frame
    currentFormatCode   = 0;                 // 0 = grayscale
    Serial.println("Mode: GRAYSCALE (QVGA 320x240)");
  }
  else {
    config.pixel_format = PIXFORMAT_GRAYSCALE;
    config.frame_size   = FRAMESIZE_QVGA;
    currentFormatCode   = 0;                 
    Serial.println("Mode: custom");
  }

  config.fb_location  = CAMERA_FB_IN_DRAM; // REQUIRED because there is no PSRAM
  config.fb_count     = 1;
  config.grab_mode    = CAMERA_GRAB_LATEST;
  config.jpeg_quality = 12; // ignored because format is not JPEG

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed, error 0x%x\n", err);
    return false;
  }
  return true;
}

void sendFrame() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Failed to capture frame");
    return;
  }

  uint8_t header[7];
  header[0] = 0xAA;
  header[1] = 0x55;
  header[2] = fb->width & 0xFF;
  header[3] = (fb->width >> 8) & 0xFF;
  header[4] = fb->height & 0xFF;
  header[5] = (fb->height >> 8) & 0xFF;
  header[6] = currentFormatCode;

  Serial.write(header, 7);
  Serial.write(fb->buf, fb->len);
  Serial.flush();

  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);
  cameraReady = initCamera();
  if (cameraReady) {
    Serial.println("Camera ready. Send 'c' to capture.");
  }
}

void loop() {
  if (Serial.available()) {
    int c = Serial.read();
    if (c == 'c' && cameraReady) {
      sendFrame();
    }
  }
}

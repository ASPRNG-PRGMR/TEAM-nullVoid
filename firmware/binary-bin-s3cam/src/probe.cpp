// probe.cpp - "which ESP32-S3 camera board is this, actually?"
//
// Built only into the s3cam_probe environment. It walks the known pin maps,
// tries to initialise each one and grab a frame, and prints the winner so you
// can set BB_BOARD_PROFILE once and forget about it.
#if defined(BB_RUN_PIN_PROBE) && BB_RUN_PIN_PROBE

#include <Arduino.h>

#include "esp_camera.h"

namespace {

struct PinMap {
  const char* name;
  int profile;
  int xclk, siod, sioc;
  int d7, d6, d5, d4, d3, d2, d1, d0;
  int vsync, href, pclk;
  int ledCandidates[3];
};

const PinMap kMaps[] = {
    {"GENERIC_S3CAM / FREENOVE / S3_EYE (shared reference map)", 1,
     15, 4, 5, 16, 17, 18, 12, 10, 8, 9, 11, 6, 7, 13, {48, 2, 21}},
    {"XIAO_ESP32S3_SENSE", 4,
     10, 40, 39, 48, 11, 12, 14, 16, 18, 17, 15, 38, 47, 13, {21, -1, -1}},
};

bool tryMap(const PinMap& m) {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer   = LEDC_TIMER_0;
  c.pin_pwdn = -1;
  c.pin_reset = -1;
  c.pin_xclk = m.xclk;
  c.pin_sccb_sda = m.siod;
  c.pin_sccb_scl = m.sioc;
  c.pin_d7 = m.d7; c.pin_d6 = m.d6; c.pin_d5 = m.d5; c.pin_d4 = m.d4;
  c.pin_d3 = m.d3; c.pin_d2 = m.d2; c.pin_d1 = m.d1; c.pin_d0 = m.d0;
  c.pin_vsync = m.vsync;
  c.pin_href = m.href;
  c.pin_pclk = m.pclk;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_QVGA;
  c.jpeg_quality = 14;
  c.fb_count = 1;
  c.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

  const esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("   init -> 0x%x\n", err);
    return false;
  }
  camera_fb_t* fb = esp_camera_fb_get();
  const bool got = (fb != nullptr && fb->len > 1000);
  if (fb != nullptr) {
    Serial.printf("   frame %u bytes, %ux%u\n", (unsigned)fb->len,
                  (unsigned)fb->width, (unsigned)fb->height);
    esp_camera_fb_return(fb);
  }
  sensor_t* s = esp_camera_sensor_get();
  if (s != nullptr) Serial.printf("   sensor PID 0x%04X\n", s->id.PID);
  esp_camera_deinit();
  return got;
}

}  // namespace

void runPinProbe() {
  Serial.println("\n--- camera pin-map probe ---");
  Serial.printf("psram: %s\n", psramFound() ? "yes" : "NO (check memory_type)");

  int winner = -1;
  for (size_t i = 0; i < sizeof(kMaps) / sizeof(kMaps[0]); ++i) {
    Serial.printf("\n[%u] %s\n", (unsigned)i, kMaps[i].name);
    if (tryMap(kMaps[i])) {
      Serial.println("   *** WORKS ***");
      winner = static_cast<int>(i);
      break;
    }
    delay(300);
  }

  if (winner < 0) {
    Serial.println("\nNo map worked. Check the ribbon seating and the 3V3 rail,");
    Serial.println("then compare against your board's schematic.");
    return;
  }

  Serial.printf("\nSet in platformio.ini:  -DBB_BOARD_PROFILE=%d\n",
                kMaps[winner].profile);
  Serial.println("\nNow blinking each flash-LED candidate for 2 s. Note which");
  Serial.println("one actually lights the white LED and set BB_PIN_FLASH_LED.");
  for (int j = 0; j < 3; ++j) {
    const int pin = kMaps[winner].ledCandidates[j];
    if (pin < 0) continue;
    Serial.printf("   GPIO %d ...\n", pin);
    pinMode(pin, OUTPUT);
    for (int k = 0; k < 4; ++k) {
      digitalWrite(pin, HIGH); delay(250);
      digitalWrite(pin, LOW);  delay(250);
    }
  }
  Serial.println("probe done.");
}

#endif  // BB_RUN_PIN_PROBE

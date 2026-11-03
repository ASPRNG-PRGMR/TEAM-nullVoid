#include "camera_cfg.h"

#include "config.h"

namespace bb {
namespace cam {
namespace {

constexpr int kFlashChannel = 7;  // LEDC channel; ESP32Servo takes the low ones
const char* g_sensor = "unknown";

camera_config_t makeConfig() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer   = LEDC_TIMER_0;
  c.pin_d0       = BB_CAM_PIN_D0;
  c.pin_d1       = BB_CAM_PIN_D1;
  c.pin_d2       = BB_CAM_PIN_D2;
  c.pin_d3       = BB_CAM_PIN_D3;
  c.pin_d4       = BB_CAM_PIN_D4;
  c.pin_d5       = BB_CAM_PIN_D5;
  c.pin_d6       = BB_CAM_PIN_D6;
  c.pin_d7       = BB_CAM_PIN_D7;
  c.pin_xclk     = BB_CAM_PIN_XCLK;
  c.pin_pclk     = BB_CAM_PIN_PCLK;
  c.pin_vsync    = BB_CAM_PIN_VSYNC;
  c.pin_href     = BB_CAM_PIN_HREF;
  c.pin_sccb_sda = BB_CAM_PIN_SIOD;
  c.pin_sccb_scl = BB_CAM_PIN_SIOC;
  c.pin_pwdn     = BB_CAM_PIN_PWDN;
  c.pin_reset    = BB_CAM_PIN_RESET;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size   = FRAMESIZE_SVGA;  // 800x600
  c.jpeg_quality = cfg::kJpegQuality;
  // Two buffers in the 8 MB PSRAM, and always hand us the newest frame. With
  // fb_count = 1 the driver returns whatever was latched before the flash
  // fired, which is exactly the frame we do not want.
  c.fb_count     = 2;
  c.fb_location  = CAMERA_FB_IN_PSRAM;
  c.grab_mode    = CAMERA_GRAB_LATEST;
  return c;
}

}  // namespace

constexpr bool kHaveFlashLed = (BB_PIN_FLASH_LED >= 0);

void flash(uint8_t duty) {
  if (!kHaveFlashLed) return;
  ledcWrite(BB_PIN_FLASH_LED, duty);
}

bool begin() {
  // LEDC on the flash LED so we can dim it. Arduino core 3.x API.
  if (kHaveFlashLed) {
    ledcAttach(BB_PIN_FLASH_LED, 5000 /*Hz*/, 8 /*bits*/);
    flash(0);
  } else {
    Serial.println("[cam] no illuminator - running on ambient light");
  }

  if (!psramFound()) {
    Serial.println("[cam] no PSRAM - check board_build.arduino.memory_type");
  }

  camera_config_t c = makeConfig();
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("[cam] esp_camera_init failed: 0x%x\n", err);
    return false;
  }

  sensor_t* s = esp_camera_sensor_get();
  if (s == nullptr) {
    Serial.println("[cam] sensor_get returned null");
    return false;
  }
  switch (s->id.PID) {
    case OV2640_PID: g_sensor = "OV2640"; break;
    case OV3660_PID: g_sensor = "OV3660"; break;
    case OV5640_PID: g_sensor = "OV5640"; break;
    default:         g_sensor = "other";  break;
  }

  // The sensor looks straight down into a shallow box under a single LED, so
  // the auto defaults (tuned for outdoor scenes) wash everything out.
  s->set_brightness(s, 0);
  s->set_contrast(s, 1);
  s->set_saturation(s, 1);
  s->set_whitebal(s, 1);
  s->set_awb_gain(s, 1);
  s->set_exposure_ctrl(s, 1);
  s->set_aec2(s, 1);
  s->set_gain_ctrl(s, 1);
  // With an LED doing the work a low ceiling keeps noise down; without one
  // the sensor needs the headroom or an enclosed bay comes out black.
  s->set_gainceiling(s, kHaveFlashLed ? GAINCEILING_4X : GAINCEILING_16X);
  s->set_lenc(s, 1);
  if (s->id.PID == OV3660_PID || s->id.PID == OV5640_PID) {
    s->set_vflip(s, 1);  // these modules are usually mounted upside down
  }

  Serial.printf("[cam] up: %s, SVGA JPEG q%d\n", g_sensor, cfg::kJpegQuality);
  return true;
}

camera_fb_t* captureWithFlash() {
  if (kHaveFlashLed) {
    flash(cfg::kFlashDuty);
    delay(cfg::kFlashWarmupMs);  // let AE/AWB settle under the LED
  }

  // Drain the pipeline. Frames already in flight were exposed in the dark.
  for (int i = 0; i < cfg::kThrowawayFrames; ++i) {
    camera_fb_t* stale = esp_camera_fb_get();
    if (stale != nullptr) esp_camera_fb_return(stale);
  }

  camera_fb_t* fb = esp_camera_fb_get();
  flash(0);

  if (fb == nullptr) {
    Serial.println("[cam] capture failed");
    return nullptr;
  }
  if (fb->format != PIXFORMAT_JPEG) {
    Serial.println("[cam] frame is not JPEG - refusing to send");
    esp_camera_fb_return(fb);
    return nullptr;
  }
  return fb;
}

void release(camera_fb_t* fb) {
  if (fb != nullptr) esp_camera_fb_return(fb);
}

const char* sensorName() { return g_sensor; }

}  // namespace cam
}  // namespace bb

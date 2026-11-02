#pragma once
#include <Arduino.h>
#include "esp_camera.h"

namespace bb {
namespace cam {

// Brings the sensor up in JPEG mode with frame buffers in PSRAM.
// Returns false if the sensor does not answer - almost always a wrong pin map.
bool begin();

// Fires the onboard flash, waits for exposure to settle, throws away the
// stale frames the sensor still holds, then returns a fresh JPEG.
// Caller MUST hand the buffer back with release().
camera_fb_t* captureWithFlash();
void release(camera_fb_t* fb);

void flash(uint8_t duty);  // 0 = off, 255 = full
const char* sensorName();

}  // namespace cam
}  // namespace bb

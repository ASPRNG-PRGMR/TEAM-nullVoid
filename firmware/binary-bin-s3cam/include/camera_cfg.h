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

// Viewfinder. Drops the sensor to QQVGA so frames are a few kB and the
// serial line can carry them at video-ish rates; restores SVGA when off.
// Idempotent, so it is safe to call every tick.
bool setPreviewMode(bool on);
bool previewMode();

// A plain frame at whatever size the sensor is currently set to. No flash,
// no settling delay. Caller MUST hand the buffer back with release().
camera_fb_t* grab();

void flash(uint8_t duty);  // 0 = off, 255 = full
const char* sensorName();

}  // namespace cam
}  // namespace bb

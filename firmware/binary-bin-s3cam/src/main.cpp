// Binary Bin - ESP32-S3-CAM firmware, v1.
//
// v1 splits the work: this chip owns the mechanism and the camera, the laptop
// owns the model. The seam between them is bb::Link, and it is one function
// wide - classify(jpeg) -> bin. v2 swaps the Wi-Fi/UART implementation for an
// on-chip INT8 model and nothing else in this file changes.
#include <Arduino.h>

#include "camera_cfg.h"
#include "config.h"
#include "link.h"
#include "motion.h"
#include "statemachine.h"

#if defined(BB_RUN_PIN_PROBE) && BB_RUN_PIN_PROBE
void runPinProbe();  // probe.cpp
#endif

namespace {

bb::Link* g_link = nullptr;

void banner() {
  Serial.printf("\n=== Binary Bin %s (%s) ===\n", bb::cfg::kFirmware,
                bb::cfg::kDeviceId);
  Serial.printf("chip   : %s rev%d, %d MHz, %d core(s)\n", ESP.getChipModel(),
                ESP.getChipRevision(), getCpuFrequencyMhz(), ESP.getChipCores());
  Serial.printf("flash  : %u MB\n", ESP.getFlashChipSize() / (1024U * 1024U));
  Serial.printf("psram  : %u KB free of %u KB\n", ESP.getFreePsram() / 1024,
                ESP.getPsramSize() / 1024);
  Serial.printf("heap   : %u KB free\n", ESP.getFreeHeap() / 1024);
}

}  // namespace

void setup() {
  Serial.begin(bb::cfg::kUartBaud);
  const uint32_t deadline = millis() + 1500;
  while (!Serial && millis() < deadline) delay(10);
  banner();

#if defined(BB_RUN_PIN_PROBE) && BB_RUN_PIN_PROBE
  runPinProbe();
  return;
#endif

  if (!bb::cam::begin()) {
    Serial.println("FATAL: camera did not come up.");
    Serial.println("Flash the s3cam_probe environment to find this board's pin map.");
    while (true) delay(1000);
  }

#if BB_LINK_DEFAULT == BB_LINK_WIFI
  g_link = bb::makeWifiLink();
  if (!g_link->begin()) {
    Serial.println("[main] Wi-Fi link failed, falling back to UART");
    g_link = bb::makeUartLink();
    g_link->begin();
  }
#else
  g_link = bb::makeUartLink();
  g_link->begin();
#endif
  Serial.printf("[main] link: %s\n", g_link->name());

  if (!bb::motion::begin()) {
    Serial.println("FATAL: motion init failed");
    while (true) delay(1000);
  }

  bb::sm::begin(g_link);
  Serial.println("[main] ready");
}

void loop() {
#if defined(BB_RUN_PIN_PROBE) && BB_RUN_PIN_PROBE
  // The probe is a one-shot. setup() returned early, so the state machine was
  // never started - ticking it here would drive STEP/DIR with no pinMode and
  // spew "IO 41 is not set as GPIO" forever.
  delay(1000);
#else
  bb::sm::tick();
#endif
}

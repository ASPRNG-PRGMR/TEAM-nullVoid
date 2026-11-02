// config.h - everything you are likely to want to change, in one place.
#pragma once

#include <Arduino.h>
#include "board_pins.h"

#define BB_LINK_WIFI 1
#define BB_LINK_UART 2
#ifndef BB_LINK_DEFAULT
#define BB_LINK_DEFAULT BB_LINK_WIFI
#endif

namespace bb {
namespace cfg {

// ----------------------------------------------------------------- identity
constexpr const char* kDeviceId = "binary-bin-01";
constexpr const char* kFirmware = "v1.0.0";

// -------------------------------------------------------------------- bins
// Bin 1 is the recyclable / dry side, bin 2 the non-recyclable / wet side.
// If the classifier is unsure, or the link is down, we route to bin 2: a dry
// item in the wet bin costs us one item, a wet item in the dry bin
// contaminates the whole recyclable load.
enum Bin : uint8_t { kBinNone = 0, kBinRecyclable = 1, kBinNonRecyclable = 2 };
constexpr Bin kFallbackBin = kBinNonRecyclable;
constexpr float kMinConfidence = 0.55f;

// -------------------------------------------------------------- mechanics
// Geometry of the shuttle axis, in millimetres from the home switch.
// The lid is split in thirds; the shuttle parks under the open third and
// travels to the centre of each bin aperture.
constexpr float kBinPitchMm      = 300.0f;  // centre-to-centre of the thirds
constexpr float kStationLoadMm   = 0.0f;
constexpr float kStationBin1Mm   = kBinPitchMm;        // 300
constexpr float kStationBin2Mm   = kBinPitchMm * 2.0f; // 600
constexpr float kMaxTravelMm     = 640.0f;  // soft limit, beyond bin 2's centre

// GT2 belt, 2 mm pitch, 20-tooth pulley -> 40 mm per revolution.
// NEMA 17 at 1.8 deg = 200 full steps, driver at 1/16 microstepping.
constexpr float kMmPerRev        = 40.0f;
constexpr int   kFullStepsPerRev = 200;
constexpr int   kMicrosteps      = 16;
constexpr float kStepsPerMm      = (kFullStepsPerRev * kMicrosteps) / kMmPerRev; // 80

constexpr float kCruiseMmPerSec  = 180.0f;
constexpr float kHomingMmPerSec  = 40.0f;
constexpr float kAccelMmPerSec2  = 900.0f;
constexpr bool  kDirHomeIsLow    = true;   // flip if the carriage runs away from home
constexpr bool  kEnableActiveLow = true;   // TMC2209 / A4988 EN is active LOW
// Normally-OPEN switch between the pin and GND, with INPUT_PULLUP: pressed
// reads LOW. If you wire a normally-CLOSED switch instead (safer - a broken
// wire then reads as "at home" and stops the axis) set this to HIGH.
constexpr uint8_t kHomeSwitchActive = LOW;

// ---------------------------------------------------------------- servos
// Measure these on the rig once and never guess again.
constexpr int kFlapClosedDeg  = 12;
constexpr int kFlapOpenDeg    = 95;
constexpr int kHatchClosedDeg = 8;
constexpr int kHatchOpenDeg   = 85;
constexpr uint32_t kServoTravelMs = 450;   // let the horn finish before moving on
constexpr uint32_t kDumpSettleMs  = 700;   // hold the hatch open so the item clears

// --------------------------------------------------------------- timeouts
constexpr uint32_t kItemWaitMs     = 8000;   // flap open, waiting for a drop
constexpr uint32_t kFlapHoldMs     = 1200;   // keep the flap up after the drop
constexpr uint32_t kClassifyMs     = 4000;   // host round trip budget
constexpr uint32_t kIdlePollMs     = 40;
constexpr uint32_t kCooldownMs     = 1500;   // after a cycle, before re-arming
constexpr uint32_t kMoveTimeoutMs  = 12000;
constexpr uint32_t kHomeTimeoutMs  = 20000;

// ----------------------------------------------------------------- camera
// SVGA is the sweet spot: big enough that a crumpled wrapper is still legible,
// small enough that the JPEG fits one Wi-Fi burst.
constexpr int      kJpegQuality     = 12;    // 10 (best) .. 63 (worst)
constexpr uint32_t kFlashWarmupMs   = 120;   // let AE/AWB settle under the LED
constexpr uint8_t  kFlashDuty       = 150;   // 0..255; full blast blows out plastic
constexpr int      kThrowawayFrames = 2;     // the first frames after the LED are wrong

// ------------------------------------------------------------------ link
constexpr uint32_t kUartBaud       = 921600;
constexpr uint16_t kHttpPort       = 8000;
constexpr const char* kHttpPath    = "/classify";
constexpr uint32_t kWifiConnectMs  = 20000;

// ------------------------------------------------------------------ misc
constexpr bool kDryRun = false;  // true = run the state machine, never move a motor

}  // namespace cfg
}  // namespace bb

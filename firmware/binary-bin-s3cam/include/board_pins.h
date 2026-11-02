// board_pins.h - camera and actuator pin maps for the ESP32-S3 camera boards
// we might end up holding.
//
// "ESP32-S3-CAM" is not one board. The cheap N16R8 modules ship with at least
// three different camera pin maps, and the flash-LED pin moves around too.
// Flash the `s3cam_probe` environment once; it tries every profile below and
// prints the one that initialises. Then set BB_BOARD_PROFILE in platformio.ini
// and stop thinking about it.

#pragma once

#define BB_BOARD_GENERIC_S3CAM 1  // ESP32-CAM-shaped S3 clone (most AliExpress N16R8)
#define BB_BOARD_FREENOVE      2  // Freenove ESP32-S3-WROOM CAM
#define BB_BOARD_S3_EYE        3  // Espressif ESP32-S3-EYE devkit
#define BB_BOARD_XIAO_SENSE    4  // Seeed XIAO ESP32S3 Sense

#ifndef BB_BOARD_PROFILE
#define BB_BOARD_PROFILE BB_BOARD_GENERIC_S3CAM
#endif

// ---------------------------------------------------------------- camera ---
#if BB_BOARD_PROFILE == BB_BOARD_GENERIC_S3CAM || BB_BOARD_PROFILE == BB_BOARD_FREENOVE || \
    BB_BOARD_PROFILE == BB_BOARD_S3_EYE
// The generic clone, the Freenove board and the S3-EYE all descend from the
// same Espressif reference layout, so they share one map. They differ only in
// where the flash LED sits.
#define BB_CAM_PIN_PWDN  (-1)
#define BB_CAM_PIN_RESET (-1)
#define BB_CAM_PIN_XCLK  15
#define BB_CAM_PIN_SIOD  4
#define BB_CAM_PIN_SIOC  5
#define BB_CAM_PIN_D7    16
#define BB_CAM_PIN_D6    17
#define BB_CAM_PIN_D5    18
#define BB_CAM_PIN_D4    12
#define BB_CAM_PIN_D3    10
#define BB_CAM_PIN_D2    8
#define BB_CAM_PIN_D1    9
#define BB_CAM_PIN_D0    11
#define BB_CAM_PIN_VSYNC 6
#define BB_CAM_PIN_HREF  7
#define BB_CAM_PIN_PCLK  13

#elif BB_BOARD_PROFILE == BB_BOARD_XIAO_SENSE
#define BB_CAM_PIN_PWDN  (-1)
#define BB_CAM_PIN_RESET (-1)
#define BB_CAM_PIN_XCLK  10
#define BB_CAM_PIN_SIOD  40
#define BB_CAM_PIN_SIOC  39
#define BB_CAM_PIN_D7    48
#define BB_CAM_PIN_D6    11
#define BB_CAM_PIN_D5    12
#define BB_CAM_PIN_D4    14
#define BB_CAM_PIN_D3    16
#define BB_CAM_PIN_D2    18
#define BB_CAM_PIN_D1    17
#define BB_CAM_PIN_D0    15
#define BB_CAM_PIN_VSYNC 38
#define BB_CAM_PIN_HREF  47
#define BB_CAM_PIN_PCLK  13

#else
#error "Unknown BB_BOARD_PROFILE"
#endif

// ------------------------------------------------------------- flash LED ---
// The onboard white LED next to the lens. Drive it through LEDC so we can dim
// it: full brightness blows out close-up plastics and the highlight eats the
// label we are trying to read.
#if BB_BOARD_PROFILE == BB_BOARD_FREENOVE
#define BB_PIN_FLASH_LED 2
#elif BB_BOARD_PROFILE == BB_BOARD_XIAO_SENSE
#define BB_PIN_FLASH_LED 21
#else
#define BB_PIN_FLASH_LED 48
#endif

// -------------------------------------------------------------- actuators --
// Everything below is our own wiring, not the board's. These are the GPIOs
// left over once the camera, the 16 MB flash (GPIO 26-32) and the 8 MB OPI
// PSRAM (GPIO 33-37) have taken theirs. Avoid GPIO 0/3/45/46 (strapping) and
// GPIO 19/20 (native USB D-/D+).
#define BB_PIN_STEP        41  // TMC2209 / A4988 STEP
#define BB_PIN_DIR         42  // TMC2209 / A4988 DIR
#define BB_PIN_EN          40  // driver ENABLE, active LOW
#define BB_PIN_HOME_SW     39  // home limit switch at the LOAD station, NC to GND
#define BB_PIN_SERVO_FLAP  14  // load-flap servo (the 1/3 lid)
#define BB_PIN_SERVO_HATCH1 21 // drop hatch over BIN 1 (recyclable / dry)
#define BB_PIN_SERVO_HATCH2 47 // drop hatch over BIN 2 (non-recyclable / wet)
#define BB_PIN_PRESENCE    38  // approach / proximity sensor, active LOW
#define BB_PIN_BREAKBEAM   1   // IR break-beam across the load throat, active LOW
#define BB_PIN_I2C_SDA     2   // VL53L1X fill sensors (v1.5, optional)
#define BB_PIN_I2C_SCL     3   // NOTE: GPIO3 is a strapping pin - move if it bites

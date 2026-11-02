# Hardware

## Board

**ESP32-S3-WROOM-1 N16R8** on a camera carrier: 16 MB QIO flash, 8 MB **OPI**
PSRAM, OV2640 (or OV5640) module, onboard white flash LED.

The PSRAM is not optional. SVGA JPEG frame buffers live there; with
`fb_count = 2` in internal RAM the camera driver will not initialise.

```ini
board_build.arduino.memory_type = qio_opi   ; N16R8
```

Change to `qio_qspi` for an N8R2/N16R2 part. Getting this wrong gives you a
board that boots and then fails camera init with a confusing error.

The build lands at roughly **1.1 MB**, which is 85% of the stock 4 MB
partition table's app slot. `platformio.ini` already selects
`default_16MB.csv` so there is plenty of room; if you build this through the
Arduino IDE instead, pick a 16 MB scheme by hand or you will hit the ceiling
the first time you add a library.

## "ESP32-S3-CAM" is not one board

At least three different camera pin maps ship under that name, and the flash
LED moves around too. Rather than guess, flash the prober once:

```bash
cd firmware/binary-bin-s3cam
pio run -e s3cam_probe -t upload && pio device monitor
```

It tries each known map, prints which one grabs a frame and the sensor PID,
then blinks each flash-LED candidate for two seconds. Note which GPIO actually
lights the white LED, then set both values in `platformio.ini` /
`include/board_pins.h` and move on.

| Profile | Boards | XCLK / SIOD / SIOC | Flash LED |
|---|---|---|---|
| `BB_BOARD_GENERIC_S3CAM` | ESP32-CAM-shaped S3 clones | 15 / 4 / 5 | GPIO 48 |
| `BB_BOARD_FREENOVE` | Freenove ESP32-S3-WROOM CAM | 15 / 4 / 5 | GPIO 2 |
| `BB_BOARD_S3_EYE` | Espressif ESP32-S3-EYE | 15 / 4 / 5 | — |
| `BB_BOARD_XIAO_SENSE` | Seeed XIAO ESP32S3 Sense | 10 / 40 / 39 | GPIO 21 |

## GPIO budget

What is left after the camera, the flash (GPIO 26–32) and the OPI PSRAM
(GPIO 33–37) have taken theirs. Avoid GPIO 0/3/45/46 (strapping) and
GPIO 19/20 (native USB D-/D+).

| Signal | GPIO | Notes |
|---|---|---|
| Stepper STEP | 41 | |
| Stepper DIR | 42 | |
| Stepper EN | 40 | active LOW |
| Home limit switch | 39 | NC to GND, `INPUT_PULLUP` |
| Load-flap servo | 14 | |
| Hatch 1 servo (dry) | 21 | |
| Hatch 2 servo (wet) | 47 | |
| Proximity sensor | 38 | active LOW |
| IR break-beam | 1 | active LOW |
| I2C SDA / SCL | 2 / 3 | VL53L1X fill sensors (optional, v1.5) |
| Camera flash LED | 48 | profile-dependent — **verify with the prober** |

GPIO 3 is a strapping pin (JTAG source select). It is usable as I2C SCL in
practice, but if boot behaves oddly with the fill sensors attached, that is
the first wire to move.

## Bill of materials

**Electronics**

| Part | Qty | Why this one |
|---|---|---|
| ESP32-S3-CAM N16R8 | 1 | PSRAM for frame buffers; native USB for the UART fallback |
| NEMA 17 stepper, 40 N·cm | 1 | the shuttle axis |
| TMC2209 driver | 1 | quiet, and its StallGuard is a free jam detector later |
| MG996R / DS3218 servo | 3 | load flap + two drop hatches |
| Microswitch, NC | 1 | home position |
| IR break-beam pair | 1 | confirms the item entered the shuttle |
| HC-SR04 or IR proximity | 1 | wakes the bin |
| VL53L1X ToF | 2 | per-bin fill % (v1.5) |
| 5 V 5 A PSU | 1 | servos stall at ~2 A each; do not run them off USB |
| 12 V supply | 1 | stepper driver VM |

**Mechanics**

| Part | Qty |
|---|---|
| MGN9 linear rail + carriage, ~700 mm | 1 |
| GT2 belt, 2 mm pitch | ~1.5 m |
| GT2 20-tooth pulley + idler | 1 each |
| Shuttle box, open top and bottom | 1 |
| Deck plate with two apertures | 1 |
| Hatch leaves + hinges | 2 per bin (bomb-bay pair) |
| Brush/rubber wiper seal for the shuttle rim | 1 set |

## Power

Servos and the stepper share a ground with the S3 but **not** a rail. Three
MG996R servos stalling together will brown out a board being fed from USB,
and the symptom is a camera that "randomly" fails to initialise.

* 12 V → TMC2209 VM
* 5 V 5 A → servo V+ (with a 1000 µF bulk cap at the servo rail)
* 5 V → board VIN, or just leave USB connected during development
* one star ground

## Camera placement

On the **back wall of the load bay**, about 40 mm above the shuttle rim, tilted
down into the box. Not on the lid: the lid over the load third is the flap, and
it swings away exactly when you need the picture.

The onboard flash is the light source, which is the whole reason the frame is
usable — a closed bin has no ambient light, and a fixed LED at a fixed distance
means every training image and every inference image see the same lighting.
Start at `kFlashDuty = 150` of 255; full brightness blows out clear PET and the
specular highlight eats the label you are trying to read.

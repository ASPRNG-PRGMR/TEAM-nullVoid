#include "motion.h"

#include <ESP32Servo.h>
#include <stdarg.h>

namespace bb {
namespace motion {
namespace {

Servo g_flap, g_hatch1, g_hatch2;

bool    g_homed = false;
bool    g_flapOpen = false;
bool    g_hatch1Open = false;
bool    g_hatch2Open = false;
long    g_posSteps = 0;
Station g_station = Station::kLoad;
char    g_fault[64] = {0};

void fault(const char* f, ...) {
  va_list ap;
  va_start(ap, f);
  vsnprintf(g_fault, sizeof(g_fault), f, ap);
  va_end(ap);
  Serial.printf("[motion] FAULT %s\n", g_fault);
}

long mmToSteps(float mm) { return lroundf(mm * cfg::kStepsPerMm); }

float stationMm(Station s) {
  switch (s) {
    case Station::kBin1: return cfg::kStationBin1Mm;
    case Station::kBin2: return cfg::kStationBin2Mm;
    default:             return cfg::kStationLoadMm;
  }
}

void enableDriver(bool on) {
  digitalWrite(BB_PIN_EN, (on == cfg::kEnableActiveLow) ? LOW : HIGH);
}

bool homeSwitchPressed() {
  return digitalRead(BB_PIN_HOME_SW) == cfg::kHomeSwitchActive;
}

// One microstep. The driver latches on the rising edge; 3 us satisfies both
// the A4988 (1 us min) and the TMC2209 (100 ns) with room to spare.
inline void pulse(uint32_t delayUs) {
  digitalWrite(BB_PIN_STEP, HIGH);
  delayMicroseconds(3);
  digitalWrite(BB_PIN_STEP, LOW);
  if (delayUs > 3) delayMicroseconds(delayUs - 3);
}

uint32_t stepIntervalUs(float mmPerSec) {
  const float stepsPerSec = mmPerSec * cfg::kStepsPerMm;
  if (stepsPerSec < 1.0f) return 100000;
  return static_cast<uint32_t>(1000000.0f / stepsPerSec);
}

// Trapezoidal profile. Short hops never reach cruise, so the ramp is clamped
// to half the move - without that the carriage slams into the far end of a
// 10 mm nudge at full speed.
bool runSteps(long steps, bool forward, float cruiseMmPerSec, uint32_t timeoutMs,
              bool stopOnHomeSwitch) {
  if (steps <= 0) return true;
  if (cfg::kDryRun) {
    delay(min<uint32_t>(timeoutMs, 200));
    return true;
  }

  digitalWrite(BB_PIN_DIR, (forward != cfg::kDirHomeIsLow) ? LOW : HIGH);
  enableDriver(true);
  delayMicroseconds(10);  // DIR setup time

  const uint32_t cruiseUs = stepIntervalUs(cruiseMmPerSec);
  const uint32_t startUs  = stepIntervalUs(max(8.0f, cruiseMmPerSec * 0.12f));
  long rampSteps = lroundf((cruiseMmPerSec * cruiseMmPerSec) /
                           (2.0f * cfg::kAccelMmPerSec2) * cfg::kStepsPerMm);
  rampSteps = min(rampSteps, steps / 2);
  if (rampSteps < 1) rampSteps = 1;

  const uint32_t deadline = millis() + timeoutMs;
  for (long i = 0; i < steps; ++i) {
    if (millis() > deadline) {
      fault("move timeout at step %ld/%ld", i, steps);
      enableDriver(false);
      return false;
    }
    if (stopOnHomeSwitch && homeSwitchPressed()) {
      g_posSteps = 0;
      return true;
    }

    uint32_t iv = cruiseUs;
    const long togo = steps - i;
    if (i < rampSteps) {
      iv = startUs - (startUs - cruiseUs) * i / rampSteps;
    } else if (togo < rampSteps) {
      iv = startUs - (startUs - cruiseUs) * togo / rampSteps;
    }
    pulse(iv);
    g_posSteps += forward ? 1 : -1;

    if ((i & 0x3FF) == 0) yield();  // keep the Wi-Fi task and the WDT happy
  }
  return true;
}

void writeServo(Servo& s, int deg) {
  s.write(constrain(deg, 0, 180));
  delay(cfg::kServoTravelMs);
}

}  // namespace

bool begin() {
  pinMode(BB_PIN_STEP, OUTPUT);
  pinMode(BB_PIN_DIR, OUTPUT);
  pinMode(BB_PIN_EN, OUTPUT);
  digitalWrite(BB_PIN_STEP, LOW);
  enableDriver(false);

  pinMode(BB_PIN_HOME_SW, INPUT_PULLUP);
  pinMode(BB_PIN_PRESENCE, INPUT_PULLUP);
  pinMode(BB_PIN_BREAKBEAM, INPUT_PULLUP);

  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  g_flap.setPeriodHertz(50);
  g_hatch1.setPeriodHertz(50);
  g_hatch2.setPeriodHertz(50);
  g_flap.attach(BB_PIN_SERVO_FLAP, 500, 2400);
  g_hatch1.attach(BB_PIN_SERVO_HATCH1, 500, 2400);
  g_hatch2.attach(BB_PIN_SERVO_HATCH2, 500, 2400);

  // Known-safe state before anything else happens.
  writeServo(g_flap, cfg::kFlapClosedDeg);
  writeServo(g_hatch1, cfg::kHatchClosedDeg);
  writeServo(g_hatch2, cfg::kHatchClosedDeg);
  g_flapOpen = g_hatch1Open = g_hatch2Open = false;
  return true;
}

bool home() {
  g_homed = false;
  closeHatches();
  closeFlap();

  // Already sitting on the switch: step off it first, or the slow approach
  // below has nothing to find.
  if (homeSwitchPressed()) {
    if (!runSteps(mmToSteps(12.0f), true, cfg::kHomingMmPerSec,
                  cfg::kHomeTimeoutMs, false)) {
      return false;
    }
  }

  // Fast seek.
  const long sweep = mmToSteps(cfg::kMaxTravelMm + 40.0f);
  if (!runSteps(sweep, false, cfg::kHomingMmPerSec, cfg::kHomeTimeoutMs, true)) {
    return false;
  }
  if (!homeSwitchPressed()) {
    fault("home switch never closed");
    return false;
  }

  // Back off and creep back in: the repeatability of a microswitch is in the
  // release point, not the trip point.
  runSteps(mmToSteps(5.0f), true, cfg::kHomingMmPerSec * 0.5f, 4000, false);
  if (!runSteps(mmToSteps(10.0f), false, cfg::kHomingMmPerSec * 0.25f, 6000, true)) {
    return false;
  }

  g_posSteps = 0;
  g_station = Station::kLoad;
  g_homed = true;
  enableDriver(false);
  Serial.println("[motion] homed");
  return true;
}

bool isHomed() { return g_homed; }

bool moveTo(Station s) {
  if (!g_homed) {
    fault("move before home");
    return false;
  }
  if (g_flapOpen) {
    fault("refusing to move with the load flap open");
    return false;
  }
  if (anyHatchOpen()) {
    fault("refusing to move with a hatch open");
    return false;
  }

  const float targetMm = stationMm(s);
  if (targetMm < -0.1f || targetMm > cfg::kMaxTravelMm) {
    fault("target %.1f mm outside travel", targetMm);
    return false;
  }

  const long target = mmToSteps(targetMm);
  const long delta = target - g_posSteps;
  if (delta == 0) {
    g_station = s;
    return true;
  }

  const bool ok = runSteps(labs(delta), delta > 0, cfg::kCruiseMmPerSec,
                           cfg::kMoveTimeoutMs, false);
  enableDriver(false);
  if (!ok) {
    g_homed = false;  // position is now a guess; force a re-home
    return false;
  }
  g_posSteps = target;
  g_station = s;
  return true;
}

Station station() { return g_station; }
float positionMm() { return g_posSteps / cfg::kStepsPerMm; }

bool openFlap() {
  if (anyHatchOpen()) {
    fault("refusing to open the flap while a hatch is open");
    return false;
  }
  if (g_station != Station::kLoad) {
    fault("refusing to open the flap away from the load station");
    return false;
  }
  writeServo(g_flap, cfg::kFlapOpenDeg);
  g_flapOpen = true;
  return true;
}

bool closeFlap() {
  writeServo(g_flap, cfg::kFlapClosedDeg);
  g_flapOpen = false;
  return true;
}

bool flapIsOpen() { return g_flapOpen; }

bool openHatch(cfg::Bin bin) {
  if (g_flapOpen) {
    fault("refusing to open a hatch with the load flap open");
    return false;
  }
  const Station want = (bin == cfg::kBinRecyclable) ? Station::kBin1 : Station::kBin2;
  if (bin != cfg::kBinRecyclable && bin != cfg::kBinNonRecyclable) {
    fault("openHatch with no bin");
    return false;
  }
  if (!g_homed || g_station != want) {
    fault("shuttle is not parked over bin %u", static_cast<unsigned>(bin));
    return false;
  }
  // Position has to agree with the station we think we are at, or the belt
  // slipped and the item would land on the deck between the two apertures.
  const float err = fabsf(positionMm() - stationMm(want));
  if (err > 3.0f) {
    fault("position error %.1f mm", err);
    return false;
  }

  if (bin == cfg::kBinRecyclable) {
    writeServo(g_hatch1, cfg::kHatchOpenDeg);
    g_hatch1Open = true;
  } else {
    writeServo(g_hatch2, cfg::kHatchOpenDeg);
    g_hatch2Open = true;
  }
  return true;
}

void closeHatches() {
  if (g_hatch1Open) {
    writeServo(g_hatch1, cfg::kHatchClosedDeg);
    g_hatch1Open = false;
  }
  if (g_hatch2Open) {
    writeServo(g_hatch2, cfg::kHatchClosedDeg);
    g_hatch2Open = false;
  }
}

bool anyHatchOpen() { return g_hatch1Open || g_hatch2Open; }

void disableDriver() { enableDriver(false); }

const char* lastFault() { return g_fault; }

}  // namespace motion
}  // namespace bb

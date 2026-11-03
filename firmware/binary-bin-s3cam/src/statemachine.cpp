// statemachine.cpp - one item, one lap round the loop.
//
//   IDLE -> FLAP_OPEN -> CAPTURE -> CLASSIFY -> TRAVEL -> DUMP -> RETURN -> IDLE
//
// Every transition that touches the mechanism goes through motion.*, which
// enforces the interlocks. If anything here is wrong the worst outcome is a
// refused move and a fault, never an item in the wrong bin with the hatch
// open mid-travel.
#include "statemachine.h"

#include <ArduinoJson.h>

#include "camera_cfg.h"
#include "config.h"
#include "motion.h"

namespace bb {
namespace sm {
namespace {

Link*    g_link = nullptr;
State    g_state = State::kBoot;
uint32_t g_enteredAt = 0;
uint32_t g_seq = 0;
cfg::Bin g_target = cfg::kBinNone;
Verdict  g_verdict;
uint32_t g_cycleStart = 0;
bool     g_faultSwReleased = false;
bool     g_consoleGo = false;  // this cycle was started from the console
bool     g_previewWanted = false;  // viewfinder requested from the console
uint32_t g_counts[3] = {0, 0, 0};  // [0] unused, [1] bin1, [2] bin2
uint32_t g_fallbacks = 0;

void enter(State s) {
  if (s != g_state) {
    Serial.printf("[sm] %s -> %s\n", stateName(g_state), stateName(s));
  }
  g_state = s;
  g_enteredAt = millis();
}

uint32_t elapsed() { return millis() - g_enteredAt; }

bool personPresent() { return digitalRead(BB_PIN_PRESENCE) == LOW; }
bool beamBroken()    { return digitalRead(BB_PIN_BREAKBEAM) == LOW; }

// Console commands, for bring-up before the sensors exist:
//   g  start one cycle, standing in for the presence sensor / break-beam
//   v  toggle the viewfinder stream
// Consumes console keystrokes but stops dead at 0xA5 - the first byte of a
// link frame - so the UART link's own traffic is left untouched.
struct ConsoleCmd {
  bool go = false;
  bool togglePreview = false;
};

ConsoleCmd consoleCommand() {
  ConsoleCmd cmd;
  if (!cfg::kSerialTrigger) return cmd;
  while (Serial.available() > 0) {
    const int c = Serial.peek();
    if (c == 0xA5) break;
    Serial.read();
    if (c == 'g' || c == 'G') cmd.go = true;
    if (c == 'v' || c == 'V') cmd.togglePreview = true;
  }
  return cmd;
}

// Ship one viewfinder frame, no faster than kPreviewIntervalMs. Only ever
// called from IDLE: the sensor is at QQVGA here, and a classification frame
// must be SVGA, so the two must never overlap.
void pumpPreview() {
  static uint32_t last = 0;
  if (g_link == nullptr) return;
  if (millis() - last < cfg::kPreviewIntervalMs) return;
  last = millis();
  camera_fb_t* fb = cam::grab();
  if (fb == nullptr) return;
  g_link->preview(fb->buf, fb->len);
  cam::release(fb);
}

void reportCycle() {
  if (g_link == nullptr) return;
  JsonDocument doc;
  doc["device"]     = cfg::kDeviceId;
  doc["seq"]        = g_seq;
  doc["label"]      = g_verdict.label;
  doc["bin"]        = static_cast<uint8_t>(g_target);
  doc["confidence"] = g_verdict.confidence;
  doc["fallback"]   = (g_verdict.bin != g_target) || !g_verdict.ok;
  doc["cycle_ms"]   = millis() - g_cycleStart;
  doc["link_ms"]    = g_verdict.latencyMs;
  doc["bin1_total"] = g_counts[1];
  doc["bin2_total"] = g_counts[2];
  doc["fallbacks"]  = g_fallbacks;
  char buf[320];
  serializeJson(doc, buf, sizeof(buf));
  g_link->telemetry(buf);
}

void abortToFault(const char* why) {
  Serial.printf("[sm] abort: %s\n", why);
  motion::closeHatches();
  motion::closeFlap();
  motion::disableDriver();
  if (g_link != nullptr) g_link->log(why);
  g_faultSwReleased = false;
  g_consoleGo = false;
  enter(State::kFault);
}

}  // namespace

const char* stateName(State s) {
  switch (s) {
    case State::kBoot:     return "BOOT";
    case State::kHoming:   return "HOMING";
    case State::kIdle:     return "IDLE";
    case State::kFlapOpen: return "FLAP_OPEN";
    case State::kCapture:  return "CAPTURE";
    case State::kClassify: return "CLASSIFY";
    case State::kTravel:   return "TRAVEL";
    case State::kDump:     return "DUMP";
    case State::kReturn:   return "RETURN";
    case State::kCooldown: return "COOLDOWN";
    case State::kFault:    return "FAULT";
  }
  return "?";
}

void begin(Link* link) {
  g_link = link;
  enter(State::kHoming);
}

State state() { return g_state; }

void tick() {
  switch (g_state) {
    case State::kBoot:
      enter(State::kHoming);
      break;

    case State::kHoming:
      if (motion::home()) {
        enter(State::kIdle);
      } else {
        abortToFault(motion::lastFault());
      }
      break;

    case State::kIdle: {
      delay(cfg::kIdlePollMs);
      // The break-beam is the more reliable trigger of the two: someone can
      // post an item through without ever tripping the proximity sensor.
      const ConsoleCmd cmd = consoleCommand();
      if (cmd.togglePreview) {
        g_previewWanted = !g_previewWanted;
        Serial.printf("[sm] viewfinder %s\n", g_previewWanted ? "on" : "off");
      }
      g_consoleGo = cmd.go;

      if (!personPresent() && !beamBroken() && !g_consoleGo) {
        // Idle is the only place the viewfinder runs, so the sensor is never
        // at QQVGA when a frame is wanted for classification.
        cam::setPreviewMode(g_previewWanted);
        if (g_previewWanted) pumpPreview();
        break;
      }
      cam::setPreviewMode(false);  // back to SVGA for the real capture
      g_cycleStart = millis();
      g_seq++;
      if (!motion::openFlap()) {
        abortToFault(motion::lastFault());
        break;
      }
      enter(State::kFlapOpen);
      break;
    }

    case State::kFlapOpen: {
      if (g_consoleGo || beamBroken()) {
        // Item on its way in. Hold the flap a moment so a second item in the
        // same handful is not guillotined by the closing lid - a lid that
        // only exists when something is actually driving it.
        if (!cfg::kDryRun) delay(cfg::kFlapHoldMs);
        motion::closeFlap();
        enter(State::kCapture);
        break;
      }
      if (elapsed() > cfg::kItemWaitMs) {
        // False alarm - someone walked past.
        motion::closeFlap();
        enter(State::kCooldown);
      }
      break;
    }

    case State::kCapture: {
      camera_fb_t* fb = cam::captureWithFlash();
      if (fb == nullptr) {
        // No frame means no evidence. Route by the fail-safe rather than
        // stalling with an item sitting in the shuttle.
        g_verdict = Verdict{};
        strlcpy(g_verdict.label, "no-frame", sizeof(g_verdict.label));
        g_target = cfg::kFallbackBin;
        g_fallbacks++;
        enter(State::kTravel);
        break;
      }
      Serial.printf("[sm] frame %u bytes\n", static_cast<unsigned>(fb->len));
      g_verdict = g_link->classify(fb->buf, fb->len, g_seq);
      cam::release(fb);
      enter(State::kClassify);
      break;
    }

    case State::kClassify: {
      g_target = resolveBin(g_verdict);
      if (!g_verdict.ok) {
        Serial.printf("[sm] link error: %s -> fallback bin %u\n", g_verdict.error,
                      static_cast<unsigned>(g_target));
        g_fallbacks++;
      } else {
        Serial.printf("[sm] %s  conf %.2f  %lu ms -> bin %u\n", g_verdict.label,
                      g_verdict.confidence, (unsigned long)g_verdict.latencyMs,
                      static_cast<unsigned>(g_target));
        if (g_verdict.bin != g_target) g_fallbacks++;
      }
      enter(State::kTravel);
      break;
    }

    case State::kTravel: {
      const motion::Station s = (g_target == cfg::kBinRecyclable)
                                    ? motion::Station::kBin1
                                    : motion::Station::kBin2;
      if (!motion::moveTo(s)) {
        abortToFault(motion::lastFault());
        break;
      }
      enter(State::kDump);
      break;
    }

    case State::kDump: {
      if (!motion::openHatch(g_target)) {
        abortToFault(motion::lastFault());
        break;
      }
      delay(cfg::kDumpSettleMs);
      motion::closeHatches();
      g_counts[g_target]++;
      enter(State::kReturn);
      break;
    }

    case State::kReturn: {
      if (!motion::moveTo(motion::Station::kLoad)) {
        abortToFault(motion::lastFault());
        break;
      }
      motion::disableDriver();
      reportCycle();
      Serial.printf("[sm] cycle %lu done in %lu ms\n", (unsigned long)g_seq,
                    (unsigned long)(millis() - g_cycleStart));
      enter(State::kCooldown);
      break;
    }

    case State::kCooldown:
      g_consoleGo = false;
      if (elapsed() > cfg::kCooldownMs) enter(State::kIdle);
      break;

    case State::kFault: {
      // Deliberately sticky. A mechanism that mis-sorts quietly is worse than
      // one that stops and says so. Recover with a power cycle, or by
      // pressing the home switch by hand.
      //
      // That has to be an edge, not a level: a fault very often leaves the
      // shuttle parked on the home switch, and a level test would re-home
      // instantly and loop. So we wait for the switch to be seen released
      // first, and only then treat a press as a deliberate reset.
      const bool pressed = digitalRead(BB_PIN_HOME_SW) == cfg::kHomeSwitchActive;
      if (!pressed) {
        g_faultSwReleased = true;
      } else if (g_faultSwReleased && elapsed() > 1000) {
        Serial.println("[sm] manual reset");
        g_faultSwReleased = false;
        enter(State::kHoming);
      }
      delay(100);
      break;
    }
  }
}

}  // namespace sm
}  // namespace bb

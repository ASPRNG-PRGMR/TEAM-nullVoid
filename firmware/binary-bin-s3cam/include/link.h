// link.h - the one thing the state machine asks of the outside world:
// "here is a JPEG, which bin does it go in?"
//
// Two implementations answer that question in v1 (Wi-Fi HTTP and a framed
// USB-UART protocol) and a third will in v2 (an INT8 model running on this
// very chip). The state machine never learns which one it is talking to, so
// moving inference to the edge is a one-line change in main.cpp.
#pragma once

#include <Arduino.h>

#include "config.h"

namespace bb {

struct Verdict {
  bool        ok = false;        // did we get an answer at all
  cfg::Bin    bin = cfg::kBinNone;
  float       confidence = 0.0f;
  char        label[24] = {0};
  uint32_t    latencyMs = 0;
  char        error[48] = {0};
};

class Link {
 public:
  virtual ~Link() = default;
  virtual bool begin() = 0;
  virtual bool healthy() = 0;
  // Blocking, bounded by cfg::kClassifyMs.
  virtual Verdict classify(const uint8_t* jpeg, size_t len, uint32_t seq) = 0;
  // Fire-and-forget. Never blocks the cycle.
  virtual void log(const char* line) = 0;
  virtual void telemetry(const char* json) = 0;
  // Low-rate viewfinder frames, for aiming the camera. A transport that has
  // no use for them drops them, so this is never required of an implementation.
  virtual void preview(const uint8_t* jpeg, size_t len) {
    (void)jpeg;
    (void)len;
  }
  virtual const char* name() const = 0;
};

Link* makeWifiLink();
Link* makeUartLink();

// Applies the confidence floor and the fail-safe fallback. Everything that
// is not a confident "recyclable" ends up in the non-recyclable bin.
cfg::Bin resolveBin(const Verdict& v);

}  // namespace bb

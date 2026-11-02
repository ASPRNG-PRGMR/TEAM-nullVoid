#pragma once

#include <Arduino.h>

#include "link.h"

namespace bb {
namespace sm {

enum class State : uint8_t {
  kBoot,
  kHoming,
  kIdle,        // armed, waiting for someone to walk up
  kFlapOpen,    // flap up, waiting for an item
  kCapture,     // flap down, flash + frame
  kClassify,    // asking the host
  kTravel,      // shuttle on its way to the chosen bin
  kDump,        // hatch open, item falling
  kReturn,      // back to the load station
  kCooldown,
  kFault,
};

void begin(Link* link);
void tick();
State state();
const char* stateName(State s);

}  // namespace sm
}  // namespace bb

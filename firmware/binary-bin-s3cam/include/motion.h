// motion.h - the shuttle axis, the load flap and the two drop hatches.
//
// The whole mechanism is one linear axis and three servos. Everything that
// could drop waste in the wrong place is gated here rather than in the state
// machine, so a bug upstairs cannot produce a mis-sort: the hatch simply
// refuses to open.
#pragma once

#include <Arduino.h>

#include "config.h"

namespace bb {
namespace motion {

enum class Station : uint8_t { kLoad = 0, kBin1 = 1, kBin2 = 2 };

bool begin();

// Drives into the home switch, backs off, re-approaches slowly and zeroes the
// position. Nothing else is allowed to move until this has succeeded.
bool home();
bool isHomed();

// Refuses to move unless the flap and both hatches are shut.
bool moveTo(Station s);
Station station();
float positionMm();

bool openFlap();
bool closeFlap();
bool flapIsOpen();

// Refuses unless the shuttle is parked over exactly this bin and the axis is
// idle. Only one hatch is ever commanded open.
bool openHatch(cfg::Bin bin);
void closeHatches();
bool anyHatchOpen();

void disableDriver();  // let the motor cool between cycles
const char* lastFault();

}  // namespace motion
}  // namespace bb

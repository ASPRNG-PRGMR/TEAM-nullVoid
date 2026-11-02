# The mechanism

## What changed from the pitch deck

The submitted design sorted with **twin sweep arms**: an item landed on a
shallow staging deck, and whichever arm was *opposite* the target bin swept it
sideways over a trap door. It reads well on a slide. On a bench it is three
mechanisms that all have to agree — two arm actuators on a shared rail, two
trap doors, and an interlock saying only one arm may move per cycle — and an
arm that stalls halfway leaves an item sitting on the deck with no way to
clear it.

v2 replaces all of that with **one travelling box**.

| | Pitch-deck design | This design |
|---|---|---|
| Moving parts in the sort path | 2 arms + 2 doors | 1 shuttle + 2 hatches |
| Degrees of freedom | 2 (independent arms) | 1 (linear axis) |
| Lid | full-width, lifts on approach | fixed; only the first **1/3** opens |
| Failure with a stalled actuator | item stranded on an open deck | item stays contained in the shuttle |
| Alignment that must hold | arm stroke vs. door edge, twice | shuttle stop vs. aperture, twice |

Losing a degree of freedom is the whole point. A single axis can be homed
against one switch, and its position can be checked against that switch before
anything is allowed to fall.

## Layout

The bin's top face is divided into three equal thirds along its length:

```
   +---------------+---------------+---------------+
   |  LOAD (1/3)   |  BIN 1 (1/3)  |  BIN 2 (1/3)  |   <- top face
   |  hinged flap  |  fixed panel  |  fixed panel  |
   +---------------+---------------+---------------+
   |   [SHUTTLE]  ---------------------------->    |   <- rail, one axis
   +===============+=======@=======+=======@=======+   <- sealed deck
   |   service     |  recyclable   | non-recyclable|      @ = drop hatch
   |   cavity      |     / dry     |    / wet      |
   +---------------+---------------+---------------+
```

* **Only the load third ever opens.** The other two thirds are a sealed panel.
  Nobody can reach into a bin, nothing blows in, and the smell stays put.
* **The shuttle box is open at the top and open at the bottom.** It has no
  floor of its own. The sealed deck it rides on *is* its floor.
* **Each bin has one hatch** in the deck, flush with it, driven by a servo.

So waste can only fall when a hatch is commanded open, and a hatch is only
over a bin. In transit the shuttle is sliding across solid deck.

## The cycle

1. **APPROACH** — proximity sensor (or the break-beam) wakes the bin; the load
   flap lifts.
2. **DROP** — the item falls into the shuttle, which is parked at the load
   station. The break-beam confirms it actually went in. The flap shuts.
3. **IDENTIFY** — the onboard flash fires, the camera grabs a frame, and the
   laptop answers `bin: 1` or `bin: 2`.
4. **TRAVEL** — the carriage drives the shuttle along the rail to the centre
   of the chosen bin's aperture and stops. Both hatches are shut throughout,
   including while passing over bin 1 on the way to bin 2.
5. **DUMP** — that bin's hatch opens, the item falls, the hatch shuts, the
   shuttle returns home.

## Why the box is open at the bottom

There were two ways to do the dump, and the user's brief allowed either:

**A. Closed-bottom shuttle with its own opening floor.** One dump actuator
total, however many bins you add. But the actuator rides on the carriage, so
it needs power and signal through a drag chain or a festoon, and every added
bin makes that cable longer.

**B. Open-bottom shuttle, one hatch per bin.** *This is what is built.* The
shuttle is a passive U of sheet — no wiring on the moving part at all, just a
belt. The hatches are stationary, so their servos are screwed to the frame
next to the controller. The cost is one servo per bin instead of one in total,
which at two bins is a wash and at four bins would start to hurt.

B wins for v1 because the moving part is dumb. The only thing that can go
wrong on the carriage is the belt, and a slipped belt is caught by the
position check before the hatch opens.

If you ever want A, it is a localised change: give the shuttle a floor servo,
and make `motion::openHatch()` drive that instead of the per-bin hatch. The
state machine above it does not change.

## Interlocks

These live in `firmware/binary-bin-s3cam/src/motion.cpp`, not in the state
machine, so a logic bug upstairs cannot produce a mis-sort — the mechanism
refuses instead.

* `moveTo()` refuses while the load flap is open or **either** hatch is open.
* `openFlap()` refuses unless the shuttle is at the load station and both
  hatches are shut.
* `openHatch(bin)` refuses unless the axis is homed, the shuttle's station is
  exactly that bin, and the measured position is within **3 mm** of the
  aperture centre. A slipped belt therefore produces a fault, not an item
  dropped onto the partition between the two bins.
* A failed move clears the homed flag. The axis will not move again until it
  has found the home switch.
* Faults are **sticky**. A bin that quietly mis-sorts is worse than one that
  stops and says so.

## Fail-safe routing

Anything uncertain goes to **bin 2, non-recyclable**:

* classifier confidence below `kMinConfidence` (0.55)
* the host did not answer, or answered with something unparseable
* the camera returned no frame

One dry item in the wet bin costs one item. One wet item in the dry bin
contaminates the whole recyclable load — which is exactly the problem the
project exists to fix.

## Numbers to measure on the real rig

Everything in `include/config.h` is a guess until it is measured. The ones
that matter:

| Constant | Guess | How to check |
|---|---|---|
| `kBinPitchMm` | 300 mm | measure aperture centre to aperture centre |
| `kMmPerRev` | 40 mm | GT2 belt, 20-tooth pulley; count a known move |
| `kFlapOpenDeg` / `kHatchOpenDeg` | 95° / 85° | sweep the servo by hand first |
| `kDumpSettleMs` | 700 ms | slowest sticky item you can find |
| `kFlashDuty` | 150/255 | full brightness blows out clear plastic |

# Roadmap

## v1 — camera stack, inference on the laptop ✅ *this repo*

The bin owns the mechanism and the camera. The laptop owns the model. The
seam between them is one function: `classify(jpeg) -> bin`.

- [x] Camera brought up on the N16R8 with PSRAM frame buffers and the onboard
      flash as the light source
- [x] Two interchangeable transports (Wi-Fi HTTP, framed USB-UART) behind one
      `bb::Link` interface
- [x] Full motion stack: homing, trapezoidal moves, three servos, interlocks
- [x] Cycle state machine with a fail-safe bin and sticky faults
- [x] Host: FastAPI `/classify`, three classifier backends, every frame
      archived as training data
- [x] Fine-tuning script for the archived frames

**Done when:** an item dropped through the flap lands in the right bin,
hands-off, repeatably.

## v1.5 — make it measurable

Nothing here changes the architecture; it changes what you can say about it
on stage.

- [ ] VL53L1X per-bin fill %, reported in the telemetry stream
- [ ] Fine-tune on ~300 images/class captured by this rig and report real
      accuracy — the pitch deck's target is ≥ 90%
- [ ] Replay harness: feed `data/captures/` through the classifier and print
      a confusion matrix, so a model change is a number and not a vibe
- [ ] TMC2209 StallGuard as a jam detector, with a retry-then-lockout
- [ ] A small web dashboard over the telemetry log

## v2 — move inference to the edge

This is the point of the project. v1 exists to make v2 a swap, not a rewrite.

- [ ] Export the fine-tuned MobileNetV3-Small to INT8
- [ ] Implement a third `bb::Link` whose `classify()` runs that model in PSRAM
      (ESP-DL, or TFLite-Micro) and returns without touching the network
- [ ] Measure: inference latency, PSRAM high-water mark, accuracy delta
      against the laptop
- [ ] Keep the host path alive behind a build flag — it is the reference the
      on-device model gets graded against, and it is how the dataset keeps
      growing

**The thing to protect:** nothing above `bb::Link` should need to change.
If v2 requires edits to `statemachine.cpp` or `motion.cpp`, the seam was
drawn in the wrong place.

## Later

- [ ] Sanitary / special-care third compartment — the mechanism already
      generalises: add a station, add a hatch, the axis does not change
- [ ] Solar + battery
- [ ] LoRa for sites with no Wi-Fi
- [ ] Route planning across a fleet of bins from the fill data

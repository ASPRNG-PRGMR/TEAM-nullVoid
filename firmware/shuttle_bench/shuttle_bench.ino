// shuttle_bench.ino - the Arduino IDE sketch that drives the shuttle box and
// the load lid by hand, from the serial monitor.
//
// This is NOT the bin's firmware. The real cycle lives in
// firmware/binary-bin-s3cam/ and runs on the ESP32-S3-CAM. This sketch is the
// bench rig we built the mechanism against at the buildathon: a plain ESP32
// dev board, a TB6612FNG carrying the shuttle motor, and the lid servo. It
// exists so one person can prove the mechanics move - and move far enough -
// without the camera, the classifier or the state machine being in the way.
//
// Pins below are a classic ESP32 (GPIO 23 does not exist on an S3, and 5/18
// are camera lines there). Keep the two boards separate; do not flash this to
// the camera module.
//
//   Driver        ESP32
//   ------        -----
//   BIN1          18
//   BIN2          19
//   PWMB          5
//   STBY          23
//   lid servo     13   (signal only - servo power comes off the 5 V rail)
//
// Serial monitor: 115200 baud, line ending = Newline.
//
//   4        travel LEFT  for the run time  -> bin 1, recyclable / dry
//   6        travel RIGHT for the run time  -> bin 2, non-recyclable / wet
//   O        open the lid    (1600 us)
//   C        close the lid   (1000 us)
//   U<us>    raw lid pulse, 1000-2000 us, for finding those two numbers again
//   F / R    raw forward / reverse, if you do not care which bin that is
//   S        stop now, and brake
//   T<ms>    set the run time   (default 5000)
//   P<0-255> set the motor PWM  (default 75)
//   ?        print what everything is set to
//
// 4 and 6 are the same two keys the demo screens use (host/showcase.py), laid
// out like the bins themselves: left key, left bin. Pressing them here moves
// the box the way a verdict would.

#include <Arduino.h>
#include <esp_arduino_version.h>

// ------------------------------------------------------------------- pins
const uint8_t IN1_PIN = 18;  // Driver BIN1
const uint8_t IN2_PIN = 19;  // Driver BIN2
const uint8_t PWM_PIN = 5;   // Driver PWMB
const uint8_t STBY_PIN = 23;
const uint8_t LID_PIN = 13;  // load-lid servo signal

// -------------------------------------------------------------- motor PWM
const uint32_t PWM_FREQ = 20000;   // above hearing, so the belt is the loudest thing
const uint8_t PWM_BITS = 8;
const uint8_t PWM_CHANNEL = 0;     // core 2.x only; 3.x allocates by pin

// --------------------------------------------------------------- lid servo
// A hobby servo wants a 50 Hz frame and a 1000-2000 us pulse. Measured on this
// rig: 1600 us is the lid open, 1000 us is the lid shut. Re-measure with U
// before trusting them on a rebuilt lid - a horn one tooth over changes both.
const uint32_t SERVO_FREQ = 50;
const uint8_t SERVO_BITS = 16;
const uint8_t SERVO_CHANNEL = 2;   // core 2.x: channels 0/1 share a timer with the motor
const uint16_t LID_OPEN_US = 1600;
const uint16_t LID_CLOSE_US = 1000;
const uint16_t LID_MIN_US = 1000;  // refuse anything that could strip the horn
const uint16_t LID_MAX_US = 2000;

// Leave the lid limp at power-up rather than slamming it to a position the
// mechanism may not be in. Set true once the geometry is trusted.
const bool CLOSE_LID_ON_BOOT = false;

// Which way the motor turns for "right". Flip this one line if 4 and 6 send
// the box to the wrong bins - nothing else needs to change.
const bool RIGHT_IS_FORWARD = true;

uint8_t motorSpeed = 75;       // PWM: 0-255
uint32_t runTimeMs = 5000;     // 5 s: load station to a bin, measured on the rail

bool pwmReady = false;
bool servoReady = false;
bool running = false;
bool waitingToStart = false;
bool pendingForward = true;

uint32_t stoppedAt = 0;
uint32_t runStartedAt = 0;
uint32_t activeRunTimeMs = 0;
uint8_t activeSpeed = 0;

uint16_t lidUs = 0;            // 0 = never commanded, so no pulse is going out

void setSpeed(uint8_t speed) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PWM_PIN, speed);
#else
  ledcWrite(PWM_CHANNEL, speed);
#endif
}

// ------------------------------------------------------------------- lid
void writeLidUs(uint16_t us) {
  if (!servoReady) {
    Serial.println("Lid servo not attached.");
    return;
  }

  // duty = us / 20000 us of frame, scaled to the full 16-bit range.
  const uint32_t full = 1UL << SERVO_BITS;
  const uint32_t periodUs = 1000000UL / SERVO_FREQ;
  uint32_t duty = ((uint32_t)us * full) / periodUs;

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(LID_PIN, duty);
#else
  ledcWrite(SERVO_CHANNEL, duty);
#endif

  lidUs = us;
}

void reportLid(const char* what) {
  Serial.print(what);
  Serial.print(" (");
  Serial.print(lidUs);
  Serial.println(" us)");
}

// ----------------------------------------------------------------- motor
void stopMotor() {
  // IN1 = IN2 = HIGH is the driver's short brake, and STBY stays HIGH so the
  // brake is actually held: a belt-driven box left coasting rolls off station.
  digitalWrite(IN1_PIN, HIGH);
  digitalWrite(IN2_PIN, HIGH);
  setSpeed(0);
  digitalWrite(STBY_PIN, HIGH);

  running = false;
  waitingToStart = false;
  stoppedAt = millis();
}

void requestRun(bool forward) {
  stopMotor();

  pendingForward = forward;
  activeRunTimeMs = runTimeMs;
  activeSpeed = motorSpeed;
  waitingToStart = true;
}

void travel(bool right) {
  requestRun(right == RIGHT_IS_FORWARD);

  Serial.print(right ? "Bin 2, right: " : "Bin 1, left: ");
  Serial.print(activeRunTimeMs);
  Serial.print(" ms at PWM ");
  Serial.println(activeSpeed);
}

void updateMotor() {
  // Non-blocking pause before starting/changing direction.
  if (waitingToStart && millis() - stoppedAt >= 300) {
    digitalWrite(STBY_PIN, LOW);
    digitalWrite(IN1_PIN, pendingForward ? HIGH : LOW);
    digitalWrite(IN2_PIN, pendingForward ? LOW : HIGH);
    setSpeed(activeSpeed);
    digitalWrite(STBY_PIN, HIGH);

    waitingToStart = false;
    running = true;
    runStartedAt = millis();

    Serial.print(pendingForward ? "Forward for " : "Reverse for ");
    Serial.print(activeRunTimeMs);
    Serial.println(" ms");
  }

  if (running && millis() - runStartedAt >= activeRunTimeMs) {
    stopMotor();
    Serial.println("Stopped: timer finished.");
  }
}

// --------------------------------------------------------------- console
void printStatus() {
  Serial.print("Run time ");
  Serial.print(runTimeMs);
  Serial.print(" ms, PWM ");
  Serial.print(motorSpeed);
  Serial.print(", right = ");
  Serial.println(RIGHT_IS_FORWARD ? "forward" : "reverse");

  Serial.print("Lid: ");
  if (lidUs == 0) {
    Serial.println("no pulse yet - O or C to take it");
  } else {
    Serial.print(lidUs);
    Serial.print(" us");
    if (lidUs == LID_OPEN_US) Serial.print(" (open)");
    if (lidUs == LID_CLOSE_US) Serial.print(" (closed)");
    Serial.println();
  }

  Serial.print("Motor: ");
  if (running) {
    uint32_t elapsed = millis() - runStartedAt;
    Serial.print("running, ");
    Serial.print(elapsed >= activeRunTimeMs ? 0 : activeRunTimeMs - elapsed);
    Serial.println(" ms left");
  } else if (waitingToStart) {
    Serial.println("about to start");
  } else {
    Serial.println("stopped (braked)");
  }
}

void printHelp() {
  Serial.println("Commands: 4 left, 6 right, O open lid, C close lid,");
  Serial.println("          U1600 raw lid us, F, R, S, T5000, P75, ?");
}

void handleCommand(String command) {
  command.trim();
  command.toUpperCase();

  if (command.length() == 0) return;

  if (command == "4") {
    travel(false);
    return;
  }

  if (command == "6") {
    travel(true);
    return;
  }

  if (command == "O") {
    writeLidUs(LID_OPEN_US);
    if (servoReady) reportLid("Lid open");
    return;
  }

  if (command == "C") {
    writeLidUs(LID_CLOSE_US);
    if (servoReady) reportLid("Lid closed");
    return;
  }

  if (command == "F") {
    requestRun(true);
    return;
  }

  if (command == "R") {
    requestRun(false);
    return;
  }

  if (command == "S") {
    stopMotor();
    Serial.println("Stopped.");
    return;
  }

  if (command == "?") {
    printStatus();
    return;
  }

  char type = command.charAt(0);

  if ((type == 'T' || type == 'P' || type == 'U') && command.length() > 1) {
    String number = command.substring(1);
    number.trim();

    bool valid = number.length() > 0 && number.length() <= 5;

    for (size_t i = 0; i < number.length(); i++) {
      if (number[i] < '0' || number[i] > '9') {
        valid = false;
      }
    }

    if (!valid) {
      Serial.println("Use T5000, P75 or U1600.");
      return;
    }

    long value = number.toInt();

    if (type == 'T') {
      if (value < 1 || value > 60000) {
        Serial.println("Time must be 1-60000 ms.");
        return;
      }

      runTimeMs = (uint32_t)value;
      Serial.print("Run time set to ");
      Serial.print(runTimeMs);
      Serial.println(" ms");
    } else if (type == 'P') {
      if (value < 0 || value > 255) {
        Serial.println("PWM must be 0-255.");
        return;
      }

      motorSpeed = (uint8_t)value;
      Serial.print("PWM set to ");
      Serial.println(motorSpeed);
    } else {
      if (value < LID_MIN_US || value > LID_MAX_US) {
        Serial.print("Lid pulse must be ");
        Serial.print(LID_MIN_US);
        Serial.print("-");
        Serial.print(LID_MAX_US);
        Serial.println(" us.");
        return;
      }

      writeLidUs((uint16_t)value);
      if (servoReady) reportLid("Lid at");
    }
    return;
  }

  printHelp();
}

void readSerial() {
  static String input;
  static bool overflow = false;

  // Bound work per loop so motor timing stays responsive.
  for (uint8_t i = 0; i < 32 && Serial.available(); i++) {
    char c = Serial.read();

    if (c == '\n') {
      if (!overflow) {
        handleCommand(input);
      } else {
        Serial.println("Command too long.");
      }
      input = "";
      overflow = false;
    } else if (c != '\r' && !overflow) {
      if (input.length() < 32) {
        input += c;
      } else {
        overflow = true;
      }
    }
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(STBY_PIN, OUTPUT);
  digitalWrite(STBY_PIN, LOW);

  pinMode(IN1_PIN, OUTPUT);
  pinMode(IN2_PIN, OUTPUT);
  digitalWrite(IN1_PIN, LOW);
  digitalWrite(IN2_PIN, LOW);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  pwmReady = ledcAttach(PWM_PIN, PWM_FREQ, PWM_BITS);
  servoReady = ledcAttach(LID_PIN, SERVO_FREQ, SERVO_BITS);
#else
  pwmReady = ledcSetup(PWM_CHANNEL, PWM_FREQ, PWM_BITS) > 0;
  if (pwmReady) {
    ledcAttachPin(PWM_PIN, PWM_CHANNEL);
  }
  servoReady = ledcSetup(SERVO_CHANNEL, SERVO_FREQ, SERVO_BITS) > 0;
  if (servoReady) {
    ledcAttachPin(LID_PIN, SERVO_CHANNEL);
  }
#endif

  if (!pwmReady) {
    Serial.println("Motor PWM setup failed. Motor disabled.");
    return;
  }

  if (!servoReady) {
    Serial.println("Lid PWM setup failed. Lid disabled.");
  } else if (CLOSE_LID_ON_BOOT) {
    writeLidUs(LID_CLOSE_US);
    reportLid("Lid closed");
  }
  // Otherwise the lid channel is attached but at zero duty, so no pulse goes
  // out and the lid stays where the mechanism left it instead of snapping to
  // a position at power-up. O or C takes it.

  stopMotor();
  printHelp();
  Serial.print("Default: ");
  Serial.print(runTimeMs);
  Serial.print(" ms run time, PWM ");
  Serial.print(motorSpeed);
  Serial.print(", lid ");
  Serial.print(LID_OPEN_US);
  Serial.print("/");
  Serial.print(LID_CLOSE_US);
  Serial.println(" us. Use Newline.");
}

void loop() {
  if (!pwmReady) return;

  updateMotor();
  readSerial();
}

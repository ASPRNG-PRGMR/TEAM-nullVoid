// link_uart.cpp - framed binary protocol over the USB serial port.
//
// Same port carries logs, telemetry, images and verdicts, so there is exactly
// one cable to the laptop. Everything is framed and CRC'd, which means the
// host can resynchronise after the bootloader's plaintext banner, after a
// panic dump, or after you unplug the thing mid-frame.
//
//   byte 0      0xA5
//   byte 1      0x5A
//   byte 2      type
//   bytes 3..6  payload length, u32 little endian
//   bytes 7..   payload
//   last 4      CRC32 (IEEE, zlib-compatible) over the payload, little endian
//
// See docs/PROTOCOL.md. host/binarybin/protocol.py is the other half.
#include <ArduinoJson.h>

#include "link.h"

namespace bb {
namespace {

constexpr uint8_t kSync0 = 0xA5;
constexpr uint8_t kSync1 = 0x5A;

constexpr uint8_t kTypeImage     = 0x01;
constexpr uint8_t kTypeVerdict   = 0x02;
constexpr uint8_t kTypePreview   = 0x03;
constexpr uint8_t kTypeLog       = 0x10;
constexpr uint8_t kTypeTelemetry = 0x11;
constexpr uint8_t kTypeHello     = 0x20;

constexpr size_t kMaxPayload = 512 * 1024;

// Nibble-table CRC32: 1/16th the flash of the byte table, fast enough for a
// 40 kB frame at 240 MHz (~1 ms).
const uint32_t kCrcNibble[16] = {
    0x00000000, 0x1DB71064, 0x3B6E20C8, 0x26D930AC, 0x76DC4190, 0x6B6B51F4,
    0x4DB26158, 0x5005713C, 0xEDB88320, 0xF00F9344, 0xD6D6A3E8, 0xCB61B38C,
    0x9B64C2B0, 0x86D3D2D4, 0xA00AE278, 0xBDBDF21C};

uint32_t crc32Update(uint32_t crc, const uint8_t* data, size_t len) {
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    crc = (crc >> 4) ^ kCrcNibble[crc & 0x0F];
    crc = (crc >> 4) ^ kCrcNibble[crc & 0x0F];
  }
  return crc;
}

class UartLink final : public Link {
 public:
  bool begin() override {
    Serial.begin(cfg::kUartBaud);
    const uint32_t deadline = millis() + 2000;
    while (!Serial && millis() < deadline) delay(10);
    delay(100);
    JsonDocument doc;
    doc["device"] = cfg::kDeviceId;
    doc["fw"] = cfg::kFirmware;
    doc["proto"] = 1;
    char buf[160];
    const size_t n = serializeJson(doc, buf, sizeof(buf));
    sendFrame(kTypeHello, reinterpret_cast<const uint8_t*>(buf), n);
    return true;
  }

  bool healthy() override { return true; }

  Verdict classify(const uint8_t* jpeg, size_t len, uint32_t seq) override {
    Verdict v;
    const uint32_t t0 = millis();

    // Payload is the sequence number followed by the JPEG, so a late reply
    // from a previous cycle can be recognised and dropped.
    uint8_t hdr[4];
    writeU32(hdr, seq);
    beginFrame(kTypeImage, 4 + len);
    uint32_t crc = 0xFFFFFFFFu;
    crc = crc32Update(crc, hdr, 4);
    Serial.write(hdr, 4);
    crc = crc32Update(crc, jpeg, len);
    writeChunked(jpeg, len);
    endFrame(crc);

    uint8_t type = 0;
    const size_t n = readFrame(&type, rx_, sizeof(rx_), cfg::kClassifyMs);
    if (n == 0) {
      snprintf(v.error, sizeof(v.error), "no reply");
      return v;
    }
    if (type != kTypeVerdict) {
      snprintf(v.error, sizeof(v.error), "type 0x%02X", type);
      return v;
    }

    JsonDocument doc;
    if (deserializeJson(doc, rx_, n)) {
      snprintf(v.error, sizeof(v.error), "bad json");
      return v;
    }
    const uint32_t replySeq = doc["seq"] | seq;
    if (replySeq != seq) {
      snprintf(v.error, sizeof(v.error), "stale seq %lu", (unsigned long)replySeq);
      return v;
    }

    v.ok = true;
    v.bin = static_cast<cfg::Bin>(doc["bin"] | 0);
    v.confidence = doc["confidence"] | 0.0f;
    strlcpy(v.label, doc["label"] | "?", sizeof(v.label));
    v.latencyMs = millis() - t0;
    return v;
  }

  void preview(const uint8_t* jpeg, size_t len) override {
    sendFrame(kTypePreview, jpeg, len);
  }

  void log(const char* line) override {
    sendFrame(kTypeLog, reinterpret_cast<const uint8_t*>(line), strlen(line));
  }

  void telemetry(const char* json) override {
    sendFrame(kTypeTelemetry, reinterpret_cast<const uint8_t*>(json), strlen(json));
  }

  const char* name() const override { return "uart"; }

 private:
  static void writeU32(uint8_t* p, uint32_t v) {
    p[0] = v & 0xFF;
    p[1] = (v >> 8) & 0xFF;
    p[2] = (v >> 16) & 0xFF;
    p[3] = (v >> 24) & 0xFF;
  }

  void beginFrame(uint8_t type, size_t len) {
    uint8_t head[7] = {kSync0, kSync1, type};
    writeU32(head + 3, static_cast<uint32_t>(len));
    Serial.write(head, sizeof(head));
  }

  void endFrame(uint32_t crc) {
    crc ^= 0xFFFFFFFFu;
    uint8_t tail[4];
    writeU32(tail, crc);
    Serial.write(tail, 4);
    Serial.flush();
  }

  // USB-CDC chokes if you hand it 40 kB in one call; feed it in packet-sized
  // bites and let the stack drain between them.
  void writeChunked(const uint8_t* data, size_t len) {
    size_t off = 0;
    while (off < len) {
      const size_t n = min<size_t>(1024, len - off);
      size_t written = 0;
      while (written < n) {
        const size_t w = Serial.write(data + off + written, n - written);
        if (w == 0) delay(1);
        written += w;
      }
      off += n;
    }
  }

  void sendFrame(uint8_t type, const uint8_t* payload, size_t len) {
    const uint32_t crc = crc32Update(0xFFFFFFFFu, payload, len);
    beginFrame(type, len);
    writeChunked(payload, len);
    endFrame(crc);
  }

  int readByte(uint32_t deadline) {
    while (millis() < deadline) {
      if (Serial.available()) return Serial.read();
      delay(1);
    }
    return -1;
  }

  // Hunts for the sync word, so plaintext boot noise simply gets skipped.
  size_t readFrame(uint8_t* type, uint8_t* out, size_t cap, uint32_t timeoutMs) {
    const uint32_t deadline = millis() + timeoutMs;
    int state = 0;
    while (millis() < deadline) {
      const int b = readByte(deadline);
      if (b < 0) return 0;
      if (state == 0) {
        state = (b == kSync0) ? 1 : 0;
        continue;
      }
      if (state == 1) {
        if (b == kSync1) break;
        state = (b == kSync0) ? 1 : 0;
        continue;
      }
    }
    if (millis() >= deadline) return 0;

    const int t = readByte(deadline);
    if (t < 0) return 0;
    uint8_t lenb[4];
    for (int i = 0; i < 4; ++i) {
      const int b = readByte(deadline);
      if (b < 0) return 0;
      lenb[i] = static_cast<uint8_t>(b);
    }
    const uint32_t len = lenb[0] | (lenb[1] << 8) | (lenb[2] << 16) |
                         (static_cast<uint32_t>(lenb[3]) << 24);
    if (len > cap || len > kMaxPayload) return 0;

    for (uint32_t i = 0; i < len; ++i) {
      const int b = readByte(deadline);
      if (b < 0) return 0;
      out[i] = static_cast<uint8_t>(b);
    }
    uint8_t crcb[4];
    for (int i = 0; i < 4; ++i) {
      const int b = readByte(deadline);
      if (b < 0) return 0;
      crcb[i] = static_cast<uint8_t>(b);
    }
    const uint32_t want = crcb[0] | (crcb[1] << 8) | (crcb[2] << 16) |
                          (static_cast<uint32_t>(crcb[3]) << 24);
    uint32_t got = crc32Update(0xFFFFFFFFu, out, len) ^ 0xFFFFFFFFu;
    if (got != want) return 0;

    *type = static_cast<uint8_t>(t);
    return len;
  }

  uint8_t rx_[512];
};

UartLink g_uart;

}  // namespace

Link* makeUartLink() { return &g_uart; }

}  // namespace bb

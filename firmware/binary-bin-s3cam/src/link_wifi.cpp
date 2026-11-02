// link_wifi.cpp - POST the JPEG at the laptop, read the verdict back as JSON.
//
// Why HTTP and not raw sockets: a 40 kB SVGA JPEG is one burst, the laptop
// side is twenty lines of FastAPI, and when something breaks you can test the
// whole path with curl instead of a logic analyser.
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>

#include "link.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#warning "No secrets.h - copy include/secrets.h.example. Wi-Fi link disabled."
#define BB_WIFI_SSID ""
#define BB_WIFI_PASS ""
#define BB_HOST_IP   "0.0.0.0"
#endif

namespace bb {
namespace {

class WifiLink final : public Link {
 public:
  bool begin() override {
    if (strlen(BB_WIFI_SSID) == 0) {
      Serial.println("[wifi] no credentials compiled in");
      return false;
    }
    snprintf(url_, sizeof(url_), "http://%s:%u%s", BB_HOST_IP,
             static_cast<unsigned>(cfg::kHttpPort), cfg::kHttpPath);

    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);  // sleep adds ~200 ms of jitter to every round trip
    WiFi.begin(BB_WIFI_SSID, BB_WIFI_PASS);

    const uint32_t deadline = millis() + cfg::kWifiConnectMs;
    while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
      delay(200);
    }
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("[wifi] connect timed out");
      return false;
    }
    Serial.printf("[wifi] %s  rssi %d dBm  -> %s\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI(), url_);
    return true;
  }

  bool healthy() override { return WiFi.status() == WL_CONNECTED; }

  Verdict classify(const uint8_t* jpeg, size_t len, uint32_t seq) override {
    Verdict v;
    const uint32_t t0 = millis();

    if (!healthy()) {
      snprintf(v.error, sizeof(v.error), "wifi down");
      WiFi.reconnect();
      return v;
    }

    HTTPClient http;
    http.setTimeout(cfg::kClassifyMs);
    http.setConnectTimeout(1500);
    http.setReuse(true);
    if (!http.begin(url_)) {
      snprintf(v.error, sizeof(v.error), "begin failed");
      return v;
    }
    http.addHeader("Content-Type", "image/jpeg");
    http.addHeader("X-Device-Id", cfg::kDeviceId);
    http.addHeader("X-Seq", String(seq));

    const int code = http.POST(const_cast<uint8_t*>(jpeg), len);
    if (code != 200) {
      snprintf(v.error, sizeof(v.error), "http %d", code);
      http.end();
      return v;
    }

    JsonDocument doc;
    const DeserializationError err = deserializeJson(doc, http.getStream());
    http.end();
    if (err) {
      snprintf(v.error, sizeof(v.error), "json %s", err.c_str());
      return v;
    }

    v.ok = true;
    v.bin = static_cast<cfg::Bin>(doc["bin"] | 0);
    v.confidence = doc["confidence"] | 0.0f;
    strlcpy(v.label, doc["label"] | "?", sizeof(v.label));
    v.latencyMs = millis() - t0;
    return v;
  }

  void log(const char* line) override { Serial.printf("[log] %s\n", line); }

  void telemetry(const char* json) override {
    if (!healthy()) return;
    char url[96];
    snprintf(url, sizeof(url), "http://%s:%u/telemetry", BB_HOST_IP,
             static_cast<unsigned>(cfg::kHttpPort));
    HTTPClient http;
    http.setTimeout(800);
    if (!http.begin(url)) return;
    http.addHeader("Content-Type", "application/json");
    http.POST(json);
    http.end();
  }

  const char* name() const override { return "wifi"; }

 private:
  char url_[96] = {0};
};

WifiLink g_wifi;

}  // namespace

Link* makeWifiLink() { return &g_wifi; }

}  // namespace bb

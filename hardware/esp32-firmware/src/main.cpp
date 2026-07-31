/*
 * hardware/esp32-firmware/src/main.cpp -- ESP32 Arduino/C++ firmware for
 * the school heat-monitor. Same behavior as hardware/controller.py
 * (MicroPython), rewritten for PlatformIO's actual Arduino toolchain --
 * that file needs a separate MicroPython interpreter flash this project
 * never required; skip it and use this one instead if you're on PlatformIO.
 *
 * Behavior (as specified):
 *   - Reads the heat sensor once per second, continuously, forever.
 *   - temp < 50: no action, no connection to the AI at all.
 *   - temp > 50, the FIRST time after being normal: sends "HIGH TEMPERATURE
 *     DETECTED" to the AI exactly once. While it stays above 50 on later
 *     seconds, nothing more is sent.
 *   - temp < 50 again, after having been high: sends "Temperature
 *     normalized" exactly once, then the connection is dismissed (closed)
 *     and the device is ready to detect the next HIGH event.
 *
 * This only reports EDGES (state changes), never a running feed of every
 * reading -- that is the whole point of the state guard below.
 *
 * The server's response carries a generated "device plan" -- a short
 * LED+buzzer pattern the AI composed for this specific event (see
 * core/device_planner.py on the server), validated server-side so it can
 * never exceed sane timing bounds. runDevicePlan() plays it once the HTTP
 * response actually arrives, then everything goes silent/dark again --
 * by explicit choice (2026-07-30), the AI's rhythmic pattern is the entire
 * audible/visible experience, not bookended by an instant continuous tone
 * before and after it. This trades away the "alarm still sounds if the
 * network/AI is unreachable" safety guarantee an earlier version had.
 *
 * Pin assignments below match the actual physical wiring confirmed live
 * (2026-07-30), not the original guessed layout -- that mismatch (firmware
 * driving GPIO25/26/27 while the real wiring used GPIO4/5/19/23) was the
 * actual reason nothing visibly happened on an earlier run despite the
 * server responding 200 with a valid plan.
 */
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHTesp.h>

// ── configuration ───────────────────────────────────────────────────────────
// Credentials live in wifi_secrets.h, which is gitignored. Copy
// wifi_secrets.example.h to wifi_secrets.h and fill in your values. This
// repository is public, so hardcoding them here would publish them.
#include "wifi_secrets.h"

const char *WIFI_SSID = WIFI_SSID_VALUE;
const char *WIFI_PASSWORD = WIFI_PASSWORD_VALUE;

// Must match the machine running `uvicorn api.server:app` (its LAN IP, not
// 127.0.0.1 -- the ESP32 is a different device on the network).
const char *SERVER_URL = "http://" SERVER_HOST ":8000/api/sensor";
// Polled once per loop tick alongside the DHT read: lets the demo web page's
// buttons substitute for the dead DHT11 sensor (2026-07-30) without needing
// the fragile serial 'h'/'n' keystroke -- the ESP32 itself still fires its
// own existing sendMessage()/runDevicePlan() flow unchanged, this just
// replaces how it learns "a test event was requested."
const char *MANUAL_TRIGGER_URL = "http://" SERVER_HOST ":8000/api/sensor/manual-trigger";

// Must match VIBE_API_TOKEN in vibe_ai/.env -- the server rejects requests
// without a matching bearer token when that's set (see api/server.py's
// require_token). Leave as any placeholder if running the server locally
// with VIBE_API_TOKEN unset -- it skips the check on a localhost bind.
const char *API_TOKEN = "YOUR_VIBE_API_TOKEN";

// 30 C: chosen for demo sensitivity -- ambient here measures ~28.5 C, so a
// hand or a lighter held near the sensor crosses it immediately, without
// needing a heat source strong enough to reach 40+. Note this sits only
// ~1.5 C above ambient, so ordinary room drift can trip it; raise it back
// toward 40 for a realistic fire threshold. (The hard ceiling to respect is
// 50 C, the DHT11's own rated max, above which it cannot report reliably.)
const float TEMP_THRESHOLD_C = 30.0;
const unsigned long POLL_INTERVAL_MS = 1000;
const unsigned long WIFI_CONNECT_TIMEOUT_MS = 20000;
const unsigned long WIFI_RETRY_INTERVAL_MS = 15000;
const char *DEVICE_ID = "esp32-heat-01";

// ── warning devices ──────────────────────────────────────────────────────────
// Plain digital outputs (HIGH = active, LOW = off). Device ids in the JSON
// plan MUST match core/device_knowledge.py's manifest exactly -- that is
// what the server-side planner is allowed to target. Generic led_1/2/3
// names on purpose: colors weren't specified for these three, unlike the
// earlier red/green assumption.
const int BUZZER_PIN = 23;
// Second buzzer on its own pin rather than paralleled onto GPIO23. Sharing
// one pin made the two buzzers a single louder channel that could never be
// driven independently (and risked exceeding the pin's ~20mA budget); on a
// separate GPIO the planner can write genuine two-voice rhythms -- alternating
// beats, call-and-response, offset pulses.
const int BUZZER2_PIN = 21;
const int LED1_PIN = 4;
const int LED2_PIN = 18;  // moved off GPIO5: that's a strapping pin, confirmed live
const int LED3_PIN = 19;

// ── sensor ──────────────────────────────────────────────────────────────────
// DHT11: a digital single-wire-protocol sensor, NOT an analog LM35-style
// voltage output. The original analogRead()-based version read raw
// electrical noise off this pin and reported 330.0degC -- confirmed live.
// This pin was previously unconnected entirely (power/ground only, no
// data line wired) -- connect the DHT11's middle pin (usually labeled
// OUT/S/DATA) here.
const int DHT_PIN = 27;
DHTesp dht;

enum SensorState { NORMAL, HIGH_TEMP };
SensorState state = NORMAL;

// The alarm plays in repeated chunks rather than one fixed burst: the
// temperature is re-checked between chunks, so the alarm keeps sounding for
// exactly as long as the room stays over the threshold and stops promptly
// once it cools. 3s is long enough that the brief gap between chunks is not
// audible as an interruption, while still reacting to a drop within ~3s.
const unsigned long ALARM_CHUNK_MS = 3000;

// Last AI-generated plan, retained so it can be replayed continuously while
// the temperature stays high without re-querying the server every cycle.
JsonDocument g_planDoc;
bool g_hasPlan = false;

// True when the current alarm was started by the web button rather than by a
// real sensor crossing. Without this, a demo trigger is cancelled about a
// second later by the normalize branch, because the real sensor is reading a
// perfectly normal room. That was invisible while the DHT11 was dead (it
// returned NaN, so the sensor branch never ran at all) and only became a
// problem once the replacement sensor started reporting.
bool g_manualOverride = false;

float readTemperature() {
  float t = dht.getTemperature();
  if (isnan(t)) {
    Serial.print("DHT11 read failed (");
    Serial.print(dht.getStatusString());
    Serial.println(") -- check wiring/pull-up, treating as no reading");
    return NAN;
  }
  Serial.print("DHT11 reading: ");
  Serial.print(t);
  Serial.println(" degC");
  return t;
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);
  delay(100);

  // Scan first. This prints what the ESP32's OWN radio can see, which is the
  // only evidence that counts -- a laptop seeing the network proves nothing,
  // since the ESP32 is 2.4GHz-only and has a much weaker antenna. If the
  // target SSID is absent here, the problem is the network/band/range, not
  // the password.
  Serial.println("Scanning for networks...");
  int n = WiFi.scanNetworks();
  if (n <= 0) {
    Serial.println("  (no networks visible to the ESP32 at all)");
  } else {
    for (int i = 0; i < n; i++) {
      Serial.print("  ["); Serial.print(i); Serial.print("] '");
      Serial.print(WiFi.SSID(i));
      Serial.print("'  rssi="); Serial.print(WiFi.RSSI(i));
      Serial.print("  ch="); Serial.println(WiFi.channel(i));
    }
  }
  WiFi.scanDelete();

  Serial.print("Connecting to '"); Serial.print(WIFI_SSID); Serial.println("'");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  // Bounded, NOT infinite. The previous version spun here forever, which
  // meant any WiFi problem ALSO silently blocked every DHT read and the
  // whole of loop() from ever running -- confirmed live (2026-07-31) while
  // trying to test a replaced sensor. Now it gives up, lets loop() run (the
  // sensor is perfectly readable with no network), and retries in the
  // background from loop().
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_CONNECT_TIMEOUT_MS) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi connected: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.print("WiFi FAILED -- status=");
    Serial.println(WiFi.status());
    Serial.println("  (1 = SSID not found, 4 = wrong password, 6 = disconnected)");
    Serial.println("Continuing anyway: sensor reads still work without network.");
  }
}

// Plays a server-generated {"device_id": [{"on_ms","off_ms"}, ...]} pattern
// across however many devices it names, ALL running at once (a shared tick
// loop, not one device finishing before the next starts) so a combined
// buzzer+LED plan reads as one warning sign rather than several in
// sequence. Once finished, everything goes off -- by explicit choice, the
// AI's rhythmic pattern is the entire audible/visible experience, not
// sandwiched between an instant continuous tone before and after it.
void runDevicePlan(JsonObject plan, unsigned long minDurationMs) {
  struct Step { unsigned long onMs, offMs; };
  // loopStart: index to rewind to when a pattern repeats. The planner gives
  // each device an optional lead-in step with on_ms == 0, whose only job is
  // to delay that device's FIRST pulse so the LEDs fall into a staggered
  // chase. It is a one-time phase offset, so repeating it every cycle would
  // re-insert its dead time forever -- and since the stagger grows per device
  // (led_1 ~200ms, led_2 ~400ms, led_3 ~600ms), the last LED suffered most
  // and looked like it was barely firing. Confirmed live 2026-07-31.
  struct Timeline { int pin; Step steps[8]; int count; int idx; int loopStart; unsigned long until; };

  Timeline timelines[5];  // 2 buzzers + 3 LEDs, max
  int deviceCount = 0;

  auto addDevice = [&](const char *name, int pin) {
    if (!plan.containsKey(name)) {
      Serial.print("  plan has no entry for "); Serial.println(name);
      return;
    }
    JsonArray steps = plan[name].as<JsonArray>();
    if (steps.isNull() || steps.size() == 0) {
      Serial.print("  "); Serial.print(name); Serial.println(" entry present but empty/not an array");
      return;
    }
    Timeline &t = timelines[deviceCount];
    t.pin = pin;
    t.count = 0;
    t.idx = 0;
    t.loopStart = 0;
    for (JsonObject step : steps) {
      if (t.count >= 8) break;
      unsigned long onMs = step["on_ms"] | 200;
      unsigned long offMs = step["off_ms"] | 200;
      // A leading on_ms == 0 is the stagger offset, not a real pulse: play it
      // once to set this device's phase, then loop past it. Detected on the
      // raw value, before the max(1UL, ...) floor below rewrites it to 1ms.
      if (t.count == 0 && onMs == 0) t.loopStart = 1;
      t.steps[t.count++] = { max(1UL, onMs), max(1UL, offMs) };
    }
    // Degenerate case: a device whose ONLY step is the offset has nothing
    // left to loop, so fall back to replaying the whole thing rather than
    // rewinding past the end and stalling the device permanently.
    if (t.loopStart >= t.count) t.loopStart = 0;
    if (t.count > 0) {
      Serial.print("  "); Serial.print(name); Serial.print(" on pin "); Serial.print(pin);
      Serial.print(": "); Serial.print(t.count); Serial.println(" steps queued");
      deviceCount++;
    }
  };

  Serial.println("runDevicePlan: checking plan contents...");
  addDevice("buzzer", BUZZER_PIN);
  addDevice("buzzer_2", BUZZER2_PIN);
  addDevice("led_1", LED1_PIN);
  addDevice("led_2", LED2_PIN);
  addDevice("led_3", LED3_PIN);

  if (deviceCount == 0) {
    Serial.println("runDevicePlan: no usable devices in plan -- nothing will run");
    return;
  }
  Serial.print("runDevicePlan: running "); Serial.print(deviceCount); Serial.println(" device(s) now");

  unsigned long now = millis();
  unsigned long totalMs = 0;
  for (int i = 0; i < deviceCount; i++) {
    timelines[i].until = now;
    unsigned long sum = 0;
    for (int s = 0; s < timelines[i].count; s++) sum += timelines[i].steps[s].onMs + timelines[i].steps[s].offMs;
    totalMs = max(totalMs, sum);
  }
  // A single pass through the model's pattern is often under 2 seconds --
  // too brief to actually register as a rhythm to a human watching. Now
  // that each device's own sequence loops (below), stretch the shared
  // window to a minimum so the pattern repeats enough times to be seen.
  totalMs = max(totalMs, minDurationMs);
  unsigned long end = now + totalMs;

  // Each step alternates on/off; track which half of the step we're in.
  bool onHalf[5] = { true, true, true, true, true };

  while ((long)(end - millis()) > 0) {
    unsigned long t = millis();
    for (int i = 0; i < deviceCount; i++) {
      Timeline &tl = timelines[i];
      if ((long)(t - tl.until) >= 0) {
        Step &s = tl.steps[tl.idx];
        if (onHalf[i]) {
          digitalWrite(tl.pin, HIGH);
          tl.until = t + s.onMs;
          onHalf[i] = false;
        } else {
          digitalWrite(tl.pin, LOW);
          tl.until = t + s.offMs;
          onHalf[i] = true;
          tl.idx++;
          // Loop this device's own pattern rather than going dark once its
          // (usually shorter) sequence finishes -- devices don't all sum to
          // the same total duration, and the shared window runs as long as
          // the longest one. Confirmed live: without this, LEDs with a
          // shorter total than the buzzer's pattern went dark for the back
          // half of every alarm burst while the buzzer kept going alone.
          if (tl.idx >= tl.count) tl.idx = tl.loopStart;
        }
      }
    }
    delay(10);
  }

  digitalWrite(LED1_PIN, LOW);
  digitalWrite(LED2_PIN, LOW);
  digitalWrite(LED3_PIN, LOW);
  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(BUZZER2_PIN, LOW);
}

// One request, one connection, then closed -- matching hardware/controller.py's
// same "connection dismissed" guarantee: no socket sits open between events.
void sendMessage(const char *message, float temperature) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Send failed: WiFi not connected");
    return;
  }

  HTTPClient http;
  http.begin(SERVER_URL);
  // Default HTTPClient timeout (5s) is too short: /api/sensor runs a real
  // Manager/Council reasoning call plus device-plan generation, observed
  // taking 5-10+ seconds in testing. Confirmed live -- without this, the
  // ESP32 got HTTPC_ERROR_READ_TIMEOUT (-11) even though the server had
  // already accepted the request and was still working on it.
  http.setTimeout(30000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + API_TOKEN);

  JsonDocument body;
  body["message"] = message;
  body["temperature"] = temperature;
  body["device_id"] = DEVICE_ID;
  String payload;
  serializeJson(body, payload);

  int status = http.POST(payload);
  Serial.print("Sent: ");
  Serial.print(message);
  Serial.print(" -> HTTP ");
  Serial.println(status);

  if (status == 200) {
    String responseBody = http.getString();
    Serial.print("Response body length: ");
    Serial.println(responseBody.length());

    JsonDocument response;
    DeserializationError err = deserializeJson(response, responseBody);
    if (err) {
      Serial.print("JSON parse failed: ");
      Serial.println(err.c_str());
    } else if (!response["device_plan"].is<JsonObject>()) {
      Serial.println("Parsed OK, but response has no usable device_plan object");
    } else {
      // Keep the plan so loop() can go on replaying it for as long as the
      // temperature stays high, instead of the alarm falling silent after a
      // single burst while the room is still overheating.
      g_planDoc.set(response["device_plan"]);
      g_hasPlan = true;
      runDevicePlan(g_planDoc.as<JsonObject>(), ALARM_CHUNK_MS);
    }
  }

  http.end();  // releases the connection immediately
}

// Polls the server for a pending web-button trigger (see MANUAL_TRIGGER_URL
// above). A fast, plain in-memory lookup server-side -- no AI call happens
// here, so this stays quick even though it runs every loop tick, unlike
// sendMessage()'s real /api/sensor call.
void checkManualWebTrigger() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("checkManualWebTrigger: WiFi not connected, skipping poll");
    return;
  }

  HTTPClient http;
  http.begin(MANUAL_TRIGGER_URL);
  http.setTimeout(3000);
  int status = http.GET();
  if (status != 200) {
    Serial.print("checkManualWebTrigger: poll failed, HTTP ");
    Serial.println(status);
  } else {
    String body = http.getString();
    JsonDocument response;
    DeserializationError err = deserializeJson(response, body);
    if (err) {
      Serial.print("checkManualWebTrigger: JSON parse failed: ");
      Serial.println(err.c_str());
    } else {
      // NO state guard here, unlike the real-sensor path below. The sensor
      // path needs one (it polls every second and must only report EDGES,
      // not spam every reading). A web button press IS the edge -- it only
      // happens when a human deliberately clicks. Guarding it on state made
      // a second "high" click silently do nothing whenever state was
      // already HIGH_TEMP, which is exactly what happened live (2026-07-31):
      // the server log showed the board consuming the flag every time but
      // never once calling /api/sensor afterwards.
      const char *event = response["event"];
      if (event && strcmp(event, "high") == 0) {
        Serial.println("Web trigger: simulating HIGH TEMPERATURE DETECTED");
        sendMessage("HIGH TEMPERATURE DETECTED", 55.0);
        state = HIGH_TEMP;
        g_manualOverride = true;   // survive the real sensor reading normal
      } else if (event && strcmp(event, "normal") == 0) {
        Serial.println("Web trigger: simulating Temperature normalized");
        sendMessage("Temperature normalized", 25.0);
        state = NORMAL;
        g_hasPlan = false;         // stop the sustained alarm
        g_manualOverride = false;
      }
    }
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(BUZZER2_PIN, OUTPUT);
  pinMode(LED1_PIN, OUTPUT);
  pinMode(LED2_PIN, OUTPUT);
  pinMode(LED3_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(BUZZER2_PIN, LOW);
  digitalWrite(LED1_PIN, LOW);
  digitalWrite(LED2_PIN, LOW);
  digitalWrite(LED3_PIN, LOW);

  // Idle-line check before handing the pin to the DHT driver. An idle DHT11
  // holds its data line HIGH via the pull-up; it only pulls LOW to transmit.
  // So a steady LOW here means the line is shorted to ground, plugged into
  // the wrong breadboard row, or the pull-up is missing -- i.e. a wiring
  // fault -- whereas HIGH means wiring is sound and any later TIMEOUT points
  // at the sensor or the GPIO itself. Added after a REPLACEMENT sensor
  // failed identically to the original (2026-07-31), which makes two dead
  // units unlikely and the shared wiring the prime suspect.
  pinMode(DHT_PIN, INPUT_PULLUP);
  delay(50);
  Serial.print("DHT data line idle state on GPIO");
  Serial.print(DHT_PIN);
  Serial.print(" = ");
  Serial.println(digitalRead(DHT_PIN) ? "HIGH (wiring looks sound)"
                                      : "LOW (WIRING FAULT: shorted/miswired/no pull-up)");

  // Manual DHT11 handshake probe. Protocol: the master pulls the line LOW
  // for >=18ms and releases; a healthy sensor answers within ~40us by
  // pulling the line LOW itself for ~80us. Doing this by hand rather than
  // through the library separates two faults the library reports
  // identically as "TIMEOUT":
  //   line never rises after release -> stuck LOW, wiring/short problem
  //   rises but nothing answers      -> sensor absent, dead, or its DATA
  //                                     wire is not actually making contact
  // Needed because the earlier idle-HIGH check used the ESP32's INTERNAL
  // pull-up, which reads HIGH even on a totally disconnected pin -- so it
  // could not distinguish "wired correctly" from "not wired at all".
  Serial.println("DHT handshake probe:");
  pinMode(DHT_PIN, OUTPUT);
  digitalWrite(DHT_PIN, LOW);
  delay(20);
  pinMode(DHT_PIN, INPUT_PULLUP);

  bool rose = false;
  for (int i = 0; i < 200; i++) {
    if (digitalRead(DHT_PIN)) { rose = true; break; }
    delayMicroseconds(1);
  }
  bool answered = false;
  for (int i = 0; i < 1000; i++) {
    if (!digitalRead(DHT_PIN)) { answered = true; break; }
    delayMicroseconds(1);
  }
  Serial.print("  line released HIGH: ");
  Serial.println(rose ? "yes" : "NO (stuck LOW -- short or miswire)");
  Serial.print("  sensor answered:    ");
  Serial.println(answered ? "YES (sensor is alive and wired)"
                          : "NO (no response -- dead sensor, or DATA wire not connected)");

  dht.setup(DHT_PIN, DHTesp::DHT11);

  // Self-test: unconditionally exercises every warning device on boot, with
  // zero dependency on WiFi, the server, or JSON parsing. If this doesn't
  // visibly/audibly happen, the problem is wiring -- not the device-plan
  // logic, which hasn't run at all yet at this point in setup().
  Serial.println("Self-test: both buzzers + all 3 LEDs, one at a time...");
  digitalWrite(BUZZER_PIN, HIGH); delay(300); digitalWrite(BUZZER_PIN, LOW); delay(200);
  digitalWrite(BUZZER2_PIN, HIGH); delay(300); digitalWrite(BUZZER2_PIN, LOW); delay(200);
  digitalWrite(LED1_PIN, HIGH); delay(300); digitalWrite(LED1_PIN, LOW); delay(200);
  digitalWrite(LED2_PIN, HIGH); delay(300); digitalWrite(LED2_PIN, LOW); delay(200);
  digitalWrite(LED3_PIN, HIGH); delay(300); digitalWrite(LED3_PIN, LOW);
  Serial.println("Self-test done.");

  connectWiFi();

  // Starts assuming a normal reading. If the sensor is already above the
  // threshold at boot, the first loop iteration still correctly fires the
  // HIGH message exactly once, since state starts as NORMAL here.
  state = NORMAL;
}

void loop() {
  // Manual test trigger over serial: sensor hardware is currently unreachable
  // (confirmed dead/unpowered after exhausting every other wiring/library
  // cause), so this lets the rest of the pipeline -- HTTP round trip, AI
  // device-plan generation, LED/buzzer playback -- be proven and demoed
  // without depending on it. Type 'h' + Enter for a fake HIGH TEMPERATURE
  // event, 'n' + Enter to normalize. Real sensor readings below still take
  // priority whenever they succeed.
  if (Serial.available()) {
    char c = Serial.read();
    if (c == 'h' && state != HIGH_TEMP) {
      Serial.println("Manual trigger: simulating HIGH TEMPERATURE DETECTED");
      sendMessage("HIGH TEMPERATURE DETECTED", 55.0);
      state = HIGH_TEMP;
      g_manualOverride = true;
    } else if (c == 'n' && state == HIGH_TEMP) {
      Serial.println("Manual trigger: simulating Temperature normalized");
      sendMessage("Temperature normalized", 25.0);
      state = NORMAL;
      g_hasPlan = false;
      g_manualOverride = false;
    }
  }

  checkManualWebTrigger();

  // Background WiFi retry -- deliberately non-blocking. A dropped or failed
  // connection must never stop the sensor loop again (see connectWiFi()).
  static unsigned long lastWifiRetry = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiRetry > WIFI_RETRY_INTERVAL_MS) {
    lastWifiRetry = millis();
    Serial.print("WiFi not connected (status=");
    Serial.print(WiFi.status());
    Serial.println(") -- retrying in background");
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }

  float temp = readTemperature();

  if (!isnan(temp)) {
    if (temp > TEMP_THRESHOLD_C && state != HIGH_TEMP) {
      sendMessage("HIGH TEMPERATURE DETECTED", temp);
      state = HIGH_TEMP;
    } else if (temp < TEMP_THRESHOLD_C && state == HIGH_TEMP && !g_manualOverride) {
      sendMessage("Temperature normalized", temp);
      state = NORMAL;
      g_hasPlan = false;   // stop the sustained alarm
    }
    // Otherwise: either temp below threshold while already normal (do
    // nothing, per spec), or still above while already high -- handled by
    // the sustain block below rather than by re-sending.
  }

  // Sustain: while the room stays over the threshold, keep replaying the
  // AI's pattern. Previously the alarm played one burst and then went
  // silent even though the temperature was still climbing, which is wrong
  // for a fire alarm -- the danger has not passed just because the pattern
  // finished. Replaying in chunks (rather than one long run) means the
  // temperature is re-checked every ~3s, so it also stops promptly on cool
  // down. Returning early skips the idle poll delay below, which would
  // otherwise punch a 1s silence into the middle of the alarm.
  if (state == HIGH_TEMP && g_hasPlan) {
    runDevicePlan(g_planDoc.as<JsonObject>(), ALARM_CHUNK_MS);
    return;
  }

  // DHT11 is a slow sensor (spec'd ~1 reading/sec max) -- this interval
  // already matches that, no separate rate limit needed.
  delay(POLL_INTERVAL_MS);
}

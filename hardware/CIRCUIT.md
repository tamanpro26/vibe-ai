# AI Fire-Alert System — Circuit & Architecture Reference

Source material for the exhibition poster. Every pin, threshold, and timing
below is taken from the shipped firmware and backend, not from a plan — see
"Verification" at the end for where each value lives in code.

---

## 1. One-line summary

An ESP32 reads a temperature sensor once per second. When the room crosses
30 °C it calls a multi-agent AI backend, which **composes an alarm rhythm
specific to that temperature**, and the ESP32 plays it across two buzzers and
three LEDs while also pushing a notification to subscribed phones.

The AI is not selecting a preset. It writes the timing pattern itself, so a
31 °C alert and a 55 °C alert sound audibly different.

---

## 2. Bill of materials

| # | Component | Notes |
| --- | --- | --- |
| 1 | ESP32 DevKit (esp32dev, CH9102 USB-serial) | 2.4 GHz WiFi only — will not join a 5 GHz network |
| 1 | DHT11 temperature/humidity sensor | Digital single-wire protocol, **not** analog |
| 1 | 10 kΩ resistor | Pull-up on the DHT11 data line — **required** |
| 2 | Buzzers | Independently driven on separate GPIOs |
| 3 | LEDs | Warning indicators |
| 3 | Current-limiting resistors | One per LED |
| 1 | Breadboard + jumper wires | |
| 1 | USB cable | Power + programming |

---

## 3. Pin map

| Device | ESP32 pin | Direction | Behaviour |
| --- | --- | --- | --- |
| DHT11 DATA | **GPIO27** | bidirectional | Single-wire protocol, 10 kΩ pull-up to 3V3 |
| Buzzer 1 | **GPIO23** | output | HIGH = sound |
| Buzzer 2 | **GPIO21** | output | HIGH = sound, independently timed from buzzer 1 |
| LED 1 | **GPIO4** | output | HIGH = lit |
| LED 2 | **GPIO18** | output | HIGH = lit |
| LED 3 | **GPIO19** | output | HIGH = lit |

**Power rails:** USB → ESP32 → `3V3` pin → breadboard **+** rail;
`GND` pin → breadboard **−** rail. Every component's ground returns to the
− rail.

### Why these pins specifically

Not arbitrary — each was chosen or moved for a concrete reason:

- **GPIO18 instead of GPIO5** for LED 2. GPIO5 is a *strapping pin*: its
  level at power-on helps select the ESP32's boot mode, so driving it can
  interfere with booting.
- **GPIO21 for buzzer 2.** Both buzzers were briefly wired to GPIO23 in
  parallel. That makes them electrically one device — they can only ever
  sound identically — and two buzzers on one pin risks exceeding the ~20 mA
  per-pin current rating. Separating them is what allows interleaved,
  two-channel rhythms.
- **GPIO27 for the DHT11**, freed when buzzer 1 moved to GPIO23.
- **Avoided:** GPIO34–39 are *input-only* and cannot drive the DHT11's
  bidirectional line; GPIO0/2/12/15 are strapping pins.

### The 10 kΩ pull-up is not optional

The DHT11 signals by pulling the data line **low**; the line must be held
**high** at rest. Without the pull-up the line floats and every read fails.

Wire it between **DATA and VCC (+)**, never DATA and GND — a resistor to
ground is a pull-*down*, which clamps the line low permanently and produces
exactly the same "sensor not responding" symptom. This was the actual root
cause of a long debugging session on this build.

---

## 4. Signal flow (poster diagram)

```
┌──────────────┐   1-wire    ┌──────────────────┐
│   DHT11      │────────────▶│                  │
│  (GPIO27)    │  temp °C    │      ESP32       │
└──────────────┘             │  reads every 1 s │
                             └────────┬─────────┘
                                      │  crosses 30 °C?
                                      │  (edge only — once per crossing)
                                      ▼
                             ┌──────────────────┐
                             │  WiFi  ·  HTTP   │
                             │  POST /api/sensor│
                             │  { message, °C } │
                             └────────┬─────────┘
                                      ▼
              ┌───────────────────────────────────────────┐
              │        VibeAI backend (FastAPI)           │
              │                                           │
              │  device_knowledge.py ── retrieves the      │
              │     device manifest (RAG grounding)        │
              │              ↓                             │
              │  device_planner.py  ── LLM composes a      │
              │     rhythm scaled to the reading           │
              │              ↓                             │
              │  validate_plan()    ── SAFETY CLAMP        │
              │     timing bounds, step cap, unknown       │
              │     devices dropped                        │
              └───────┬───────────────────────┬───────────┘
                      │ device_plan (JSON)    │ (background)
                      ▼                       ▼
        ┌──────────────────────────┐   ┌──────────────────┐
        │  ESP32 plays the plan     │   │ Manager/Council  │
        │  buzzer  (GPIO23)         │   │ reasoning  +     │
        │  buzzer_2(GPIO21)         │   │ Web Push alert   │
        │  led_1   (GPIO4)          │   │ to phones        │
        │  led_2   (GPIO18)         │   └──────────────────┘
        │  led_3   (GPIO19)         │
        │  repeats while still hot  │
        └──────────────────────────┘
```

---

## 5. Behaviour

### Trigger logic
- Sensor polled every **1 s**.
- Threshold **30 °C**. Only **edges** are reported — crossing up sends
  `HIGH TEMPERATURE DETECTED` once, crossing down sends
  `Temperature normalized` once. Staying hot does not spam the server.
- 30 °C is a demo-sensitivity value (ambient ≈ 28.5 °C, so a hand triggers
  it). The DHT11's rated ceiling is 50 °C, so it cannot report reliably
  above that.

### Alarm
- Plays in **3 s chunks**, re-reading the temperature between each, so it
  sustains for as long as the room stays hot and stops within ~3 s of cooling.
- All five devices run on a **shared tick loop**, so the pattern reads as one
  combined signal rather than devices taking turns.
- LEDs can be staggered into a rotating "chase" using a first step of
  `on_ms: 0`, which delays that device's start.

### How the AI encodes temperature
The generated rhythm carries the reading, so it can be *heard*:

| Reading | Character |
| --- | --- |
| 30–36 °C | Unhurried — long gaps, few steps. Deliberately not an emergency |
| 36–45 °C | Steady, insistent, mid-tempo |
| 45 °C+ | Rapid, dense, minimum gaps — unmistakably urgent |

### Safety validation
The LLM's output drives real hardware, so it is never trusted directly.
`validate_plan()` runs on every response regardless of what the model said:

- Unknown device IDs are **dropped**
- Step timings **clamped** to 50–2000 ms
- Max **8 steps** per device
- Buzzer `on_ms` capped at **300 ms** (longer reads as one flat tone, not a
  rhythm) with a **200 ms minimum gap** (a piezo's ring-out otherwise blurs
  consecutive beeps into a continuous drone)
- Any malformed response falls back to a fixed safe pattern — the alarm
  always fires, even if generation fails entirely

---

## 6. Notification path

`POST /api/sensor` returns the device plan **immediately**, then finishes the
Manager reasoning and Web Push broadcast in a background task.

This ordering matters: previously the device waited for the full multi-agent
reasoning chain — about **14 seconds** — before it could make a sound, even
though it never reads that text. Alarm start-up latency is now **1–2 s**.

Phones receive a Web Push notification. Push requires a one-time per-device
opt-in (an OS-level requirement on both Android and iOS — no app or website
can notify a device that never subscribed).

---

## 7. Failure handling built into the firmware

| Failure | Behaviour |
| --- | --- |
| WiFi down / wrong password | Non-blocking: scans, reports a status code, retries in the background. Sensor keeps reading — a network fault must never look like a dead sensor |
| Server unreachable | Logs the HTTP error, keeps polling |
| Sensor read fails | Reports the reason and skips that cycle; no false alarm |
| AI response malformed | Falls back to the fixed safe pattern |
| No heat source available | Web/serial manual trigger fires the identical pipeline |

---

## 8. Poster talking points

1. **The AI writes the alarm, it does not pick one.** Same event at two
   temperatures produces two audibly different rhythms.
2. **The rhythm is the data.** Urgency scales continuously with the reading,
   so the alarm communicates severity to anyone in earshot — no screen needed.
3. **A hard safety boundary sits between the LLM and the hardware.** Every
   generated plan is clamped before a single pin is driven.
4. **It degrades gracefully.** Network, sensor, and AI failures each have a
   defined fallback; the alarm still sounds.
5. **Reaches beyond the room.** Local buzzers/LEDs for anyone present, Web
   Push for anyone who subscribed.

---

## 9. Verification

Values in this document trace to:

| Value | Source |
| --- | --- |
| Pin assignments, threshold, chunk timing | `hardware/esp32-firmware/src/main.cpp` |
| Device manifest given to the AI | `core/device_knowledge.py` |
| Rhythm rules, temperature bands, safety clamps | `core/device_planner.py` |
| Endpoint, background reasoning, push broadcast | `api/server.py` |

WiFi credentials and the server IP live in a **gitignored**
`hardware/esp32-firmware/src/wifi_secrets.h` (template:
`wifi_secrets.example.h`) — this repository is public.

"""
hardware/controller.py -- ESP32 MicroPython firmware for the school
heat-monitor.

Behavior (as specified):
  - Reads the heat sensor once per second, continuously, forever.
  - temp < 50: no action, no connection to the AI at all.
  - temp > 50, the FIRST time after being normal: sounds the buzzer
    immediately (local, no network round trip needed to react) and sends
    "HIGH TEMPERATURE DETECTED" to the AI exactly once. While it stays
    above 50 on later seconds, nothing more is sent.
  - temp < 50 again, after having been high: silences the buzzer and sends
    "Temperature normalized" exactly once, then the connection is
    dismissed (closed) and the device is ready to detect the next HIGH
    event.

This only reports EDGES (state changes), never a running feed of every
reading -- that is the whole point of the state guard below.

The buzzer's instant on/off is driven locally (a plain GPIO write),
independent of the network call: it should still sound even if WiFi or
the server is down at the exact moment of the event -- an alarm that
depends on a working internet connection to go off is worse than a dumb
wired one.

On TOP of that instant reaction, the server's response to send_message()
carries a generated "device plan" -- a short LED+buzzer pattern the AI
composed for this specific event (see core/device_planner.py), server-side
validated so it can never exceed sane timing bounds. run_device_plan()
below plays it once the network round trip actually completes, then
restores the buzzer to whatever its instant base state should be (still
sounding if still HIGH, silent if normalized) -- the generated pattern is
additive polish, never a replacement for the instant reaction.

Networking: urequests.post() opens one plain HTTP connection for the
duration of a single request and closes it immediately after
response.close() -- there is no persistent/kept-open connection between
events. That is what "connection dismissed" means here: no socket sits
open while temp<50 or while temp stays >50 between the two edges.

Install urequests first if it is not already on the device:
    import mip; mip.install("urequests")
(needs WiFi already up once, e.g. via Thonny's "Manage packages" first).
"""
import network
import time
from machine import ADC, Pin
import urequests

# ── configuration -- fill these in for your setup ─────────────────────────
WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

# Must match the machine running `uvicorn api.server:app` (its LAN IP, not
# 127.0.0.1 -- the ESP32 is a different device on the network).
SERVER_URL = "http://<vibeai-server-ip>:8000/api/sensor"

# Must match VIBE_API_TOKEN in vibe_ai/.env -- the server rejects requests
# without a matching bearer token (see api/server.py's require_token).
API_TOKEN = "YOUR_VIBE_API_TOKEN"

TEMP_THRESHOLD_C = 50
POLL_INTERVAL_S = 1
DEVICE_ID = "esp32-heat-01"

# ── warning devices ──────────────────────────────────────────────────────────
# All three are plain digital outputs (HIGH = active, LOW = off). Wire an
# active buzzer/LED directly to its pin, or a relay module's IN pin for
# something louder/brighter -- this firmware only ever sets a pin high or
# low, never anything driver-specific. Device ids here MUST match
# core/device_knowledge.py's manifest exactly; that is what the server-side
# planner is allowed to target.
BUZZER_PIN = 27
LED_RED_PIN = 25
LED_GREEN_PIN = 26

buzzer = Pin(BUZZER_PIN, Pin.OUT)
led_red = Pin(LED_RED_PIN, Pin.OUT)
led_green = Pin(LED_GREEN_PIN, Pin.OUT)
for _pin in (buzzer, led_red, led_green):
    _pin.value(0)  # everything off at boot

DEVICE_PINS = {"buzzer": buzzer, "led_red": led_red, "led_green": led_green}

# ── sensor ──────────────────────────────────────────────────────────────────
# Analog heat sensor (LM35-style: 10 mV per °C, 0 V = 0 °C) on GPIO34, an
# input-only ADC pin on most ESP32 boards. Swap read_temperature()'s body
# for a DS18B20/DHT11 driver if a different sensor is wired up -- nothing
# else in this file needs to change, since the state machine below only
# ever needs a numeric Celsius value back from this function.
sensor = ADC(Pin(34))
sensor.atten(ADC.ATTN_11DB)   # full 0-3.3V input range
sensor.width(ADC.WIDTH_12BIT)  # 0-4095 counts


def read_temperature():
    raw = sensor.read()                # 0-4095
    voltage = raw * 3.3 / 4095
    return voltage * 100                # LM35: 10 mV/°C -> volts * 100 = °C


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to WiFi...")
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        while not wlan.isconnected():
            time.sleep(0.5)
    print("WiFi connected:", wlan.ifconfig()[0])


def run_device_plan(plan, base_buzzer_state):
    """
    Plays a server-generated {"device_id": [{"on_ms", "off_ms"}, ...]}
    pattern across however many devices it names, all running at once
    (a shared tick loop, not one device fully finishing before the next
    starts) so a "buzzer + red LED" plan actually reads as one combined
    warning sign rather than two separate ones back to back.

    Once the pattern finishes, every LED goes off and the buzzer is
    restored to base_buzzer_state -- the caller's own instant on/off
    decision from the edge-detection below, which this must never override
    (a plan that finishes mid-HIGH should leave the alarm still sounding).
    """
    if not plan:
        return

    timelines = {}
    for device_id, steps in plan.items():
        pin = DEVICE_PINS.get(device_id)
        if pin is None or not steps:
            continue
        timeline = []
        for step in steps:
            timeline.append((max(1, step.get("on_ms", 200)), True))
            timeline.append((max(1, step.get("off_ms", 200)), False))
        timelines[device_id] = timeline

    if not timelines:
        return

    now = time.ticks_ms()
    cursors = {d: {"idx": 0, "until": now} for d in timelines}
    total_ms = max(sum(dur for dur, _ in tl) for tl in timelines.values())
    end = time.ticks_add(now, total_ms)

    while time.ticks_diff(end, time.ticks_ms()) > 0:
        t = time.ticks_ms()
        for device_id, timeline in timelines.items():
            cur = cursors[device_id]
            if cur["idx"] >= len(timeline):
                continue
            if time.ticks_diff(t, cur["until"]) >= 0:
                duration, is_on = timeline[cur["idx"]]
                DEVICE_PINS[device_id].value(1 if is_on else 0)
                cur["until"] = time.ticks_add(t, duration)
                cur["idx"] += 1
        time.sleep_ms(10)

    led_red.value(0)
    led_green.value(0)
    buzzer.value(1 if base_buzzer_state else 0)


def send_message(message, temperature):
    """
    One request, one connection, then closed -- see module docstring.
    Returns the server's device_plan (a dict, possibly empty) so main()
    can play it; returns None on any failure so main() knows there is
    nothing to run.
    """
    try:
        response = urequests.post(
            SERVER_URL,
            json={
                "message": message,
                "temperature": temperature,
                "device_id": DEVICE_ID,
            },
            headers={
                "Authorization": "Bearer " + API_TOKEN,
                "Content-Type": "application/json",
            },
        )
        print("Sent:", message, "-> HTTP", response.status_code)
        plan = None
        if response.status_code == 200:
            try:
                plan = response.json().get("device_plan")
            except Exception as e:
                print("device_plan parse failed:", e)
        response.close()   # releases the socket immediately
        return plan
    except Exception as e:
        print("Send failed:", e)
        return None


def main():
    connect_wifi()

    # Starts assuming a normal reading. If the sensor is already above the
    # threshold at boot, the first loop iteration still correctly fires the
    # HIGH message exactly once, since state starts as "normal" here.
    state = "normal"

    while True:
        temp = read_temperature()

        if temp > TEMP_THRESHOLD_C and state != "high":
            buzzer.value(1)
            plan = send_message("HIGH TEMPERATURE DETECTED", temp)
            state = "high"
            run_device_plan(plan, base_buzzer_state=True)
        elif temp < TEMP_THRESHOLD_C and state == "high":
            buzzer.value(0)
            plan = send_message("Temperature normalized", temp)
            state = "normal"
            run_device_plan(plan, base_buzzer_state=False)
        # Otherwise: either temp < 50 while already normal (do nothing, per
        # spec), temp > 50 while already high (already sent, stay silent),
        # or temp == 50 exactly (neither edge fires).

        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()

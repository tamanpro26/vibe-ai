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

The buzzer is driven locally (a plain GPIO write), independent of the
network call: it should still sound even if WiFi or the server is down at
the exact moment of the event -- an alarm that depends on a working
internet connection to go off is worse than a dumb wired one.

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

# ── buzzer / alarm ──────────────────────────────────────────────────────────
# A plain digital output: HIGH sounds the alarm, LOW silences it. Wire an
# active buzzer directly to this pin (its own driver just needs the GPIO
# signal), or a relay module's IN pin if driving something louder (siren,
# strobe) -- this firmware doesn't need to know which; it only ever sets a
# pin high or low.
BUZZER_PIN = 27
buzzer = Pin(BUZZER_PIN, Pin.OUT)
buzzer.value(0)  # off at boot

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


def send_message(message, temperature):
    """One request, one connection, then closed -- see module docstring."""
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
        response.close()   # releases the socket immediately
    except Exception as e:
        print("Send failed:", e)


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
            send_message("HIGH TEMPERATURE DETECTED", temp)
            state = "high"
        elif temp < TEMP_THRESHOLD_C and state == "high":
            buzzer.value(0)
            send_message("Temperature normalized", temp)
            state = "normal"
        # Otherwise: either temp < 50 while already normal (do nothing, per
        # spec), temp > 50 while already high (already sent, stay silent),
        # or temp == 50 exactly (neither edge fires).

        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()

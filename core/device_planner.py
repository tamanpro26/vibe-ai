"""
core/device_planner.py -- generates a validated LED/buzzer "warning sign"
pattern for a sensor event, grounded in core/device_knowledge.py.

Deliberately calls ONE model directly (models.registry), never
manager.handle_user_request(): this is not part of the website's Manager/
Team/Council pipeline, by explicit design -- a separate, narrower
capability for the hardware demo only, matching how core/device_knowledge.py
is kept apart from core/collective_memory.py for the same reason.

The output of this module drives REAL hardware timing on a physical
device. An LLM is free to hallucinate "buzz for 30 seconds straight" or
return malformed JSON -- validate_plan() is the hard safety net that runs
regardless of what the model said, not an optional nicety.
"""
from __future__ import annotations

import json
import re

from loguru import logger

from core.device_knowledge import all_controllable_devices, retrieve_device_context

PLANNER_MODEL = "llama33_70b_memory"  # Groq llama-3.3-70b-versatile, direct call

MAX_STEPS = 8
MIN_MS = 50
MAX_MS = 2000
BUZZER_MAX_ON_MS = 300  # short punchy pulses only -- a long sustained tone reads as one flat beep, not a rhythm
# Confirmed live: even with on_ms capped short, an off_ms this brief lets a
# cheap piezo's ring-out/decay bleed into the next pulse, so back-to-back
# short beeps still sound like one continuous tone with no real silence
# between them. A longer floor gives the buzzer time to actually go quiet.
BUZZER_MIN_OFF_MS = 200

# Safe, deterministic pattern used whenever generation fails or returns
# something unusable -- the device must always get SOME plan, never a
# silent gap, since a bare exception here should not also mean "the LED
# does nothing."
FALLBACK_PLANS = {
    "high": {
        # Short-short-medium siren cadence, not a flat repeated beep -- every
        # on_ms stays under BUZZER_MAX_ON_MS, no long sustained tone.
        "buzzer": [
            {"on_ms": 100, "off_ms": 200},
            {"on_ms": 100, "off_ms": 200},
            {"on_ms": 250, "off_ms": 300},
        ] * 2,
        # Answers the primary rather than doubling it: offset by 300ms (the
        # primary's first on+off) so its pulse lands in the first silence,
        # and given a deliberately different, shorter phrase so the two
        # patterns drift against each other as they loop instead of locking
        # into one repeating bar. Applies even in the no-AI fallback path.
        "buzzer_2": [{"on_ms": 0, "off_ms": 300}]
        + [
            {"on_ms": 250, "off_ms": 250},
            {"on_ms": 100, "off_ms": 350},
        ] * 3,
        # Staggered start (on_ms: 0 = "begin already off, wait out off_ms
        # first") so the 3 LEDs light in sequence rather than in place --
        # a rotating "chase" ring even in the no-AI-available fallback case.
        "led_1": [{"on_ms": 200, "off_ms": 200}] * 5,
        "led_2": [{"on_ms": 0, "off_ms": 133}] + [{"on_ms": 200, "off_ms": 200}] * 5,
        "led_3": [{"on_ms": 0, "off_ms": 266}] + [{"on_ms": 200, "off_ms": 200}] * 5,
    },
    "normal": {
        "led_3": [{"on_ms": 800, "off_ms": 200}],
    },
}

PLAN_INSTRUCTIONS = f"""You control physical warning devices on an ESP32 through a strict JSON plan. Devices available: {", ".join(all_controllable_devices())}.

Return ONLY a JSON object, no prose, no markdown fences. Shape:
{{"device_id": [{{"on_ms": <int>, "off_ms": <int>}}, ...], ...}}

Hard rules:
- Only use device ids from the list above.
- At most {MAX_STEPS} steps per device.
- Each on_ms/off_ms must be between {MIN_MS} and {MAX_MS}, EXCEPT a device's
  very first step may use "on_ms": 0 -- this means that device starts
  already off, silently waiting out that step's off_ms before its pattern
  begins. Use this to stagger LEDs into a rotating/chase effect: e.g. led_1
  starts immediately, led_2's first step is {{"on_ms": 0, "off_ms": 150}} so
  it lights 150ms after led_1, led_3's first step is {{"on_ms": 0, "off_ms": 300}}
  so it lights 300ms after led_1 -- each LED then repeats its own on/off
  cycle, producing a moving "chasing light" ring rather than all LEDs
  blinking in place together. This is a good default for a HIGH TEMPERATURE
  "danger" signal when you want the lights to visually rotate.
- For a HIGH TEMPERATURE event, include BOTH buzzers AND at least two of the
  LEDs, each with its own distinct timing -- a combined light+sound signal
  reads as more urgent than sound alone, and this is a visual+audible alarm,
  not a buzzer with LEDs as an afterthought.
- buzzer and buzzer_2 are TWO SEPARATE VOICES on two separate pins. Do NOT
  give them identical timings -- that just sounds like one louder buzzer and
  throws away the second channel. Compose them as a SEQUENCE that passes
  back and forth, like two alarm units answering each other across a room:
    * Start buzzer_2 with a leading {{"on_ms": 0, "off_ms": N}} step so its
      first pulse lands inside the primary's first silent gap.
    * Choose that offset N to roughly match the primary's first on_ms +
      off_ms, so the two genuinely alternate instead of overlapping.
    * Give them DIFFERENT step counts and pulse lengths (e.g. the primary
      plays short-short-long while the second answers with a single longer
      tone). Because each device loops its own pattern independently, unequal
      lengths make the pairing drift and evolve over the alarm rather than
      repeating one identical bar -- this is what makes it sound composed.
  The goal is call-and-response: a listener should hear two distinct sources
  taking turns, not one buzzer with an echo.
- The buzzer must NOT just repeat one identical {{on_ms, off_ms}} pair over
  and over -- that reads as a flat, robotic "just beeping," not an alarm.
  Give it a real siren cadence with at least 2-3 DIFFERENT on_ms/off_ms
  values across its steps (e.g. short-short-long, or a tempo that speeds up,
  or a distinct repeating rhythm) so it sounds composed, not monotone.
- The buzzer's on_ms must never exceed {BUZZER_MAX_ON_MS} -- short, punchy
  pulses only. A long sustained tone sounds like one flat continuous beep,
  not a rhythm; build the cadence from short pulses spaced by varied
  off_ms instead of ever holding it on for a long stretch.
- The buzzer's off_ms must be at least {BUZZER_MIN_OFF_MS} -- the buzzer
  needs real silence between pulses or they blur into one continuous tone.
  Never give the buzzer back-to-back short pulses with barely any gap.
- Design the pattern to read as a real warning signal (e.g. fast alternating
  on/off for urgency, slower/fewer steps for an all-clear, or a rotating
  chase across the LEDs per the rule above) -- not a fixed template, actually
  vary it with the situation described.
- THE RHYTHM MUST COMMUNICATE THE ACTUAL TEMPERATURE. You are given the real
  reading; the alarm is the only channel the people in the room have, so
  someone listening must be able to tell roughly HOW hot it is without
  looking at a screen. Scale the urgency continuously with the reading:
    * just over the 30C threshold (~30-36C) -- deliberately unhurried:
      longer gaps, few steps, a calm "attention" signal, LEDs stepping
      slowly. It should NOT sound like an emergency.
    * clearly elevated (~36-45C) -- steady, insistent mid-tempo, tighter
      gaps, more steps, LEDs chasing at a moderate pace.
    * dangerous (45C and above) -- rapid, dense, minimum permitted gaps,
      maximum steps, all devices driven hard: unmistakably an emergency.
  Interpolate between these; do not snap to three fixed presets. Two
  different readings should produce two audibly different alarms.
"""


def _clamp(value, lo, hi):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, value))


def _clamp_on_ms(value, lo, hi):
    """Same as _clamp, but preserves an explicit 0 -- the model's signal that
    this device starts its pattern already off (see PLAN_INSTRUCTIONS' chase
    effect). The firmware itself floors any on_ms to 1ms regardless, so a 0
    here can never leave a device stuck fully on."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return lo
    if value == 0:
        return 0
    return max(lo, min(hi, value))


def validate_plan(raw: dict) -> dict:
    """
    Hard safety net: whatever the model returned, only well-formed steps on
    known devices with in-range timings survive. Anything else is dropped
    (per-device, not a whole-plan reject) rather than trusted as-is.
    """
    allowed = set(all_controllable_devices())
    clean: dict[str, list[dict[str, int]]] = {}
    if not isinstance(raw, dict):
        return clean

    for device_id, steps in raw.items():
        if device_id not in allowed or not isinstance(steps, list):
            continue
        # Buzzer gets a lower on_ms ceiling and a higher off_ms floor than
        # LEDs -- confirmed live that a long sustained tone (high on_ms) AND
        # too-short a gap (low off_ms, letting the piezo's ring-out bleed
        # into the next pulse) both make it sound like one continuous beep
        # regardless of what the model intended, so both are enforced here
        # rather than left to prompt compliance.
        # startswith, not ==: buzzer_2 is a real second buzzer on its own pin
        # and needs the exact same pulse-length ceiling and silence floor as
        # the primary. Matching only "buzzer" would leave buzzer_2 able to
        # emit long sustained tones -- the flat-beep problem this cap exists
        # to prevent.
        is_buzzer = device_id.startswith("buzzer")
        on_ms_cap = BUZZER_MAX_ON_MS if is_buzzer else MAX_MS
        off_ms_floor = BUZZER_MIN_OFF_MS if is_buzzer else MIN_MS
        clean_steps = []
        for step in steps[:MAX_STEPS]:
            if not isinstance(step, dict):
                continue
            clean_steps.append({
                "on_ms": _clamp_on_ms(step.get("on_ms"), MIN_MS, on_ms_cap),
                "off_ms": _clamp(step.get("off_ms"), off_ms_floor, MAX_MS),
            })
        if clean_steps:
            clean[device_id] = clean_steps
    return clean


def _extract_json(text: str) -> dict | None:
    """Models routinely wrap JSON in ```json fences or add a leading
    sentence despite instructions not to -- pull out the first {...} block
    rather than requiring an exact bare-JSON response."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def plan_device_response(event_message: str, temperature: float | None) -> dict:
    """
    Returns a validated {device_id: [{on_ms, off_ms}, ...]} plan for the
    given sensor event. Always returns something usable -- falls back to a
    fixed safe pattern on any generation/parsing failure rather than
    leaving the device with no plan at all.
    """
    is_high = "HIGH" in event_message.upper()
    fallback = FALLBACK_PLANS["high" if is_high else "normal"]

    context = retrieve_device_context(event_message)
    temp_note = f" Reading: {temperature:.1f}°C." if temperature is not None else ""

    try:
        from models.registry import registry

        model = registry.get(PLANNER_MODEL)
        raw_text = await model.generate(
            prompt=f"Event: {event_message}.{temp_note}\n\nDevice knowledge:\n{context}",
            system=PLAN_INSTRUCTIONS,
            # 800, not 400: a 4-device plan with up to 8 steps each is a lot
            # of JSON, and the temperature-scaling rules push the model toward
            # denser patterns. At 400 the response was being truncated
            # mid-object, so _extract_json failed and EVERY high reading
            # silently fell back to the same static plan -- which looked like
            # "the AI ignores temperature" (confirmed live 2026-07-31).
            max_tokens=800,
            temperature=0.4,
            task_type="device_plan",
        )
        parsed = _extract_json(raw_text)
        if parsed is None:
            logger.warning("[device_planner] unparseable response, using fallback")
            return fallback
        plan = validate_plan(parsed)
        if not plan:
            logger.warning("[device_planner] validated plan was empty, using fallback")
            return fallback
        return plan
    except Exception as exc:
        logger.warning(f"[device_planner] generation failed ({exc}), using fallback")
        return fallback

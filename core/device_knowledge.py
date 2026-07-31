"""
core/device_knowledge.py -- lightweight, hardware-only RAG.

Deliberately separate from core/collective_memory.py (the general chat
system's vector-memory RAG, chromadb + sentence-transformers): the school
heat-monitor demo needed its own knowledge of what devices the ESP32 has,
scoped to ONLY /api/sensor's device-plan generation, never the website
chat/Manager/Team pipeline. Keeping it a distinct module is what makes that
separation real rather than just a comment.

No vector DB here on purpose: the corpus is a handful of short device
descriptions, not thousands of documents. A keyword-overlap retriever gets
the same practical result (retrieve the relevant subset, ground the
generation in it) without pulling in chromadb/sentence-transformers/torch
for a demo whose whole point is running locally with nothing heavy.
"""
from __future__ import annotations

import re

_STOPWORDS = {
    "the", "a", "an", "is", "are", "to", "of", "and", "or", "in", "on", "for",
    "with", "this", "that", "it", "be", "has", "have", "was", "were",
}

# Each entry: (id, searchable text). Extend this list as real hardware is
# added -- the retriever doesn't care how many entries there are.
#
# Pin numbers and device count below match the actual physical wiring
# confirmed live (2026-07-30) -- an earlier version assumed GPIO25/26/27
# for 2 colored LEDs, which didn't match the real GPIO4/5/19/23 wiring at
# all (the reason nothing visibly happened on an earlier test despite the
# server generating a valid plan). No color names here since none were
# specified for these three; "led_red"/"led_green" was a guess that turned
# out wrong once, not repeating that.
_DEVICES = [
    (
        "buzzer",
        "Primary buzzer / alarm. GPIO23, digital output only (HIGH = sounds, "
        "LOW = silent). Pairs with buzzer_2 on a separate pin, so the two can "
        "be driven independently as two distinct voices. "
        "Already sounds instantly and locally the moment high "
        "temperature is detected, independent of any network call -- this "
        "device plan adds a RICHER pattern on top of that instant reaction, "
        "timed once the server has actually responded, not a replacement "
        "for it.",
    ),
    (
        "buzzer_2",
        "Second buzzer / alarm. GPIO21, digital output only (HIGH = sounds, "
        "LOW = silent). Fully independent of the primary buzzer -- it has its "
        "own pin, so the two can alternate, overlap, or trade phrases. Use "
        "the pair as TWO VOICES rather than doubling the same beat: "
        "alternating them (one sounding while the other rests) reads as a "
        "real two-tone emergency siren, whereas firing both on identical "
        "timing just sounds like one louder buzzer and wastes the second "
        "channel entirely.",
    ),
    (
        "led_1",
        "Warning LED #1. GPIO4, digital output (HIGH = lit, LOW = off). "
        "No fixed color/meaning assigned -- use it as part of a combined "
        "pattern with led_2/led_3 and the buzzer.",
    ),
    (
        "led_2",
        "Warning LED #2. GPIO18, digital output (HIGH = lit, LOW = off). "
        "No fixed color/meaning assigned -- use it as part of a combined "
        "pattern with led_1/led_3 and the buzzer.",
    ),
    (
        "led_3",
        "Warning LED #3. GPIO19, digital output (HIGH = lit, LOW = off). "
        "No fixed color/meaning assigned -- use it as part of a combined "
        "pattern with led_1/led_2 and the buzzer.",
    ),
    (
        "heat_sensor",
        "DHT11 digital temperature sensor (single-wire protocol, NOT an "
        "analog voltage output) on GPIO27. Read roughly once per second. "
        "Threshold is 30 degC: crossing above is the HIGH TEMPERATURE "
        "DETECTED edge, crossing below is the Temperature normalized edge. "
        "30 is a demo-sensitivity setting (ambient is ~28.5 degC), not a "
        "realistic fire threshold. The sensor's rated ceiling is 50 degC, so "
        "it cannot report reliably above that. Readings meaningfully above "
        "30 indicate escalating danger and the warning pattern should "
        "reflect how far above it is. "
        "This is a sensor, not something a device plan can control.",
    ),
]


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def retrieve_device_context(query: str, k: int = 4) -> str:
    """
    Keyword-overlap retrieval over the device manifest above. Returns the
    top-k matching device descriptions concatenated as grounding text: this
    is the "R" in RAG for the device-planner prompt in core/device_planner.py.
    """
    query_terms = _tokenize(query)
    scored = []
    for device_id, text in _DEVICES:
        overlap = len(query_terms & _tokenize(text))
        scored.append((overlap, device_id, text))
    scored.sort(key=lambda x: x[0], reverse=True)

    # A tiny corpus like this one is cheap enough to just return in full when
    # nothing scores above zero (e.g. a query with no shared vocabulary) --
    # under-grounding a hardware-control prompt is worse than over-including
    # four short paragraphs.
    top = [s for s in scored if s[0] > 0][:k] or scored[:k]
    return "\n\n".join(f"[{d}] {t}" for _, d, t in top)


def all_controllable_devices() -> list[str]:
    """Device ids the planner is allowed to target -- excludes sensors."""
    return [d for d, _ in _DEVICES if d != "heat_sensor"]

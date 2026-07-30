"""
core/memory_edges.py -- typed edges over the ChromaDB collective memory.

Concept ported from jcode v0.54.4 (MIT), which uses a petgraph DiGraph with
Supersedes / Contradicts / DerivedFrom edges. Minimum-viable port for ChromaDB
(no graph library): edges live as metadata links on each memory doc.

Real ChromaDB constraint the doc's pseudocode glosses over: **metadata values
must be scalars** (str/int/float/bool) -- NOT lists. So edge lists are stored
JSON-encoded (`supersedes_json` etc.) and decoded on read. These functions are
pure so they unit-test without a live ChromaDB (which needs a ~60s
sentence-transformers cold load).

Rules (jcode):
  * Superseded memories are SUPPRESSED at retrieval, never deleted -- tombstone
    the edge, keep the node (audit trail).
  * If two retrieved memories contradict, inject BOTH with an explicit marker.
    Serving one silently is how a memory system starts confidently lying.
  * 1-hop cascade only for now: pull `derived_from` parents of any hit into the
    candidate set. (jcode's full BFS + async sidecar verification is v2.)
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

_LIST_FIELDS = ("supersedes", "contradicts", "derived_from")


def encode_edges(
    *, supersedes: list[str] = (), contradicts: list[str] = (),
    derived_from: list[str] = (), superseded_by: str | None = None,
) -> dict:
    """Build the ChromaDB-safe metadata dict for a memory's edges. Lists ->
    JSON strings (ChromaDB rejects list-valued metadata)."""
    meta: dict[str, Any] = {
        "supersedes_json":   json.dumps(list(supersedes)),
        "contradicts_json":  json.dumps(list(contradicts)),
        "derived_from_json": json.dumps(list(derived_from)),
        "created_at":        time.time(),
    }
    # superseded_by is a scalar; "" means "not superseded" (ChromaDB has no null).
    meta["superseded_by"] = superseded_by or ""
    return meta


def decode_edges(meta: dict) -> dict:
    """Inverse of encode_edges: JSON-string fields -> lists; '' -> None."""
    out: dict[str, Any] = {}
    for f in _LIST_FIELDS:
        raw = meta.get(f"{f}_json")
        try:
            out[f] = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            out[f] = []
    sb = meta.get("superseded_by") or ""
    out["superseded_by"] = sb or None
    return out


def is_superseded(meta: dict) -> bool:
    return bool(meta.get("superseded_by"))


def filter_superseded(hits: list[dict]) -> list[dict]:
    """Drop hits that have been superseded. `hits` are dicts with a 'meta' key.
    The superseded node still EXISTS in the store (audit) -- it's just not
    served."""
    return [h for h in hits if not is_superseded(h.get("meta", {}))]


def attach_contradiction_flags(hits: list[dict]) -> list[dict]:
    """If two retrieved memories reference each other via a contradicts edge,
    mark BOTH with a conflict flag so the caller injects an explicit
    '[CONFLICT: verify before relying on either]' marker. Never silently drop
    one."""
    by_id = {h.get("id"): h for h in hits if h.get("id")}
    for h in hits:
        edges = decode_edges(h.get("meta", {}))
        for other in edges["contradicts"]:
            if other in by_id:
                h["conflict_with"] = h.get("conflict_with", []) + [other]
                by_id[other]["conflict_with"] = by_id[other].get("conflict_with", []) + [h["id"]]
    return hits


def cascade_derived_from(
    hits: list[dict], fetch_by_id: Callable[[str], dict | None],
) -> list[dict]:
    """1-hop: pull the derived_from PARENTS of any hit into the candidate set
    (deduped, superseded parents excluded). `fetch_by_id` returns a hit-shaped
    dict or None."""
    have = {h.get("id") for h in hits if h.get("id")}
    extra: list[dict] = []
    for h in list(hits):
        for parent_id in decode_edges(h.get("meta", {}))["derived_from"]:
            if parent_id in have:
                continue
            parent = fetch_by_id(parent_id)
            if parent and not is_superseded(parent.get("meta", {})):
                have.add(parent_id)
                extra.append(parent)
    return hits + extra


def conflict_marker(hits: list[dict]) -> str:
    """Human-readable conflict banner for any flagged pairs, or '' if none."""
    flagged = [h for h in hits if h.get("conflict_with")]
    if not flagged:
        return ""
    lines = ["[CONFLICT: the following retrieved memories contradict each other "
             "-- verify before relying on either]"]
    seen = set()
    for h in flagged:
        for other in h["conflict_with"]:
            pair = tuple(sorted((h.get("id", "?"), other)))
            if pair in seen:
                continue
            seen.add(pair)
            lines.append(f"  - {pair[0]}  <>  {pair[1]}")
    return "\n".join(lines)

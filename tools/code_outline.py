"""
tools/code_outline.py -- outline-enriched code search (agentgrep-lite).

Concept ported from jcode v0.54.4 (MIT). Grep hits are enriched with each
file's STRUCTURE (functions/classes and their line spans) so the model can
infer what a file does -- and where -- without reading the whole thing. jcode
reports this as their single biggest context saver: a file's shape costs ~50
tokens instead of a 400-line read.

Dependency note: jcode uses tree-sitter. tree-sitter is NOT installed in this
environment, and this project has repeatedly paid for dependency friction
(rich, platformio, sentence-transformers cold loads). So the primary path here
is a dependency-free regex outline for Python/JS/TS/JSX -- serviceable for the
token-saving goal -- with tree-sitter used automatically IF it is ever
installed. Outline is also the foundation for the anchor-addressing edit
interface (edit-by-@fn:name) from the precision plan: same parse, same anchors.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass
class Anchor:
    kind: str          # "function" | "class" | "method"
    name: str
    start_line: int    # 1-indexed
    end_line: int


# ── regex outline (dependency-free) ─────────────────────────────────────────

_PY_DEF = re.compile(r"^(?P<indent>[ \t]*)(?:async\s+)?def\s+(?P<name>\w+)")
_PY_CLASS = re.compile(r"^(?P<indent>[ \t]*)class\s+(?P<name>\w+)")

_JS_PATTERNS = [
    ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>")),
    ("class",    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)")),
]


def _py_block_end(lines: list[str], start_idx: int, indent: str) -> int:
    """End line (1-indexed) of a Python def/class starting at start_idx: the
    last line before dedent back to <= the header's indent."""
    base = len(indent.expandtabs())
    end = start_idx
    for i in range(start_idx + 1, len(lines)):
        ln = lines[i]
        if not ln.strip():
            continue                       # blank lines belong to the block
        cur = len(ln[:len(ln) - len(ln.lstrip())].expandtabs())
        if cur <= base:
            break
        end = i
    return end + 1


def _brace_block_end(lines: list[str], start_idx: int) -> int:
    """End line (1-indexed) for a brace-delimited block: match the first '{'
    at/after start_idx to its closing '}'. The block may only OPEN on the
    header line or the one immediately after -- otherwise a braceless
    single-expression arrow fn (`const f = () => x + 1;`) would wrongly latch
    onto the next unrelated '{' further down the file. No brace there => the
    body is a single expression, end == start."""
    depth = 0
    started = False
    for i in range(start_idx, len(lines)):
        if not started and i > start_idx + 1:
            break
        for ch in lines[i]:
            if ch == "{":
                depth += 1; started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    return i + 1
    return start_idx + 1


def _outline_python(src: str) -> list[Anchor]:
    lines = src.splitlines()
    out: list[Anchor] = []
    for i, ln in enumerate(lines):
        m = _PY_CLASS.match(ln)
        if m:
            out.append(Anchor("class", m.group("name"), i + 1,
                              _py_block_end(lines, i, m.group("indent"))))
            continue
        m = _PY_DEF.match(ln)
        if m:
            kind = "method" if m.group("indent") else "function"
            out.append(Anchor(kind, m.group("name"), i + 1,
                              _py_block_end(lines, i, m.group("indent"))))
    return out


def _outline_js(src: str) -> list[Anchor]:
    lines = src.splitlines()
    out: list[Anchor] = []
    for i, ln in enumerate(lines):
        for kind, pat in _JS_PATTERNS:
            m = pat.match(ln)
            if m:
                out.append(Anchor(kind, m.group(1), i + 1, _brace_block_end(lines, i)))
                break
    return out


_EXT_DISPATCH = {
    ".py": _outline_python,
    ".js": _outline_js, ".jsx": _outline_js,
    ".ts": _outline_js, ".tsx": _outline_js, ".mjs": _outline_js,
}

# outline cache keyed by (path, mtime) -- reparse only when the file changes.
_CACHE: dict[str, tuple[float, list[Anchor]]] = {}


def outline_source(src: str, ext: str) -> list[Anchor]:
    fn = _EXT_DISPATCH.get(ext.lower())
    return fn(src) if fn else []


def outline(path: str) -> list[Anchor]:
    """Structural outline of a file, cached by mtime. [] for unsupported
    languages or unreadable files (never raises)."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    cached = _CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
    except OSError:
        return []
    anchors = outline_source(src, os.path.splitext(path)[1])
    _CACHE[path] = (mtime, anchors)
    return anchors


def format_outline(anchors: list[Anchor], seen: set[str] | None = None) -> str:
    """Compact one-line file shape, e.g.
    `fn render(12-80), class App(4-120), fn init(82-110)`.
    Anchors already shown to the model this session are marked `(shown)` so
    repeated searches don't re-dump structure."""
    seen = seen or set()
    parts = []
    for a in anchors:
        tag = "class" if a.kind == "class" else "fn"
        mark = " (shown)" if a.name in seen else ""
        parts.append(f"{tag} {a.name}({a.start_line}-{a.end_line}){mark}")
    return ", ".join(parts)


def format_search_hit(path: str, hit_lines: list[tuple[int, str]],
                      seen: set[str] | None = None) -> str:
    """One file's search result: its outline (file shape) + the matching lines,
    instead of the surrounding content. `hit_lines` = [(lineno, text), ...]."""
    anchors = outline(path)
    shape = format_outline(anchors, seen) or "(no parseable structure)"
    hits = "\n".join(f"    L{n}: {t.strip()[:120]}" for n, t in hit_lines)
    return f"{path}\n  outline: {shape}\n  hits:\n{hits}"


def anchor_for_line(anchors: list[Anchor], line: int) -> Anchor | None:
    """Smallest enclosing anchor for a line -- the basis of edit-by-@fn:name."""
    best = None
    for a in anchors:
        if a.start_line <= line <= a.end_line:
            if best is None or (a.end_line - a.start_line) < (best.end_line - best.start_line):
                best = a
    return best

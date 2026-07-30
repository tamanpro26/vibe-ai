"""
core/repo_map.py
Aider-style repo map: a compact, signature-only view of an existing codebase,
injected into the agent's pre-flight context instead of (or alongside) a bare
filename listing. Lets a model know "what functions/classes already exist and
where" for a few hundred tokens, instead of either a useless filenames-only
listing or burning the context budget reading full files up front.

Python: parsed with `ast` — exact signatures, no guessing, silently skips a
file on a syntax error (never blocks the run over one bad file).

JS/JSX/TS/TSX: no AST parser is a project dependency and this is deliberately
not adding one — a regex extracts the declaration shapes actual codebases use
(function declarations, `export const x = (...) => `, classes). This misses
exotic patterns (computed methods, HOC factories); a miss just omits a line,
it never produces a wrong signature.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

_IGNORE_DIRS = {"node_modules", "dist", "build", ".git", "coverage", ".vite", "__pycache__"}
_PY_EXT = (".py",)
_JS_EXT = (".js", ".jsx", ".ts", ".tsx")
_MAX_FILES = 60
_MAX_CHARS = 4000  # hard cap on the rendered map — must never itself blow a tight Groq budget


def _iter_source_files(root: Path):
    # os.walk with in-place dirnames pruning instead of root.rglob("*") --
    # found in code review (2026-07-13): rglob fully materializes the entire
    # tree, including node_modules/dist, before this same _IGNORE_DIRS filter
    # discards them; os.walk's dirnames[:] mutation stops it from ever
    # descending into a pruned directory in the first place.
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        dp = Path(dirpath)
        for name in filenames:
            if Path(name).suffix.lower() in (_PY_EXT + _JS_EXT):
                yield dp / name


def _args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        return ", ".join(a.arg for a in node.args.args)
    except Exception:
        return ""


def _py_signatures(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return []
    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            lines.append(f"  {prefix} {node.name}({_args(node)})")
        elif isinstance(node, ast.ClassDef):
            lines.append(f"  class {node.name}:")
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prefix = "async def" if isinstance(sub, ast.AsyncFunctionDef) else "def"
                    lines.append(f"    {prefix} {sub.name}({_args(sub)})")
    return lines


# Line-anchored on purpose (re.match, not search): only top-level-ish
# declarations at the start of a (possibly indented) line count — this keeps
# the regex cheap and avoids matching function calls that merely contain
# these keywords mid-expression.
_JS_FUNC_RE  = re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)")
_JS_ARROW_RE = re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>")
_JS_CLASS_RE = re.compile(r"^\s*(?:export\s+(?:default\s+)?)?class\s+(\w+)")


def _js_signatures(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines: list[str] = []
    for raw in text.splitlines():
        m = _JS_FUNC_RE.match(raw)
        if m:
            lines.append(f"  function {m.group(1)}({m.group(2)})")
            continue
        m = _JS_ARROW_RE.match(raw)
        if m:
            lines.append(f"  const {m.group(1)} = ({m.group(2)}) => ...")
            continue
        m = _JS_CLASS_RE.match(raw)
        if m:
            lines.append(f"  class {m.group(1)}")
    return lines


def build_repo_map(root: Path, max_files: int = _MAX_FILES, max_chars: int = _MAX_CHARS) -> str:
    """
    Render a compact 'file -> signatures' map for source files under root,
    most-recently-modified first (whatever's being actively worked on is what
    matters most when the char budget runs out). Returns "" when there's
    nothing to map (fresh/empty workspace, or every file failed to parse) —
    callers should skip injecting the section entirely in that case.
    """
    try:
        files = sorted(_iter_source_files(root), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return ""
    files = files[:max_files]

    out: list[str] = []
    total = 0
    for f in files:
        sigs = _py_signatures(f) if f.suffix.lower() in _PY_EXT else _js_signatures(f)
        if not sigs:
            continue
        try:
            rel = f.relative_to(root)
        except ValueError:
            rel = f.name
        block = f"{rel}\n" + "\n".join(sigs)
        if total + len(block) > max_chars:
            break
        out.append(block)
        total += len(block)

    return "\n\n".join(out)

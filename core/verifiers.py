"""
core/verifiers.py
Deterministic, zero-token verification battery for web projects.

Every check here targets a defect class that was actually shipped by the free
models during live testing and then had to be found and fixed by hand:
  - JSX classNames with no matching CSS rule anywhere (shipped twice)
  - imports pointing at files that don't exist (shipped once)
  - placeholder/stub content: "Skill 1", "This is project 1", example.com URLs,
    lorem ipsum, TODO markers (shipped once)
  - image URLs that 404 (shipped once)

These run as plain code — no LLM call, no tokens, no hallucination risk.
Checking is far cheaper than generating: each finding becomes a concrete,
mechanical fix order for the model, converting "the model must be smart"
into "the model must satisfy a checklist".
"""
from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

_IGNORE_DIRS = {"node_modules", "dist", "build", ".git", "coverage", ".vite", ".vibeai"}
_MAX_FILES = 80
_MAX_FINDINGS_PER_CHECK = 8


def _iter_files(
    root: Path,
    exts: tuple[str, ...],
    allowed_dirs: set[str] | None = None,
    allowed_root_files: set[str] | None = None,
):
    """allowed_dirs, when given, restricts recursion to these specific
    top-level subdirectories; allowed_root_files (exact filenames) similarly
    restricts root-level LOOSE files. Anything else under root is skipped
    even though it physically exists there.

    Exists because `root` is sometimes the whole multi-project workspace,
    not just the current run's project (see agent_loop.py: a task whose
    files land at the workspace root, with no project directory of its own,
    falls back to verifying at the workspace root). Without the subdirectory
    half of this, `rglob` recurses into EVERY leftover project sitting in
    that workspace and reports findings from code this run never touched.
    Caught live (2026-07-12): "create reverse_string.py" (2 root files, a
    Python exercise) produced 14 findings sourced from unrelated leftover
    aurora-site/meridian/my-app web projects, and the model then tried to
    "fix" them by generating images for a plain Python-script task.

    The root-files half was a SEPARATE bug in the first version of this
    fix, found by re-running the exact same scenario live after deploying
    it: subdirectories were correctly excluded (14 findings -> 8), but every
    root-level loose file was still unconditionally allowed on the
    (untested) assumption that "a file directly in root belongs to no
    project, so there's nothing to exclude it FROM." That's false the
    moment stale unrelated files already sit at the workspace root -- this
    workspace has several (an old index.html, old letters, ...). The model
    was handed a finding about a STALE index.html left over from a past,
    unrelated session, dutifully "fixed" it by writing a 346-line page, and
    that page's own image references then triggered a ~3-minute
    image-localization detour -- for a task that should take seconds.
    allowed_root_files closes this: a root-level file must be in the
    current run's own touched set, not merely "sitting in root"."""
    n = 0
    for p in sorted(root.rglob("*")):
        if any(part in _IGNORE_DIRS for part in p.parts):
            continue
        if allowed_dirs is not None:
            rel_parts = p.relative_to(root).parts
            if len(rel_parts) > 1:
                if rel_parts[0] not in allowed_dirs:
                    continue
            elif allowed_root_files is not None and p.name not in allowed_root_files:
                continue
        if p.is_file() and p.suffix.lower() in exts:
            yield p
            n += 1
            if n >= _MAX_FILES:
                return


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ── Check 1: relative imports that resolve to nothing ─────────────────────────

_IMPORT_RE = re.compile(
    r"""import\s+(?:[\w{}\s,*$]+\s+from\s+)?['"](\.{1,2}/[^'"]+)['"]"""
)
# Vite-style resolution: exact path, then common extensions, then index files.
_RESOLVE_SUFFIXES = ("", ".js", ".jsx", ".ts", ".tsx", ".css",
                     "/index.js", "/index.jsx", "/index.ts", "/index.tsx")


def check_relative_imports(root: Path, allowed_dirs: set[str] | None = None, allowed_root_files: set[str] | None = None) -> list[str]:
    findings: list[str] = []
    for f in _iter_files(root, (".js", ".jsx", ".ts", ".tsx"), allowed_dirs, allowed_root_files):
        text = _read(f)
        for m in _IMPORT_RE.finditer(text):
            spec = m.group(1)
            base = (f.parent / spec)
            if not any(Path(str(base) + suf).exists() for suf in _RESOLVE_SUFFIXES):
                findings.append(
                    f"[import] {f.relative_to(root)}: imports '{spec}' but no such file exists "
                    f"— create it or fix the path"
                )
                if len(findings) >= _MAX_FINDINGS_PER_CHECK:
                    return findings
    return findings


# ── Check 2: JSX classNames never referenced by any CSS rule ──────────────────

# Only literal (quoted) className values — template literals / expressions are
# dynamic and can't be checked statically, so they're deliberately skipped.
_CLASSNAME_RE = re.compile(r"""className\s*=\s*["']([^"']+)["']""")
_HTML_CLASS_RE = re.compile(r"""\bclass\s*=\s*["']([^"']+)["']""")
_CSS_CLASS_DEF_RE = re.compile(r"\.([A-Za-z_][\w-]*)")


def check_css_classes(root: Path, allowed_dirs: set[str] | None = None, allowed_root_files: set[str] | None = None) -> list[str]:
    used: dict[str, str] = {}
    for f in _iter_files(root, (".jsx", ".tsx", ".js", ".html"), allowed_dirs, allowed_root_files):
        text = _read(f)
        regexes = [_CLASSNAME_RE] if f.suffix != ".html" else [_HTML_CLASS_RE]
        for rx in regexes:
            for m in rx.finditer(text):
                for cls in m.group(1).split():
                    used.setdefault(cls, str(f.relative_to(root)))

    css_files = list(_iter_files(root, (".css",), allowed_dirs, allowed_root_files))
    if not css_files or not used:
        return []
    defined: set[str] = set()
    for f in css_files:
        defined |= set(_CSS_CLASS_DEF_RE.findall(_read(f)))

    findings = []
    for cls, where in used.items():
        if cls not in defined:
            findings.append(
                f"[css] class '{cls}' (used in {where}) has no CSS rule in any stylesheet "
                f"— add styling for it or remove the className"
            )
            if len(findings) >= _MAX_FINDINGS_PER_CHECK:
                break
    return findings


# ── Check 3: placeholder / stub content ───────────────────────────────────────

_PLACEHOLDER_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"lorem ipsum", re.IGNORECASE), "lorem ipsum text"),
    (re.compile(r"via\.placeholder\.com|placehold\.it", re.IGNORECASE), "placeholder image service"),
    (re.compile(r"https?://(?:www\.)?example\.com/[\w./-]+"), "fake example.com URL"),
    (re.compile(r"coming soon", re.IGNORECASE), "'coming soon' stub"),
    (re.compile(r"\bTODO\b|\bFIXME\b"), "TODO/FIXME marker"),
    (re.compile(r">\s*(?:Skill|Project|Feature|Item)\s+\d+\s*<"), "generic numbered stub (e.g. 'Skill 1')"),
    (re.compile(r"This is (?:skill|project|feature|item) \d", re.IGNORECASE), "generic stub description"),
]


def check_placeholders(root: Path, allowed_dirs: set[str] | None = None, allowed_root_files: set[str] | None = None) -> list[str]:
    findings = []
    for f in _iter_files(root, (".jsx", ".tsx", ".js", ".html", ".css"), allowed_dirs, allowed_root_files):
        text = _read(f)
        for rx, label in _PLACEHOLDER_PATTERNS:
            m = rx.search(text)
            if m:
                findings.append(
                    f"[placeholder] {f.relative_to(root)}: contains {label} "
                    f"('{m.group(0)[:40]}') — replace with real content"
                )
                if len(findings) >= _MAX_FINDINGS_PER_CHECK:
                    return findings
    return findings


# ── Check 4: remote image URLs that hard-404 ──────────────────────────────────

_SRC_URL_RE = re.compile(r"""src=["'](https?://[^"']+)["']""")
# Statuses that mean "this image will never load". 405/501 (method rejected)
# and timeouts are NOT flagged — some CDNs reject HEAD, and generative image
# hosts (pollinations) can be slow on first hit; a false "broken" finding would
# send the model chasing a non-problem.
_HARD_FAIL_STATUSES = {400, 401, 403, 404, 410}


async def check_remote_images(root: Path, allowed_dirs: set[str] | None = None, allowed_root_files: set[str] | None = None) -> list[str]:
    urls: dict[str, str] = {}
    for f in _iter_files(root, (".jsx", ".tsx", ".js", ".html"), allowed_dirs, allowed_root_files):
        for m in _SRC_URL_RE.finditer(_read(f)):
            urls.setdefault(m.group(1), str(f.relative_to(root)))
    if not urls:
        return []

    import aiohttp
    findings = []
    checked = 0
    try:
        async with aiohttp.ClientSession() as session:
            for url, where in urls.items():
                if checked >= 8:
                    break
                checked += 1
                try:
                    async with session.head(
                        url, timeout=aiohttp.ClientTimeout(total=8), allow_redirects=True
                    ) as resp:
                        if resp.status in _HARD_FAIL_STATUSES:
                            findings.append(
                                f"[image] {where}: image URL returns HTTP {resp.status} "
                                f"and will never load: {url[:100]}"
                            )
                except Exception:
                    pass  # network flakiness must not fail the gate
    except Exception as exc:
        logger.warning(f"[verifiers] image check skipped: {str(exc)[:60]}")
    return findings


# ── Check 5: local image srcs that point at nothing ───────────────────────────

_LOCAL_SRC_RE = re.compile(r"""src=["'](/[^"']+\.(?:png|jpe?g|svg|gif|webp))["']""")


def check_local_images(root: Path, allowed_dirs: set[str] | None = None, allowed_root_files: set[str] | None = None) -> list[str]:
    """Absolute-path srcs like /images/x.png must exist under public/ (vite
    serves public/ at the web root) or under the project root itself."""
    findings = []
    for f in _iter_files(root, (".jsx", ".tsx", ".js", ".html"), allowed_dirs, allowed_root_files):
        for m in _LOCAL_SRC_RE.finditer(_read(f)):
            rel = m.group(1).lstrip("/")
            if not (root / "public" / rel).exists() and not (root / rel).exists():
                findings.append(
                    f"[image] {f.relative_to(root)}: src '{m.group(1)}' has no matching file "
                    f"under public/ — create the file or fix the path"
                )
                if len(findings) >= _MAX_FINDINGS_PER_CHECK:
                    return findings
    return findings


# ── Check 6: HTML references to local files that don't exist ──────────────────
# The exact defect class that shipped live on 2026-07-06: a standalone
# index.html referencing styles.css and script.js that were never created —
# the page "opens" as an unstyled wall of text with no behavior. Neither
# existing check caught it: check_relative_imports only reads JS `import`
# statements, and check_css_classes silently returns [] when there are NO
# css files at all (the missing stylesheet was itself the bug).

_HTML_REF_RE = re.compile(
    r"""(?:href|src)\s*=\s*["']([^"']+\.(?:css|js|mjs))["']""", re.IGNORECASE
)


def check_html_local_refs(root: Path, allowed_dirs: set[str] | None = None, allowed_root_files: set[str] | None = None) -> list[str]:
    findings = []
    for f in _iter_files(root, (".html",), allowed_dirs, allowed_root_files):
        for m in _HTML_REF_RE.finditer(_read(f)):
            ref = m.group(1)
            if ref.startswith(("http://", "https://", "//", "data:")):
                continue  # remote/CDN refs are a different check's problem
            rel = ref.lstrip("/").split("?", 1)[0]
            # Resolve like a static server would: relative to the HTML file,
            # the project root, and public/ (vite serves it at the web root).
            candidates = (f.parent / rel, root / rel, root / "public" / rel)
            if not any(c.exists() for c in candidates):
                findings.append(
                    f"[html] {f.relative_to(root)}: references '{ref}' but no such file "
                    f"exists — the page will load unstyled/broken; create it or fix the path"
                )
                if len(findings) >= _MAX_FINDINGS_PER_CHECK:
                    return findings
    return findings


# ── Check 7: Python code quality (deterministic, greppable senior-review items) ─

_PY_QUALITY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"datetime\.utcnow\s*\("),
     "datetime.utcnow() is deprecated — use datetime.now(timezone.utc)"),
    (re.compile(r"^\s*except\s*:\s*$", re.MULTILINE),
     "bare 'except:' swallows SystemExit/KeyboardInterrupt — catch Exception or narrower"),
    (re.compile(r"\brandom\.(choice|randint|random)\([^)]*\).*(?:token|secret|password|slug)", re.IGNORECASE),
     "random module used for security-sensitive value — use the secrets module"),
]


def check_python_quality(root: Path, allowed_dirs: set[str] | None = None, allowed_root_files: set[str] | None = None) -> list[str]:
    findings = []
    for f in _iter_files(root, (".py",), allowed_dirs, allowed_root_files):
        text = _read(f)
        for rx, label in _PY_QUALITY_PATTERNS:
            if rx.search(text):
                findings.append(f"[python] {f.relative_to(root)}: {label}")
                if len(findings) >= _MAX_FINDINGS_PER_CHECK:
                    return findings
    return findings


# ── Battery ────────────────────────────────────────────────────────────────────

async def run_all(
    root: Path,
    allowed_dirs: set[str] | None = None,
    allowed_root_files: set[str] | None = None,
) -> list[str]:
    """Run every check; returns a flat list of human-readable findings.

    allowed_dirs / allowed_root_files: see _iter_files. Pass these whenever
    `root` might be a multi-project workspace rather than a single project's
    own directory -- None for either means "no restriction" (root IS a
    single project), matching every existing caller's behavior unchanged.
    """
    findings: list[str] = []
    findings += check_relative_imports(root, allowed_dirs, allowed_root_files)
    findings += check_html_local_refs(root, allowed_dirs, allowed_root_files)
    findings += check_css_classes(root, allowed_dirs, allowed_root_files)
    findings += check_placeholders(root, allowed_dirs, allowed_root_files)
    findings += check_local_images(root, allowed_dirs, allowed_root_files)
    findings += check_python_quality(root, allowed_dirs, allowed_root_files)
    findings += await check_remote_images(root, allowed_dirs, allowed_root_files)
    if findings:
        logger.info(f"[verifiers] {len(findings)} finding(s) in {root.name or root}")
    else:
        logger.info(f"[verifiers] all checks clean in {root.name or root}")
    return findings

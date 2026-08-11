"""Data-only Codex skill/plugin compatibility adapter."""

from __future__ import annotations

import json

from capabilities.adapters import ComponentReport, ConversionResult, ConversionStatus
from capabilities.adapters._skill import parse_skill_document, portable_manifest


def adapt_codex(files: dict[str, bytes], source_version: str | None = None) -> ConversionResult:
    reports: list[ComponentReport] = []
    skill_paths = sorted(path for path in files if path == "SKILL.md" or path.endswith("/SKILL.md"))
    plugin_shape = any(path != "SKILL.md" for path in skill_paths) or any(
        path.startswith("hooks/") or path == ".app.json" for path in files
    )
    if plugin_shape and ".codex-plugin/plugin.json" not in files:
        return _rejected(".codex-plugin/plugin.json", "Codex plugins require their manifest", source_version)
    if ".codex-plugin/plugin.json" in files:
        try:
            plugin_manifest = json.loads(files[".codex-plugin/plugin.json"])
            if not all(plugin_manifest.get(key) for key in ("name", "version", "description")):
                raise ValueError
        except (json.JSONDecodeError, TypeError, ValueError):
            return _rejected(".codex-plugin/plugin.json", "invalid Codex plugin manifest", source_version)
    if len(skill_paths) != 1:
        return _rejected("package", "exactly one skill is required per import", source_version)
    try:
        frontmatter, instructions = parse_skill_document(files[skill_paths[0]])
        manifest = portable_manifest(frontmatter, instructions)
    except ValueError as exc:
        return _rejected(skill_paths[0], str(exc), source_version)
    reports.append(ComponentReport(
        component=skill_paths[0], status=ConversionStatus.NATIVE,
        reason="Codex Agent Skill instructions mapped to the VibeAI native profile",
    ))
    for path in sorted(files):
        if path.startswith("hooks/") or path in {".app.json", ".mcp.json"}:
            reports.append(ComponentReport(
                component=path, status=ConversionStatus.DISABLED,
                reason="Codex host integration cannot execute in VibeAI v1",
            ))
        elif path.endswith("agents/openai.yaml"):
            reports.append(ComponentReport(
                component=path, status=ConversionStatus.ADAPTED,
                reason="display metadata retained without provider runtime authority",
            ))
    return ConversionResult(
        provider="codex", provider_version=source_version, manifest=manifest,
        components=tuple(reports), resolver_eligible=True,
    )


def _rejected(component: str, reason: str, version: str | None) -> ConversionResult:
    return ConversionResult(
        provider="codex", provider_version=version, manifest=None, resolver_eligible=False,
        components=(ComponentReport(component=component, status=ConversionStatus.REJECTED, reason=reason),),
    )

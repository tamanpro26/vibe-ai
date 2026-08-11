"""Data-only Claude skill/plugin compatibility adapter."""

from __future__ import annotations

from capabilities.adapters import ComponentReport, ConversionResult, ConversionStatus
from capabilities.adapters._skill import parse_skill_document, portable_manifest

_DISABLED_FIELDS = {"allowed-tools", "disallowed-tools", "hooks", "model", "effort", "background"}
_UNSUPPORTED_FIELDS = {"context", "agent", "isolation", "permissionMode"}
_UNSUPPORTED_PREFIXES = ("agents/", "workflows/", "bin/", "monitors/")


def adapt_claude(files: dict[str, bytes], source_version: str | None = None) -> ConversionResult:
    reports: list[ComponentReport] = []
    skill_paths = sorted(path for path in files if path == "SKILL.md" or path.endswith("/SKILL.md"))
    if not skill_paths:
        legacy = sorted(path for path in files if path.startswith("commands/") and path.endswith(".md"))
        if len(legacy) == 1:
            skill_paths = legacy
            reports.append(
                ComponentReport(
                    component=legacy[0], status=ConversionStatus.ADAPTED,
                    reason="legacy Claude command converted to an instruction skill",
                )
            )
        else:
            return _rejected("package", "exactly one portable SKILL.md is required", source_version)
    if len(skill_paths) != 1:
        return _rejected("package", "ambiguous multi-skill roots require separate imports", source_version)
    skill_path = skill_paths[0]
    try:
        frontmatter, instructions = parse_skill_document(files[skill_path])
        manifest = portable_manifest(frontmatter, instructions)
    except ValueError as exc:
        return _rejected(skill_path, str(exc), source_version)

    reports.append(
        ComponentReport(
            component=skill_path,
            status=ConversionStatus.NATIVE,
            reason="portable instructions mapped to the VibeAI native profile",
        )
    )
    for field in sorted(frontmatter):
        if field in _DISABLED_FIELDS:
            reports.append(ComponentReport(
                component=f"frontmatter.{field}", status=ConversionStatus.DISABLED,
                reason="provider field cannot grant VibeAI runtime authority",
            ))
        elif field in _UNSUPPORTED_FIELDS:
            reports.append(ComponentReport(
                component=f"frontmatter.{field}", status=ConversionStatus.UNSUPPORTED,
                reason="Claude host execution semantics are not portable",
            ))
    for path in sorted(files):
        if path.startswith("hooks/") or path == ".mcp.json":
            reports.append(ComponentReport(
                component=path, status=ConversionStatus.DISABLED,
                reason="external execution is disabled during import and use",
            ))
        elif path.startswith(_UNSUPPORTED_PREFIXES):
            reports.append(ComponentReport(
                component=path, status=ConversionStatus.UNSUPPORTED,
                reason="Claude host component has no safe VibeAI equivalent",
            ))
    return ConversionResult(
        provider="claude", provider_version=source_version, manifest=manifest,
        components=tuple(_deduplicate(reports)), resolver_eligible=True,
    )


def _rejected(component: str, reason: str, version: str | None) -> ConversionResult:
    return ConversionResult(
        provider="claude", provider_version=version, manifest=None, resolver_eligible=False,
        components=(ComponentReport(component=component, status=ConversionStatus.REJECTED, reason=reason),),
    )


def _deduplicate(reports: list[ComponentReport]) -> list[ComponentReport]:
    seen: set[tuple[str, ConversionStatus]] = set()
    unique: list[ComponentReport] = []
    for item in reports:
        key = (item.component, item.status)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique

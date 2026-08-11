from __future__ import annotations

import re
from typing import Any

import yaml

from capabilities.manifests import CapabilityManifest

_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")


def parse_skill_document(content: bytes) -> tuple[dict[str, Any], str]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("SKILL.md must be UTF-8 text") from exc
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md requires YAML frontmatter")
    boundary = text.find("\n---", 4)
    if boundary < 0:
        raise ValueError("SKILL.md frontmatter is not closed")
    try:
        frontmatter = yaml.safe_load(text[4:boundary]) or {}
    except yaml.YAMLError as exc:
        raise ValueError("SKILL.md frontmatter is invalid YAML") from exc
    if not isinstance(frontmatter, dict):
        raise ValueError("SKILL.md frontmatter must be an object")
    instructions = text[boundary + 4 :].strip()
    return frontmatter, instructions


def portable_manifest(frontmatter: dict[str, Any], instructions: str) -> CapabilityManifest:
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("portable skill requires explicit name")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("portable skill requires explicit description")
    capability_id = _SLUG_CLEAN.sub("-", name.strip().lower()).strip("-")
    tasks = frontmatter.get("tasks", [])
    if not isinstance(tasks, list) or not all(isinstance(item, str) for item in tasks):
        tasks = []
    return CapabilityManifest.model_validate(
        {
            "schema_version": "vibeai.capability/v1",
            "capability_id": capability_id,
            "name": name.strip(),
            "description": description.strip(),
            "version": str(frontmatter.get("version", "1.0.0")),
            "kind": "instruction_skill",
            "supported_tasks": tasks,
            "activation": {
                "mode": "manual_only",
                "intent_tags": tasks,
                "explicit_triggers": [],
                "required_features": [],
                "priority": 0,
                "conflicts": [],
            },
            "permissions": [],
            "services": [],
            "dependencies": [],
            "trust": "user_imported",
            "risk": "low",
            "instructions": instructions,
            "configuration_schema": {},
        }
    )

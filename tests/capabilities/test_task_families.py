from __future__ import annotations

import json
from pathlib import Path

from capabilities.manifests import CapabilityManifest


def test_builtin_families_are_passive_bounded_and_provider_neutral():
    root = Path(__file__).parents[2] / "capabilities" / "builtins"
    manifests = [
        CapabilityManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in root.glob("*/manifest.json")
    ]
    assert manifests
    assert all(not item.permissions and not item.services for item in manifests if item.kind.value in {"instruction_skill", "bundle"})
    assert all(len(item.instructions or "") < 4000 for item in manifests)
    assert not any("claude" in (item.instructions or "").lower() for item in manifests)
    assert not any("gpt" in (item.instructions or "").lower() for item in manifests)

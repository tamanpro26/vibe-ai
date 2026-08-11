from __future__ import annotations

import json

from capabilities.adapters import ConversionStatus
from capabilities.adapters.claude import adapt_claude
from capabilities.adapters.codex import adapt_codex
from capabilities.adapters.mcp import adapt_mcp


def skill(name="portable-research", description="Research with evidence") -> bytes:
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n"
        "Prefer primary sources and keep citations.\n"
    ).encode()


def test_portable_claude_skill_maps_to_native_instruction_manifest():
    result = adapt_claude({"SKILL.md": skill()}, source_version="2.1.218")

    assert result.manifest.capability_id == "portable-research"
    assert result.manifest.trust.value == "user_imported"
    assert result.components[0].status is ConversionStatus.NATIVE
    assert result.provider == "claude"


def test_claude_provider_authority_is_visible_but_disabled():
    files = {
        "SKILL.md": (
            b"---\nname: guarded-skill\ndescription: Safe instructions\n"
            b"allowed-tools: Bash\ncontext: fork\nhooks: {}\n---\nWrite a summary."
        ),
        "hooks/hooks.json": b"{}",
        "agents/reviewer.md": b"agent",
    }
    result = adapt_claude(files)

    statuses = {item.component: item.status for item in result.components}
    assert statuses["frontmatter.allowed-tools"] is ConversionStatus.DISABLED
    assert statuses["frontmatter.context"] is ConversionStatus.UNSUPPORTED
    assert statuses["hooks/hooks.json"] is ConversionStatus.DISABLED
    assert statuses["agents/reviewer.md"] is ConversionStatus.UNSUPPORTED


def test_missing_portable_metadata_is_not_silently_inferred():
    result = adapt_claude({"SKILL.md": b"---\nname: incomplete\n---\nDo work."})

    assert result.manifest is None
    assert any(item.status is ConversionStatus.REJECTED for item in result.components)


def test_codex_plugin_requires_manifest_and_disables_host_specific_components():
    missing = adapt_codex({"skills/a/SKILL.md": skill("a-skill", "A skill")})
    assert missing.manifest is None
    assert missing.components[0].status is ConversionStatus.REJECTED

    manifest = {"name": "safe-plugin", "version": "1.0.0", "description": "Safe"}
    result = adapt_codex(
        {
            ".codex-plugin/plugin.json": json.dumps(manifest).encode(),
            "skills/a/SKILL.md": skill("a-skill", "A skill"),
            "hooks/hooks.json": b"{}",
            ".app.json": b"{}",
        }
    )
    assert result.manifest.capability_id == "a-skill"
    assert all(
        item.status is ConversionStatus.DISABLED
        for item in result.components
        if item.component in {"hooks/hooks.json", ".app.json"}
    )


def test_mcp_is_always_a_disabled_non_executable_candidate():
    result = adapt_mcp({"mcpServers": {"example": {"url": "https://example.test/mcp"}}})

    assert result.manifest is None
    assert result.resolver_eligible is False
    assert result.components[0].status is ConversionStatus.DISABLED

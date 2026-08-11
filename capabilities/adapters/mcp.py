"""MCP declarations remain disabled candidates in Capability Hub v1."""

from __future__ import annotations

from typing import Any

from capabilities.adapters import ComponentReport, ConversionResult, ConversionStatus


def adapt_mcp(declaration: dict[str, Any]) -> ConversionResult:
    del declaration
    return ConversionResult(
        provider="mcp",
        manifest=None,
        resolver_eligible=False,
        components=(
            ComponentReport(
                component="mcpServers",
                status=ConversionStatus.DISABLED,
                reason="MCP declarations are non-executable until a separately approved authenticated adapter ships",
            ),
        ),
    )

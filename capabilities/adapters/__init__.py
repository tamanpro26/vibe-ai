"""Provider packages converted into the stricter VibeAI import profile."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from capabilities.manifests import CapabilityManifest


class ConversionStatus(StrEnum):
    NATIVE = "native"
    ADAPTED = "adapted"
    DISABLED = "disabled"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"


class ComponentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    component: str
    status: ConversionStatus
    reason: str


class ConversionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    provider_version: str | None = None
    adapter_version: str = "vibeai-adapter/1"
    manifest: CapabilityManifest | None = None
    components: tuple[ComponentReport, ...] = ()
    resolver_eligible: bool = True


__all__ = ["ComponentReport", "ConversionResult", "ConversionStatus"]

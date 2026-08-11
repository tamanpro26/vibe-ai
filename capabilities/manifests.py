"""Strict, data-only VibeAI capability manifest validation."""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "vibeai.capability/v1"
MAX_MANIFEST_BYTES = 256 * 1024
MAX_ASSET_BYTES = 512 * 1024
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_ASSETS = 64
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
_INTERPOLATION = re.compile(r"\$\{|%[A-Za-z_][A-Za-z0-9_]*%")
_DRIVE = re.compile(r"^[A-Za-z]:")
_EXECUTABLE_SUFFIXES = {
    ".bat", ".bin", ".cmd", ".com", ".dll", ".exe", ".jar", ".js",
    ".mjs", ".ps1", ".py", ".rb", ".sh", ".so", ".wasm",
}
_EXECUTABLE_PARTS = {"bin", "hooks", "scripts"}


class CapabilityKind(StrEnum):
    INSTRUCTION_SKILL = "instruction_skill"
    APPROVED_ACTION = "approved_action"
    EXTERNAL_SERVICE = "external_service"
    BUNDLE = "bundle"


class TrustState(StrEnum):
    VIBEAI_BUILTIN = "vibeai_builtin"
    VIBEAI_REVIEWED = "vibeai_reviewed"
    COMMUNITY_SOURCE = "community_source"
    USER_IMPORTED = "user_imported"
    UNSUPPORTED = "unsupported"


class RiskClass(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActivationMode(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL_ONLY = "manual_only"
    DISABLED = "disabled"


class PermissionDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=300)
    shared_data: list[str] = Field(default_factory=list, max_length=32)
    mutable_resources: list[str] = Field(default_factory=list, max_length=32)


class ServiceDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=80)
    operations: list[str] = Field(default_factory=list, max_length=32)
    required: bool = True


class DependencyDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    version: str
    required: bool = True

    @field_validator("capability_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _SLUG.fullmatch(value):
            raise ValueError("dependency capability_id must be a lowercase slug")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not _SEMVER.fullmatch(value):
            raise ValueError("dependency version must be semantic")
        return value


class ActivationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: ActivationMode = ActivationMode.MANUAL_ONLY
    intent_tags: list[str] = Field(default_factory=list, max_length=32)
    explicit_triggers: list[str] = Field(default_factory=list, max_length=32)
    required_features: list[str] = Field(default_factory=list, max_length=32)
    priority: int = Field(default=0, ge=-1000, le=1000)
    conflicts: list[str] = Field(default_factory=list, max_length=32)


class CapabilityManifest(BaseModel):
    """Provider-neutral immutable manifest.

    Provider manifests are retained as evidence elsewhere; they are never used
    directly as runtime authority.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)

    schema_version: Literal["vibeai.capability/v1"] = SCHEMA_VERSION
    capability_id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    version: str
    kind: CapabilityKind
    supported_tasks: list[str] = Field(default_factory=list, max_length=64)
    activation: ActivationContract = Field(default_factory=ActivationContract)
    permissions: list[PermissionDeclaration] = Field(default_factory=list, max_length=32)
    services: list[ServiceDeclaration] = Field(default_factory=list, max_length=16)
    dependencies: list[DependencyDeclaration] = Field(default_factory=list, max_length=32)
    trust: TrustState
    risk: RiskClass
    instructions: str | None = Field(default=None, max_length=100_000)
    configuration_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capability_id")
    @classmethod
    def validate_capability_id(cls, value: str) -> str:
        if not _SLUG.fullmatch(value):
            raise ValueError("capability_id must be a lowercase slug")
        return value

    @field_validator("version")
    @classmethod
    def validate_semver(cls, value: str) -> str:
        if not _SEMVER.fullmatch(value):
            raise ValueError("version must be semantic")
        return value

    @field_validator("name", "description", "instructions")
    @classmethod
    def reject_interpolation(cls, value: str | None) -> str | None:
        if value is not None and _INTERPOLATION.search(value):
            raise ValueError("environment interpolation is not allowed")
        return value

    @field_validator("supported_tasks")
    @classmethod
    def normalize_tasks(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().lower().replace(" ", "_") for value in values]
        if any(not value or len(value) > 80 for value in normalized):
            raise ValueError("supported task names must be non-empty and bounded")
        if len(normalized) != len(set(normalized)):
            raise ValueError("supported tasks must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_kind_contract(self) -> "CapabilityManifest":
        if self.kind is CapabilityKind.INSTRUCTION_SKILL and not self.instructions:
            raise ValueError("instruction skills require instructions")
        if self.kind is CapabilityKind.BUNDLE and not self.dependencies:
            raise ValueError("bundles require at least one dependency")
        return self

    @property
    def content_digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return _digest(canonical)


class ManifestAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    content: bytes

    @property
    def digest(self) -> str:
        return _digest(self.content)


class ValidatedPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: CapabilityManifest
    assets: tuple[ManifestAsset, ...] = ()
    source_manifest: bytes
    source_digest: str


def validate_package(
    manifest: CapabilityManifest | dict[str, Any],
    assets: list[ManifestAsset] | tuple[ManifestAsset, ...] = (),
    *,
    source_manifest: bytes | None = None,
) -> ValidatedPackage:
    """Validate a bounded passive package without evaluating or fetching it."""

    parsed = manifest if isinstance(manifest, CapabilityManifest) else CapabilityManifest.model_validate(manifest)
    raw_manifest = source_manifest or json.dumps(
        parsed.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest exceeds size limit")
    if _INTERPOLATION.search(raw_manifest.decode("utf-8", errors="ignore")):
        raise ValueError("environment interpolation is not allowed")
    if len(assets) > MAX_ASSETS:
        raise ValueError("package contains too many assets")

    total = len(raw_manifest)
    seen: set[str] = set()
    validated_assets: list[ManifestAsset] = []
    for asset in assets:
        _validate_passive_path(asset.path)
        key = asset.path.casefold()
        if key in seen:
            raise ValueError(f"case-fold path collision: {asset.path}")
        seen.add(key)
        if len(asset.content) > MAX_ASSET_BYTES:
            raise ValueError(f"asset exceeds size limit: {asset.path}")
        if b"\x00" in asset.content:
            raise ValueError(f"binary asset is unsupported: {asset.path}")
        total += len(asset.content)
        if total > MAX_PACKAGE_BYTES:
            raise ValueError("package exceeds size limit")
        validated_assets.append(asset)

    return ValidatedPackage(
        manifest=parsed,
        assets=tuple(validated_assets),
        source_manifest=raw_manifest,
        source_digest=_digest(raw_manifest),
    )


def _validate_passive_path(raw_path: str) -> None:
    if not raw_path or "\\" in raw_path or _DRIVE.match(raw_path):
        raise ValueError("asset path must be a relative POSIX path")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("asset path may not escape its package")
    if len(path.parts) > 12 or len(raw_path) > 240:
        raise ValueError("asset path is too deep or long")
    if any(part.casefold() in _EXECUTABLE_PARTS for part in path.parts):
        raise ValueError("executable package directories are unsupported")
    if path.suffix.casefold() in _EXECUTABLE_SUFFIXES:
        raise ValueError("executable assets are unsupported")


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"

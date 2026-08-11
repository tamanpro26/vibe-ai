"""Typed, immutable external action contracts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_REPOSITORY_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_INTERPOLATION = re.compile(r"\$\{|%[A-Za-z_][A-Za-z0-9_]*%")


class GitHubIssueComment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    repository_id: str
    issue_number: int = Field(gt=0, le=2_147_483_647)
    body: str = Field(min_length=1, max_length=10_000)

    @field_validator("repository_id")
    @classmethod
    def numeric_repository_id(cls, value: str) -> str:
        if not _REPOSITORY_ID.fullmatch(value):
            raise ValueError("repository target must be an immutable numeric ID")
        return value

    @field_validator("body")
    @classmethod
    def reject_secret_interpolation(cls, value: str) -> str:
        if _INTERPOLATION.search(value):
            raise ValueError("credential interpolation is forbidden")
        return value


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    capability_version_id: str
    capability_digest: str
    connection_id: str
    project_id: str | None = None
    chat_id: str | None = None
    operation: Literal["github.issue.comment"]
    arguments: GitHubIssueComment
    shared_data: list[str] = Field(max_length=16)
    mutable_resources: list[str] = Field(max_length=16)
    idempotency_key: str

    @field_validator("capability_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not _DIGEST.fullmatch(value):
            raise ValueError("capability digest must be SHA-256")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def safe_idempotency_key(cls, value: str) -> str:
        if not _SAFE_KEY.fullmatch(value):
            raise ValueError("invalid idempotency key")
        return value

    @property
    def request_digest(self) -> str:
        value = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        return f"sha256:{hashlib.sha256(value).hexdigest()}"

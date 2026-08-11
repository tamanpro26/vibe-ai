"""Envelope-encrypted, owner-bound service credentials."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class CredentialBinding:
    owner_id: str
    provider: str
    connection_id: str


class CredentialEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_version: int = Field(default=1, frozen=True)
    key_id: str
    environment: str
    binding_digest: str
    wrapped_key_nonce: str
    wrapped_key: str
    payload_nonce: str
    ciphertext: str


class CredentialVault:
    """AES-256-GCM envelope encryption with authenticated ownership metadata."""

    def __init__(self, keys: dict[str, str | bytes], active_key_id: str, environment: str) -> None:
        if active_key_id not in keys:
            raise ValueError("active credential key is missing")
        self._keys = {key_id: _decode_key(value) for key_id, value in keys.items()}
        self._active_key_id = active_key_id
        self._environment = environment

    def encrypt(self, plaintext: bytes, binding: CredentialBinding) -> CredentialEnvelope:
        if not plaintext:
            raise ValueError("credential plaintext must not be empty")
        data_key = os.urandom(32)
        payload_nonce = os.urandom(12)
        wrapped_nonce = os.urandom(12)
        aad = self._aad(binding)
        ciphertext = AESGCM(data_key).encrypt(payload_nonce, plaintext, aad)
        wrapped = AESGCM(self._keys[self._active_key_id]).encrypt(
            wrapped_nonce, data_key, aad + b"|data-key"
        )
        return CredentialEnvelope(
            key_id=self._active_key_id,
            environment=self._environment,
            binding_digest=_binding_digest(aad),
            wrapped_key_nonce=_encode(wrapped_nonce),
            wrapped_key=_encode(wrapped),
            payload_nonce=_encode(payload_nonce),
            ciphertext=_encode(ciphertext),
        )

    def decrypt(self, envelope: CredentialEnvelope, binding: CredentialBinding) -> bytes:
        try:
            if envelope.environment != self._environment:
                raise ValueError("credential environment mismatch")
            master_key = self._keys[envelope.key_id]
            aad = self._aad(binding)
            if envelope.binding_digest != _binding_digest(aad):
                raise ValueError("credential binding mismatch")
            data_key = AESGCM(master_key).decrypt(
                _decode(envelope.wrapped_key_nonce),
                _decode(envelope.wrapped_key),
                aad + b"|data-key",
            )
            return AESGCM(data_key).decrypt(
                _decode(envelope.payload_nonce), _decode(envelope.ciphertext), aad
            )
        except (InvalidTag, KeyError, ValueError) as exc:
            raise ValueError("credential envelope authentication failed") from exc

    def rotate(self, envelope: CredentialEnvelope, binding: CredentialBinding) -> CredentialEnvelope:
        plaintext = self.decrypt(envelope, binding)
        return self.encrypt(plaintext, binding)

    def _aad(self, binding: CredentialBinding) -> bytes:
        return json.dumps(
            {
                "connection_id": binding.connection_id,
                "environment": self._environment,
                "owner_id": binding.owner_id,
                "provider": binding.provider,
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


def _decode_key(value: str | bytes) -> bytes:
    if isinstance(value, str):
        try:
            value = base64.urlsafe_b64decode(value.encode())
        except Exception as exc:  # pragma: no cover - implementation detail
            raise ValueError("credential key must be urlsafe base64") from exc
    if len(value) != 32:
        raise ValueError("credential master keys must be 32 bytes")
    return value


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode())


def _binding_digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"

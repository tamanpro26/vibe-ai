from __future__ import annotations

import base64
import os

import pytest

from capabilities.credentials import CredentialBinding, CredentialVault
from capabilities.store import CapabilityStore


def key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def test_credentials_are_envelope_encrypted_and_context_bound():
    vault = CredentialVault({"v1": key()}, active_key_id="v1", environment="test")
    binding = CredentialBinding("user-1", "github", "connection-1")
    envelope = vault.encrypt(b"secret-token", binding)

    assert b"secret-token" not in envelope.model_dump_json().encode()
    assert vault.decrypt(envelope, binding) == b"secret-token"

    with pytest.raises(ValueError):
        vault.decrypt(envelope, CredentialBinding("user-2", "github", "connection-1"))


def test_tampering_wrong_environment_and_key_fail_closed():
    keys = {"v1": key()}
    binding = CredentialBinding("user-1", "github", "connection-1")
    envelope = CredentialVault(keys, "v1", "test").encrypt(b"token", binding)

    tampered = envelope.model_copy(update={"ciphertext": envelope.ciphertext[:-2] + "AA"})
    with pytest.raises(ValueError):
        CredentialVault(keys, "v1", "test").decrypt(tampered, binding)
    with pytest.raises(ValueError):
        CredentialVault(keys, "v1", "production").decrypt(envelope, binding)


def test_key_rotation_rewraps_without_exposing_plaintext():
    keys = {"v1": key(), "v2": key()}
    binding = CredentialBinding("user-1", "github", "connection-1")
    original = CredentialVault(keys, "v1", "test").encrypt(b"token", binding)
    rotated = CredentialVault(keys, "v2", "test").rotate(original, binding)

    assert rotated.key_id == "v2"
    assert CredentialVault(keys, "v2", "test").decrypt(rotated, binding) == b"token"


@pytest.mark.asyncio
async def test_encrypted_credential_persistence_rotation_and_revocation(tmp_path):
    database = tmp_path / "capabilities.db"
    store = CapabilityStore(f"sqlite+aiosqlite:///{database}")
    await store.init()
    keys = {"v1": key(), "v2": key()}
    binding = CredentialBinding("user-1", "github", "connection-1")
    first = CredentialVault(keys, "v1", "test").encrypt(b"never-store-this", binding)
    await store.put_credential("user-1", "github", "connection-1", first)

    assert b"never-store-this" not in database.read_bytes()
    loaded = await store.get_credential("user-1", "github", "connection-1")
    rotated = CredentialVault(keys, "v2", "test").rotate(loaded, binding)
    await store.put_credential("user-1", "github", "connection-1", rotated)
    assert (await store.get_credential("user-1", "github", "connection-1")).key_id == "v2"

    await store.revoke_credential("user-1", "connection-1")
    with pytest.raises(PermissionError):
        await store.get_credential("user-1", "github", "connection-1")

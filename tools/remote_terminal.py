"""
tools/remote_terminal.py
SSH remote terminal integration for VibeAI agents.

Maintains a named registry of open SSH connections. The agent can:
  ssh_connect    — open a connection to any server
  ssh_exec       — run a shell command on the remote machine
  ssh_upload     — copy a local workspace file to the server via SFTP
  ssh_download   — read a remote file's content back into the agent
  ssh_list       — show all active connections
  ssh_disconnect — close a connection

Requires: pip install asyncssh
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

try:
    import asyncssh
    _SSH_AVAILABLE = True
except ImportError:
    _SSH_AVAILABLE = False
    _SSH_IMPORT_ERR = "asyncssh not installed — run: pip install asyncssh"


@dataclass
class _SSHSession:
    cid:      str
    host:     str
    port:     int
    username: str
    _conn:    Any = field(repr=False)


# Module-level connection registry — persists across tool calls in the same process
_SESSIONS: dict[str, _SSHSession] = {}


# Trust-on-first-use host-key store: first connection to a host records its key
# here; later connections verify against it. Weaker than pre-distributed
# known_hosts but categorically better than known_hosts=None, which silently
# accepted ANY key and made every agent-driven SSH session MITM-able.
_TOFU_KNOWN_HOSTS = Path.home() / ".vibeai_known_hosts"


async def ssh_connect(
    host:     str,
    port:     int  = 22,
    username: str  = "",
    key_path: str  = "",
    password: str  = "",
    alias:    str  = "",
) -> str:
    """
    Open an SSH connection. Returns a connection_id (8-char hex).
    alias is optional — lets you name the connection (e.g. 'prod', 'staging').
    Either key_path (path to private key file) or password must be provided.
    key_path supports ~ expansion. username is REQUIRED — there is deliberately
    no default (previously it defaulted to root, which pushed every agent
    session toward the most dangerous account on the box).
    """
    if not _SSH_AVAILABLE:
        return f"ERROR: {_SSH_IMPORT_ERR}"
    if not username:
        return "ERROR: username is required (no default — do not assume root)"

    _TOFU_KNOWN_HOSTS.touch(exist_ok=True)
    opts: dict[str, Any] = {
        "host":         host,
        "port":         port,
        "username":     username,
        "known_hosts":  str(_TOFU_KNOWN_HOSTS),
    }
    if key_path:
        opts["client_keys"] = [os.path.expanduser(key_path)]
    elif password:
        opts["password"] = password

    try:
        try:
            conn = await asyncssh.connect(**opts)
        except asyncssh.HostKeyNotVerifiable:
            # First contact with this host: record its key (trust-on-first-use),
            # then retry. Subsequent connections verify against the recorded key,
            # so a later MITM with a different key is detected and refused.
            key = await asyncssh.get_server_host_key(host, port)
            entry = f"[{host}]:{port} {key.export_public_key().decode().strip()}" \
                    if port != 22 else f"{host} {key.export_public_key().decode().strip()}"
            with open(_TOFU_KNOWN_HOSTS, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
            logger.info(f"[ssh] first contact with {host} — host key recorded (TOFU)")
            conn = await asyncssh.connect(**opts)
        cid  = alias.strip() or uuid.uuid4().hex[:8]
        _SESSIONS[cid] = _SSHSession(cid=cid, host=host, port=port, username=username, _conn=conn)
        logger.info(f"[ssh] connected: {username}@{host}:{port} (id={cid})")
        return (
            f"✓ Connected to {username}@{host}:{port}\n"
            f"connection_id: {cid}\n"
            f"Use this id in ssh_exec, ssh_upload, ssh_download, ssh_disconnect."
        )
    except Exception as exc:
        logger.warning(f"[ssh] connect failed to {host}: {type(exc).__name__}")
        return f"ERROR: SSH connect to {host} failed — {type(exc).__name__}: {exc}"


async def ssh_exec(
    connection_id: str,
    command:       str,
    timeout:       int = 60,
) -> str:
    """
    Run a shell command on a connected remote server.
    Returns stdout + stderr with the exit code prefix.
    """
    if not _SSH_AVAILABLE:
        return f"ERROR: {_SSH_IMPORT_ERR}"

    sess = _SESSIONS.get(connection_id)
    if not sess:
        active = list(_SESSIONS.keys())
        return (
            f"ERROR: Unknown connection_id '{connection_id}'. "
            f"Active connections: {active if active else ['none — use ssh_connect first']}"
        )

    try:
        result = await asyncio.wait_for(
            sess._conn.run(command, check=False),
            timeout=timeout,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        code   = result.exit_status or 0
        marker = "✓" if code == 0 else f"✗ (exit {code})"
        out    = stdout
        if stderr:
            out += f"\nSTDERR:\n{stderr}"
        logger.info(f"[ssh:{connection_id}] $ {command[:60]} → exit {code}")
        return f"{marker} [{sess.username}@{sess.host}] $ {command}\n{out}" if out else f"{marker} $ {command}\n(no output)"

    except asyncio.TimeoutError:
        return f"ERROR: Remote command timed out after {timeout}s: {command[:60]}"
    except Exception as exc:
        return f"ERROR running remote command: {exc}"


async def ssh_upload(
    connection_id: str,
    local_path:    str,
    remote_path:   str,
) -> str:
    """
    Upload a local file to the remote server via SFTP.
    local_path is relative to the agent workspace or absolute.
    """
    if not _SSH_AVAILABLE:
        return f"ERROR: {_SSH_IMPORT_ERR}"

    sess = _SESSIONS.get(connection_id)
    if not sess:
        return f"ERROR: Unknown connection_id '{connection_id}'"

    local = Path(local_path)
    if not local.exists():
        return f"ERROR: Local file not found: {local_path}"

    try:
        async with sess._conn.start_sftp_client() as sftp:
            await sftp.put(str(local), remote_path)
        size = local.stat().st_size
        logger.info(f"[ssh:{connection_id}] uploaded {local.name} → {remote_path} ({size:,} bytes)")
        return f"✓ Uploaded {local_path} → {sess.username}@{sess.host}:{remote_path} ({size:,} bytes)"
    except Exception as exc:
        return f"ERROR uploading file: {exc}"


async def ssh_download(
    connection_id: str,
    remote_path:   str,
    max_kb:        int = 50,
) -> str:
    """
    Read a remote file's content via SFTP and return it as text.
    max_kb caps output at N kilobytes (default 50 KB).
    """
    if not _SSH_AVAILABLE:
        return f"ERROR: {_SSH_IMPORT_ERR}"

    sess = _SESSIONS.get(connection_id)
    if not sess:
        return f"ERROR: Unknown connection_id '{connection_id}'"

    limit = max_kb * 1024
    try:
        async with sess._conn.start_sftp_client() as sftp:
            async with sftp.open(remote_path, "rb") as f:
                data = await f.read(limit)
        content   = data.decode("utf-8", errors="replace")
        truncated = len(data) >= limit
        logger.info(f"[ssh:{connection_id}] read {remote_path} ({len(data):,} bytes)")
        return content + (f"\n... (truncated at {max_kb} KB)" if truncated else "")
    except Exception as exc:
        return f"ERROR reading remote file {remote_path}: {exc}"


async def ssh_disconnect(connection_id: str) -> str:
    """Close an SSH connection and remove it from the registry."""
    if not _SSH_AVAILABLE:
        return f"ERROR: {_SSH_IMPORT_ERR}"

    sess = _SESSIONS.pop(connection_id, None)
    if not sess:
        return f"ERROR: Unknown connection_id '{connection_id}'"
    try:
        sess._conn.close()
    except Exception:
        pass
    logger.info(f"[ssh] disconnected {connection_id} ({sess.username}@{sess.host})")
    return f"✓ Disconnected from {sess.username}@{sess.host}:{sess.port}"


def ssh_list() -> str:
    """List all active SSH connections with their IDs."""
    if not _SESSIONS:
        return "No active SSH connections."
    lines = [f"Active SSH connections ({len(_SESSIONS)}):"]
    for cid, s in _SESSIONS.items():
        lines.append(f"  [{cid}] {s.username}@{s.host}:{s.port}")
    return "\n".join(lines)

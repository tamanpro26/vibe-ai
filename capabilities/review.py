"""Side-effect-free package scanning and digest-bound review lifecycle."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict

from capabilities.adapters import ConversionResult, ConversionStatus
from capabilities.adapters.claude import adapt_claude
from capabilities.adapters.codex import adapt_codex
from capabilities.credentials import CredentialBinding, CredentialEnvelope, CredentialVault
from capabilities.models import CapabilityImportCandidate
from capabilities.store import CapabilityStore

_DRIVE = re.compile(r"^[A-Za-z]:")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_EXECUTABLE_SUFFIXES = {
    ".bat", ".bin", ".cmd", ".com", ".dll", ".exe", ".jar", ".js",
    ".mjs", ".ps1", ".py", ".rb", ".sh", ".so", ".wasm",
}
_DENIED_NAMES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "requirements.txt",
    "pyproject.toml", "cargo.toml", "go.mod",
}
_INTERPOLATION = re.compile(r"\$\{|%[A-Za-z_][A-Za-z0-9_]*%")
_HOSTILE_INSTRUCTIONS = re.compile(
    r"ignore (?:all )?(?:previous|system)|reveal (?:the )?(?:secret|credential)|"
    r"ask (?:the )?user for (?:their )?(?:password|api token)|"
    r"without confirmation|approval is implied|bypass (?:policy|consent)",
    re.IGNORECASE,
)


class ScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_digest: str
    evidence_digest: str
    conversion: ConversionResult
    evidence: dict[str, Any]


class PackageImporter:
    def __init__(
        self,
        *,
        max_archive_bytes: int = 5 * 1024 * 1024,
        max_file_bytes: int = 512 * 1024,
        max_total_bytes: int = 2 * 1024 * 1024,
        max_files: int = 128,
        max_depth: int = 12,
    ) -> None:
        self.max_archive_bytes = max_archive_bytes
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.max_files = max_files
        self.max_depth = max_depth

    def scan_archive(self, archive: bytes, provider: str) -> ScanResult:
        if not archive or len(archive) > self.max_archive_bytes:
            raise ValueError("archive size is outside allowed limits")
        files = self._read_zip(archive)
        if provider == "claude":
            conversion = adapt_claude(files)
        elif provider == "codex":
            conversion = adapt_codex(files)
        else:
            raise ValueError("unsupported provider adapter")
        if conversion.manifest is None or any(
            item.status is ConversionStatus.REJECTED for item in conversion.components
        ):
            reason = next(
                (item.reason for item in conversion.components if item.status is ConversionStatus.REJECTED),
                "package is not importable",
            )
            raise ValueError(reason)
        instructions = conversion.manifest.instructions or ""
        if _HOSTILE_INSTRUCTIONS.search(instructions):
            raise ValueError("untrusted instruction attempts to claim policy, secret, or consent authority")
        file_digests = {
            path: _digest(content) for path, content in sorted(files.items())
        }
        evidence = {
            "provider": provider,
            "adapter_version": conversion.adapter_version,
            "file_digests": file_digests,
            "components": [item.model_dump(mode="json") for item in conversion.components],
            "declared_permissions": conversion.manifest.model_dump(mode="json")["permissions"],
            "declared_services": conversion.manifest.model_dump(mode="json")["services"],
            "processes_started": 0,
            "network_requests": 0,
        }
        canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        return ScanResult(
            source_digest=_digest(archive),
            evidence_digest=_digest(canonical),
            conversion=conversion,
            evidence=evidence,
        )

    def _read_zip(self, archive: bytes) -> dict[str, bytes]:
        import io

        files: dict[str, bytes] = {}
        total = 0
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                infos = [item for item in bundle.infolist() if not item.is_dir()]
                if len(infos) > self.max_files:
                    raise ValueError("archive has too many files")
                raw_paths = [item.filename for item in infos]
                prefix = _common_root(raw_paths)
                seen: set[str] = set()
                for info in infos:
                    _validate_archive_path(info.filename, self.max_depth + 1)
                    path = info.filename[len(prefix) :] if prefix else info.filename
                    _validate_archive_path(path, self.max_depth)
                    if stat.S_ISLNK(info.external_attr >> 16):
                        raise ValueError("archive symlinks are unsupported")
                    pure = PurePosixPath(path)
                    if pure.suffix.casefold() in _EXECUTABLE_SUFFIXES:
                        raise ValueError("executable archive content is unsupported")
                    if pure.name.casefold() in _DENIED_NAMES:
                        raise ValueError("dependency lifecycle files are unsupported")
                    if info.file_size > self.max_file_bytes:
                        raise ValueError("file size exceeds import limit")
                    total += info.file_size
                    if total > self.max_total_bytes:
                        raise ValueError("decompressed package size exceeds import limit")
                    key = path.casefold()
                    if key in seen:
                        raise ValueError("case-fold path collision")
                    seen.add(key)
                    content = bundle.read(info)
                    if b"\x00" in content:
                        raise ValueError("binary package content is unsupported")
                    if _INTERPOLATION.search(content.decode("utf-8", errors="ignore")):
                        raise ValueError("environment or credential interpolation is unsupported")
                    files[path] = content
        except zipfile.BadZipFile as exc:
            raise ValueError("invalid ZIP archive") from exc
        if not files:
            raise ValueError("archive contains no importable files")
        return files


class GitHubArchiveAcquirer:
    """Fetch a pinned public GitHub archive with a strict redirect allowlist."""

    _HOSTS = {"api.github.com", "github.com", "codeload.github.com"}

    def __init__(
        self, client: httpx.AsyncClient | None = None, *, max_archive_bytes: int = 5 * 1024 * 1024
    ) -> None:
        self.client = client
        self.max_archive_bytes = max_archive_bytes

    async def acquire(self, repository: str, commit_sha: str) -> bytes:
        if not _REPOSITORY.fullmatch(repository) or not _COMMIT.fullmatch(commit_sha):
            raise ValueError("GitHub imports require owner/repo and a full commit SHA")
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=15, follow_redirects=False)
        url = f"https://api.github.com/repos/{repository}/zipball/{commit_sha}"
        try:
            for _ in range(3):
                parsed = urlparse(url)
                if parsed.scheme != "https" or parsed.hostname not in self._HOSTS:
                    raise ValueError("GitHub archive redirected outside the allowlist")
                async with client.stream(
                    "GET",
                    url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("GitHub archive redirect is missing a destination")
                        url = str(response.url.join(location))
                        continue
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > self.max_archive_bytes:
                            raise ValueError("GitHub archive exceeds size limit")
                    return bytes(content)
            raise ValueError("GitHub archive exceeded redirect limit")
        finally:
            if owns_client:
                await client.aclose()


class EncryptedQuarantine:
    def __init__(self, root: Path, vault: CredentialVault) -> None:
        self.root = root
        self.vault = vault
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, candidate_id: str, owner_id: str, archive: bytes) -> None:
        binding = CredentialBinding(owner_id, "quarantine", candidate_id)
        envelope = self.vault.encrypt(archive, binding)
        self._path(candidate_id).write_text(envelope.model_dump_json(), encoding="utf-8")

    def read(self, candidate_id: str, owner_id: str) -> bytes:
        envelope = CredentialEnvelope.model_validate_json(
            self._path(candidate_id).read_text(encoding="utf-8")
        )
        return self.vault.decrypt(
            envelope, CredentialBinding(owner_id, "quarantine", candidate_id)
        )

    def delete(self, candidate_id: str) -> None:
        path = self._path(candidate_id)
        if path.exists():
            path.unlink()

    def exists(self, candidate_id: str) -> bool:
        return self._path(candidate_id).exists()

    def _path(self, candidate_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f-]{36}", candidate_id):
            raise ValueError("invalid quarantine candidate id")
        return self.root / f"{candidate_id}.json"


class ReviewService:
    def __init__(
        self, store: CapabilityStore, importer: PackageImporter, quarantine: EncryptedQuarantine
    ) -> None:
        self.store = store
        self.importer = importer
        self.quarantine = quarantine

    async def submit_github(
        self,
        owner_id: str,
        archive: bytes,
        provider: str,
        repository: str,
        commit_sha: str,
    ) -> CapabilityImportCandidate:
        if not _REPOSITORY.fullmatch(repository) or not _COMMIT.fullmatch(commit_sha):
            raise ValueError("GitHub imports require owner/repo and a full commit SHA")
        scan = self.importer.scan_archive(archive, provider)
        candidate_id = str(uuid.uuid4())
        self.quarantine.put(candidate_id, owner_id, archive)
        try:
            candidate = await self.store.create_import_candidate(
                candidate_id=candidate_id,
                owner_id=owner_id,
                provider=provider,
                repository=repository,
                commit_sha=commit_sha,
                source_digest=scan.source_digest,
                evidence_digest=scan.evidence_digest,
                adapter_version=scan.conversion.adapter_version,
                manifest=scan.conversion.manifest.model_dump(mode="json"),
                compatibility_report=scan.conversion.model_dump(mode="json", exclude={"manifest"}),
                evidence=scan.evidence,
            )
            if candidate.id != candidate_id:
                self.quarantine.delete(candidate_id)
            return candidate
        except Exception:
            self.quarantine.delete(candidate_id)
            raise

    async def submit_github_from_source(
        self,
        owner_id: str,
        provider: str,
        repository: str,
        commit_sha: str,
        acquirer: GitHubArchiveAcquirer | None = None,
    ) -> CapabilityImportCandidate:
        archive = await (acquirer or GitHubArchiveAcquirer()).acquire(repository, commit_sha)
        return await self.submit_github(
            owner_id, archive, provider, repository, commit_sha
        )

    async def approve(
        self,
        reviewer_id: str,
        candidate_id: str,
        source_digest: str,
        evidence_digest: str,
    ):
        if not await self.store.has_role(reviewer_id, "reviewer"):
            raise PermissionError("reviewer role required")
        version = await self.store.approve_import_candidate(
            reviewer_id, candidate_id, source_digest, evidence_digest
        )
        self.quarantine.delete(candidate_id)
        await self.store.append_audit(
            reviewer_id,
            "capability.review.approved",
            candidate_id,
            {"source_digest": source_digest, "evidence_digest": evidence_digest},
        )
        return version

    async def revoke(self, reviewer_id: str, version_id: str, reason: str) -> None:
        if not await self.store.has_role(reviewer_id, "reviewer"):
            raise PermissionError("reviewer role required")
        await self.store.revoke_version(version_id, reviewer_id, reason)
        await self.store.append_audit(
            reviewer_id,
            "capability.review.revoked",
            version_id,
            {"reason": reason},
        )

    async def reject(self, reviewer_id: str, candidate_id: str, reason: str) -> None:
        if not await self.store.has_role(reviewer_id, "reviewer"):
            raise PermissionError("reviewer role required")
        await self.store.reject_import_candidate(candidate_id, reviewer_id, reason)
        self.quarantine.delete(candidate_id)
        await self.store.append_audit(
            reviewer_id,
            "capability.review.rejected",
            candidate_id,
            {"reason": reason},
        )

    async def supersede(
        self, reviewer_id: str, previous_version_id: str, replacement_version_id: str
    ) -> None:
        if not await self.store.has_role(reviewer_id, "reviewer"):
            raise PermissionError("reviewer role required")
        await self.store.supersede_version(
            previous_version_id, replacement_version_id, reviewer_id
        )


def _common_root(paths: list[str]) -> str:
    first_parts = [PurePosixPath(path).parts for path in paths]
    if first_parts and all(len(parts) > 1 and parts[0] == first_parts[0][0] for parts in first_parts):
        return f"{first_parts[0][0]}/"
    return ""


def _validate_archive_path(path: str, max_depth: int) -> None:
    if not path or "\\" in path or _DRIVE.match(path):
        raise ValueError("archive path must be relative POSIX")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("archive path escapes package root")
    if len(pure.parts) > max_depth or len(path) > 240:
        raise ValueError("archive path exceeds depth or length limit")
    if any(part.casefold() in {".git", "node_modules", "bin", "scripts"} for part in pure.parts):
        raise ValueError("executable or hidden package directory is unsupported")


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"

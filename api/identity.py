"""Verified end-user identity for security-sensitive backend routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from config.settings import settings

_ALGORITHMS = ["RS256"]


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    session_id: str | None
    claims: dict[str, Any]

    def has_recent_step_up(self, max_age_seconds: int = 600) -> bool:
        reauthenticated_at = self.claims.get("reauthenticated_at")
        if isinstance(reauthenticated_at, (int, float)):
            import time

            return 0 <= time.time() - float(reauthenticated_at) <= max_age_seconds
        factor_age = self.claims.get("fva")
        if isinstance(factor_age, list) and len(factor_age) > 1:
            second_factor_minutes = factor_age[1]
            return (
                isinstance(second_factor_minutes, int)
                and 0 <= second_factor_minutes <= max_age_seconds // 60
            )
        return False


class IdentityVerifier:
    """Validate Clerk JWTs independently from the browser/Vercel boundary."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        authorized_parties: set[str],
        jwks_url: str = "",
        leeway_seconds: int = 60,
        static_key: Any = None,
    ) -> None:
        if not issuer or not audience or not authorized_parties:
            raise ValueError("identity issuer, audience, and authorized parties are required")
        if static_key is None and not jwks_url:
            raise ValueError("identity JWKS URL is required")
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.authorized_parties = authorized_parties
        self.leeway_seconds = max(0, min(leeway_seconds, 120))
        self.static_key = static_key
        self.jwks_client = (
            None
            if static_key is not None
            else PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=5)
        )

    def verify(self, token: str) -> Principal:
        if not token or len(token) > 16_384:
            raise ValueError("missing or oversized user token")
        try:
            key = self.static_key
            if key is None:
                key = self.jwks_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=_ALGORITHMS,
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway_seconds,
                options={
                    "require": ["sub", "iss", "aud", "azp", "iat", "nbf", "exp"],
                    "verify_signature": True,
                },
            )
            subject = claims.get("sub")
            authorized_party = claims.get("azp")
            if not isinstance(subject, str) or not subject or len(subject) > 255:
                raise ValueError("invalid identity subject")
            if authorized_party not in self.authorized_parties:
                raise ValueError("unauthorized token party")
            return Principal(
                subject=subject,
                session_id=claims.get("sid") if isinstance(claims.get("sid"), str) else None,
                claims=dict(claims),
            )
        except (jwt.PyJWTError, ValueError, OSError) as exc:
            raise ValueError("invalid end-user identity") from exc


_verifier: IdentityVerifier | None = None


def get_identity_verifier() -> IdentityVerifier:
    global _verifier
    if _verifier is None:
        parties = {item.strip() for item in settings.clerk_authorized_parties.split(",") if item.strip()}
        _verifier = IdentityVerifier(
            issuer=settings.clerk_issuer,
            audience=settings.clerk_audience,
            authorized_parties=parties,
            jwks_url=settings.clerk_jwks_url,
            leeway_seconds=settings.clerk_jwt_leeway_seconds,
        )
    return _verifier


async def get_current_principal(
    x_vibe_user_token: str | None = Header(default=None, alias="X-Vibe-User-Token"),
) -> Principal:
    if not x_vibe_user_token:
        raise HTTPException(status_code=401, detail="Verified user session required")
    try:
        return get_identity_verifier().verify(x_vibe_user_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired user session") from exc


async def get_optional_principal(
    x_vibe_user_token: str | None = Header(default=None, alias="X-Vibe-User-Token"),
) -> Principal | None:
    if not x_vibe_user_token:
        return None
    try:
        return get_identity_verifier().verify(x_vibe_user_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired user session") from exc

from datetime import timedelta
from typing import Any

import jwt
from fastapi import HTTPException
from jwt import PyJWKClient
from pydantic import ValidationError

from ops.config import Settings
from ops.domain import Principal, utcnow


def issue_token(settings: Settings, principal: Principal) -> str:
    if settings.environment == "production":
        raise ValueError("Local tokens are disabled in production; use OIDC")
    now = utcnow()
    return jwt.encode(
        {
            **principal.model_dump(),
            "sub": principal.subject,
            "iat": now,
            "exp": now + timedelta(hours=1),
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
        },
        settings.signing_key.get_secret_value(),
        algorithm="HS256",
    )


class Authenticator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jwks = (
            PyJWKClient(settings.oidc_jwks_url, timeout=5) if (settings.oidc_jwks_url) else None
        )

    def decode(self, token: str) -> Principal:
        try:
            key: Any = self.settings.signing_key.get_secret_value()
            algorithm = "HS256"
            if self.jwks:
                key = self.jwks.get_signing_key_from_jwt(token).key
                algorithm = "RS256"
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=self.settings.auth_audience,
                issuer=self.settings.auth_issuer,
                options={"require": ["exp", "iat", "sub", "tenant_id", "role", "services"]},
            )
            return Principal(
                subject=claims["sub"],
                tenant_id=claims["tenant_id"],
                role=claims["role"],
                services=claims["services"],
                tools=claims.get("tools", []),
            )
        except (jwt.PyJWTError, ValidationError, KeyError, TypeError) as exc:
            raise HTTPException(401, "Invalid or expired access token") from exc


def require_role(principal: Principal, allowed: set[str]) -> None:
    if principal.role not in allowed:
        raise HTTPException(403, "Role does not permit this operation")


def require_service(principal: Principal, service: str) -> None:
    if service not in principal.services and "*" not in principal.services:
        raise HTTPException(403, "Service is outside your scope")


def require_tool(principal: Principal, tool: str) -> None:
    if tool not in principal.tools and "*" not in principal.tools:
        raise PermissionError(f"Tool is outside your scope: {tool}")

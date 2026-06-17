from __future__ import annotations

from dataclasses import dataclass

import jwt

from src.core.settings import Settings


@dataclass
class AuthPrincipal:
    subject: str
    scopes: set[str]


def decode_jwt_token(token: str, settings: Settings) -> AuthPrincipal:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    sub = str(payload.get("sub") or "")
    scopes = set(payload.get("scopes") or [])
    if not sub:
        raise ValueError("invalid_sub")
    return AuthPrincipal(subject=sub, scopes=scopes)


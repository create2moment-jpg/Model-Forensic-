"""Authentication for server mode (``server.auth`` in model-forensic.yaml). Local mode needs none.

* ``header`` — behind an authenticating reverse proxy (oauth2-proxy with Entra ID, Azure App Service
  "Easy Auth", …): the proxy sets the user's email/UPN in a trusted header (default ``X-Forwarded-Email``;
  App Service: ``X-MS-CLIENT-PRINCIPAL-NAME``). Only enable when the app is reachable *only* through the proxy.
* ``oidc`` — the app validates an ``Authorization: Bearer`` JWT itself (Entra ID or any OIDC issuer): signature
  against the issuer's JWKS, issuer, audience, expiry. Needs ``pip install 'model-forensic[server]'`` (PyJWT).

Allowed users: ``allowed_domains`` (e-mail domains) and/or ``allowed_users``; ``admins`` may change settings.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from model_forensic.settings import ServerConfig
from model_forensic.shared.identity import User

OPEN_PATHS = ("/api/v1/health",)


class AuthError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Authenticator:
    def __init__(self, cfg: ServerConfig) -> None:
        self.cfg = cfg
        self._jwks: Any = None
        self._jwks_at = 0.0

    def _allowed(self, uid: str) -> None:
        domains = [d.lower().lstrip("@") for d in self.cfg.allowed_domains]
        users = [u.lower() for u in self.cfg.allowed_users]
        if not domains and not users:
            return
        low = uid.lower()
        if low in users or ("@" in low and low.rsplit("@", 1)[1] in domains):
            return
        raise AuthError(403, f"{uid} is not allowed to use this instance")

    def user(self, headers: Any) -> User:
        mode = self.cfg.auth
        if mode == "none":
            return User("anonymous", "anonymous", admin=False)
        if mode == "header":
            uid = (headers.get(self.cfg.trusted_header) or "").strip()
            if not uid:
                raise AuthError(401, f"missing {self.cfg.trusted_header} (is the authenticating proxy in front?)")
            name = (headers.get(self.cfg.name_header) or uid) if self.cfg.name_header else uid
        elif mode == "oidc":
            auth = headers.get("authorization") or ""
            if not auth.lower().startswith("bearer "):
                raise AuthError(401, "bearer token required")
            claims = self.verify(auth[7:].strip())
            uid = str(
                claims.get("preferred_username") or claims.get("upn") or claims.get("email") or claims.get("oid") or ""
            )
            name = str(claims.get("name") or uid)
            if not uid:
                raise AuthError(401, "token has no user claim")
        else:
            raise AuthError(500, f"unknown auth mode {mode!r}")
        self._allowed(uid)
        return User(uid, name, admin=uid.lower() in {a.lower() for a in self.cfg.admins})

    def _jwk_set(self) -> Any:
        import jwt

        if self._jwks is None or time.time() - self._jwks_at > 3600:
            if not self.cfg.oidc_issuer.startswith("https://"):
                raise AuthError(500, "oidc_issuer must be an https:// URL")
            with urllib.request.urlopen(  # noqa: S310 - https enforced above
                self.cfg.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=10
            ) as r:
                meta = json.loads(r.read())
            self._jwks = jwt.PyJWKClient(meta["jwks_uri"])
            self._jwks_at = time.time()
        return self._jwks

    def verify(self, token: str) -> dict[str, Any]:
        try:
            import jwt
        except ImportError as exc:
            raise AuthError(500, "oidc auth needs PyJWT: pip install 'model-forensic[server]'") from exc
        try:
            key = self._jwk_set().get_signing_key_from_jwt(token).key
            claims: dict[str, Any] = jwt.decode(
                token, key, algorithms=["RS256"], audience=self.cfg.oidc_audience, issuer=self.cfg.oidc_issuer
            )
        except Exception as exc:
            raise AuthError(401, f"invalid token: {type(exc).__name__}") from exc
        return claims

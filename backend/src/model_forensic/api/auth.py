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


# --- local mode, opened beyond this machine (`mf serve --remote`): one access token -----------------------------------
TOKEN_COOKIE = "mf_token"  # noqa: S105 - the cookie name, not a secret


def token_ok(given: str | None, expected: str) -> bool:
    import hmac

    return bool(given) and hmac.compare_digest(str(given), expected)


def request_token(headers: Any, cookies: Any, query: Any) -> tuple[str | None, bool]:
    """``(token, from_query)``: the token a request carries — ``Authorization: Bearer``, ``X-MF-Token``, the cookie
    set by the first visit, or ``?token=`` on the link ``mf serve --remote`` prints."""
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    if headers.get("x-mf-token"):
        return str(headers.get("x-mf-token")), False
    if cookies.get(TOKEN_COOKIE):
        return str(cookies.get(TOKEN_COOKIE)), False
    if query.get("token"):
        return str(query.get("token")), True
    return None, False


LOCKED_PAGE = """<!doctype html><meta charset="utf-8"><title>Model Forensic — access token needed</title>
<body style="font:15px system-ui;background:#0b0d12;color:#e6e9f2;display:grid;place-items:center;height:100vh;margin:0">
<div style="max-width:520px;line-height:1.5"><h2>Access token needed</h2>
<p>This Model Forensic server is open beyond its own machine, so it asks for the access token it printed when it
started (<code>mf serve --remote</code>). Open the link it printed — it ends in <code>?token=…</code> — once; this
browser then stays signed in.</p><p style="color:#a3abc0">On the server: <code>mf status</code> prints the link again.</p>
</div></body>"""

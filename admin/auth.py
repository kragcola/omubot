"""Token-based auth for /admin routes. Uses HMAC-signed cookies + middleware."""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


def _derive_signing_key(admin_token: str) -> bytes:
    return hashlib.sha256(f"admin-signing:{admin_token}".encode()).digest()


def _sign_value(value: str, key: bytes) -> str:
    ts = str(int(time.time()))
    payload = f"{value}:{ts}"
    mac = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{mac}"


def _verify_signed(signed: str, key: bytes, max_age_s: int = 86400 * 7) -> str | None:
    try:
        parts = signed.rsplit(":", 1)
        if len(parts) != 2:
            return None
        payload, mac = parts
        if not hmac.compare_digest(
            hmac.new(key, payload.encode(), hashlib.sha256).hexdigest(), mac
        ):
            return None
        value, ts = payload.rsplit(":", 1)
        if int(time.time()) - int(ts) > max_age_s:
            return None
        return value
    except (ValueError, TypeError):
        return None


def _get_admin_token() -> str:
    """Read admin token from env var."""
    import os
    return os.environ.get("ADMIN_TOKEN", "admin")


# API paths that don't need auth
_API_SKIP_PATHS = {"/api/admin/login", "/api/admin/logout"}


class AdminAuthMiddleware(BaseHTTPMiddleware):
    """Middleware that protects /api/admin/* endpoints with HMAC-signed cookies.

    Page routes under /admin/* pass through to the SPA, which handles its own
    auth UI.  API routes return 401 JSON when unauthenticated.
    """

    def __init__(self, app, admin_token: str = "") -> None:
        super().__init__(app)
        token = admin_token or _get_admin_token()
        self._signing_key = _derive_signing_key(token)
        self._admin_token = token

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Only protect JSON API routes — let the SPA handle page auth
        if not path.startswith("/api/admin/"):
            return await call_next(request)

        if path in _API_SKIP_PATHS:
            return await call_next(request)

        session = request.cookies.get("admin_session", "")
        ok = False
        if session:
            value = _verify_signed(session, self._signing_key)
            ok = value == self._admin_token

        if not ok:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        request.state.admin_authenticated = True
        return await call_next(request)

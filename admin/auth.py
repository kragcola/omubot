"""Token-based auth for /admin routes. Uses HMAC-signed cookies + middleware."""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import Request
from fastapi.responses import RedirectResponse
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


# Paths that don't need auth
_SKIP_PATHS = {"/admin/login", "/admin/logout", "/admin/static"}


class AdminAuthMiddleware(BaseHTTPMiddleware):
    """Middleware that redirects unauthenticated /admin/* requests to /admin/login."""

    def __init__(self, app, admin_token: str = "") -> None:
        super().__init__(app)
        token = admin_token or _get_admin_token()
        self._signing_key = _derive_signing_key(token)
        self._admin_token = token

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if not path.startswith("/admin/") or any(
            path == p or path.startswith(p) for p in _SKIP_PATHS
        ):
            return await call_next(request)

        session = request.cookies.get("admin_session", "")
        ok = False
        if session:
            value = _verify_signed(session, self._signing_key)
            ok = value == self._admin_token

        if not ok:
            return RedirectResponse(url="/admin/login", status_code=303)

        request.state.admin_authenticated = True
        return await call_next(request)


def create_login_router():
    """Returns an APIRouter with login/logout routes."""
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse

    router = APIRouter()

    @router.get("/admin/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        from admin.templates import render
        return await render("login.html", {"request": request})

    @router.post("/admin/login")
    async def login(request: Request):
        admin_token = _get_admin_token()
        signing_key = _derive_signing_key(admin_token)

        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            import json as _json
            body = await request.body()
            try:
                data = _json.loads(body)
                token = data.get("token", "")
            except _json.JSONDecodeError:
                token = ""
        else:
            # form-encoded or query string
            form = await request.form()
            token: str = str(form.get("token", ""))

        if not token or token != admin_token:
            from admin.templates import render
            return await render("login.html", {
                "request": request,
                "messages": [{"type": "danger", "text": "Token 无效"}],
            })

        signed = _sign_value(token, signing_key)
        response = RedirectResponse(url="/admin/", status_code=303)
        response.set_cookie(
            "admin_session",
            signed,
            httponly=True,
            max_age=86400 * 7,
            samesite="lax",
        )
        return response

    @router.get("/admin/logout")
    async def logout():
        response = RedirectResponse(url="/admin/login", status_code=303)
        response.delete_cookie("admin_session")
        return response

    return router

"""JSON API: auth — login, logout, session check."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


def create_auth_router() -> APIRouter:
    router = APIRouter()

    _COOKIE_MAX_AGE = 86400 * 7  # 7 days

    @router.post("/login")
    async def login(request: Request):
        """Login endpoint. Returns JSON with success status."""
        from admin.auth import _derive_signing_key, _get_admin_token, _sign_value

        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
            token = body.get("token", "")
        else:
            form = await request.form()
            token = form.get("token", "")

        admin_token = _get_admin_token()
        if not token or token != admin_token:
            return JSONResponse({"ok": False, "error": "Invalid token"}, status_code=401)

        signing_key = _derive_signing_key(admin_token)
        signed = _sign_value(admin_token, signing_key)

        resp = JSONResponse({"ok": True, "message": "Logged in"})
        resp.set_cookie(
            key="admin_session",
            value=signed,
            max_age=_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return resp

    @router.post("/logout")
    async def logout():
        resp = JSONResponse({"ok": True, "message": "Logged out"})
        resp.delete_cookie("admin_session")
        return resp

    @router.get("/me")
    async def me(request: Request):
        """Check current authentication status."""
        from admin.auth import _derive_signing_key, _get_admin_token, _verify_signed

        session = request.cookies.get("admin_session", "")
        if not session:
            return JSONResponse({"authenticated": False}, status_code=401)

        admin_token = _get_admin_token()
        signing_key = _derive_signing_key(admin_token)
        value = _verify_signed(session, signing_key)

        if value == admin_token:
            return {"authenticated": True}
        return JSONResponse({"authenticated": False}, status_code=401)

    return router

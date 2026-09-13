from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import database as db, identity, security


router = APIRouter(tags=["profile"])


# Unknown fields (user_id, role, is_admin...) are ignored by Pydantic: the
# account being changed is always the one behind the session cookie.
class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    current_password: Optional[str] = Field(default=None, max_length=200)
    expected_version: int = Field(ge=1)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)
    expected_version: int = Field(ge=1)


def _public(user: dict) -> dict:
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "version": user["version"],
    }


def _raise(exc: identity.ProfileError):
    raise HTTPException(
        exc.status, {"code": exc.code, "message": str(exc), "fields": exc.fields}
    ) from exc


@router.get("/api/me")
def get_me(auth: security.AuthContext = Depends(security.authenticate)):
    with db.connection() as conn:
        try:
            user = identity._active_user(conn, auth.user_id)
        except identity.ProfileError as exc:
            _raise(exc)
    return _public(user)


@router.patch("/api/me")
def update_me(
    body: ProfileUpdate,
    request: Request,
    response: Response,
    auth: security.AuthContext = Depends(security.authenticate),
):
    if body.current_password:
        security.rate_limit(request, key="profile-credentials")
    raw = None
    try:
        with db.connection() as conn:
            user, credentials_changed = identity.update_own_profile(
                conn,
                auth.user_id,
                name=body.name,
                email=body.email,
                current_password=body.current_password,
                expected_version=body.expected_version,
            )
            if credentials_changed:
                raw = security.issue_session(conn, auth.user_id, revoke_others=True)
    except identity.ProfileError as exc:
        _raise(exc)
    result = _public(user)
    if raw:
        security.set_session_cookie(response, request, raw)
        result["csrf"] = security.csrf(raw)
    return result


@router.post("/api/me/password")
def change_password(
    body: PasswordChange,
    request: Request,
    response: Response,
    auth: security.AuthContext = Depends(security.authenticate),
):
    security.rate_limit(request, key="profile-credentials")
    try:
        with db.connection() as conn:
            user = identity.change_own_password(
                conn,
                auth.user_id,
                current_password=body.current_password,
                new_password=body.new_password,
                expected_version=body.expected_version,
            )
            raw = security.issue_session(conn, auth.user_id, revoke_others=True)
    except identity.ProfileError as exc:
        _raise(exc)
    security.set_session_cookie(response, request, raw)
    return {"ok": True, "csrf": security.csrf(raw), "version": user["version"]}

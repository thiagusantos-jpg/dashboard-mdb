from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import database as db, identity, permissions, security


router = APIRouter(tags=["users"])


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)
    role: str
    store: Optional[int] = Field(default=None, ge=1)


class UserPatch(BaseModel):
    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)


class RoleChange(BaseModel):
    role: str
    store: Optional[int] = Field(default=None, ge=1)


def _public_user(user: dict, role: str, store: Optional[int] = None) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "active": bool(user["active"]),
        "is_admin": bool(user["is_admin"]),
        "role": role,
        "store": store,
        "version": user["version"],
    }


def _require_company(company: int) -> None:
    with db.connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM companies WHERE id=?", (company,)
        ).fetchone()
    if not exists:
        raise HTTPException(404, "Empresa não encontrada.")


@router.get("/api/roles")
def roles(auth=Depends(security.authenticate)):
    return [
        {"id": role, "permissions": sorted(grants)}
        for role, grants in permissions.ROLE_PERMISSIONS.items()
    ]


@router.get("/api/companies/{company}/users")
def list_users(
    company: int,
    auth=Depends(permissions.require_permission("users.manage")),
):
    _require_company(company)
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT u.id,u.email,u.name,u.active,u.is_admin,u.version,s.role,s.store
            FROM users u
            JOIN user_scopes s ON s.user_id=u.id
            WHERE s.company=?
            ORDER BY u.name,u.email
            """,
            (company,),
        ).fetchall()
    return [
        _public_user(dict(row), row["role"], row["store"] or None) for row in rows
    ]


@router.post("/api/companies/{company}/users", status_code=201)
def create_scoped_user(
    company: int,
    body: UserCreate,
    auth=Depends(permissions.require_permission("users.manage")),
):
    _require_company(company)
    if body.role not in permissions.ROLE_PERMISSIONS:
        raise HTTPException(422, "Perfil inválido.")
    if identity.user_by_email(body.email):
        raise HTTPException(409, "Já existe um usuário com este e-mail.")
    try:
        user = identity.create_user(body.email, body.password, body.name)
        permissions.grant_role(user["id"], company, body.role, body.store)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _public_user(user, body.role, body.store)


@router.put("/api/companies/{company}/users/{user_id}/role")
def change_role(
    company: int,
    user_id: int,
    body: RoleChange,
    auth=Depends(permissions.require_permission("users.manage")),
):
    _require_company(company)
    if auth.user_id == user_id:
        raise HTTPException(409, "Você não pode alterar o próprio perfil.")
    try:
        permissions.ensure_administrator_remains(company, user_id, new_role=body.role)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        permissions.grant_role(user_id, company, body.role, body.store)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True}


@router.delete("/api/companies/{company}/users/{user_id}", status_code=204)
def disable_scoped_user(
    company: int,
    user_id: int,
    auth: security.AuthContext = Depends(
        permissions.require_permission("users.manage")
    ),
):
    _require_company(company)
    if auth.user_id == user_id:
        raise HTTPException(409, "Você não pode desativar seu próprio usuário.")
    try:
        permissions.ensure_administrator_remains(company, user_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        identity.disable_user(user_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/api/companies/{company}/users/{user_id}")
def update_scoped_user(
    company: int,
    user_id: int,
    body: UserPatch,
    auth: security.AuthContext = Depends(permissions.require_permission("users.manage")),
):
    _require_company(company)
    with db.connection() as conn:
        scope = conn.execute(
            "SELECT role,store FROM user_scopes WHERE user_id=? AND company=?", (user_id, company)
        ).fetchone()
    if not scope:
        raise HTTPException(404, "Usuário não encontrado nesta empresa.")
    try:
        user = identity.update_user_name(user_id, name=body.name, expected_version=body.expected_version)
    except identity.ProfileError as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": str(exc), "fields": exc.fields}) from exc
    return _public_user(user, scope["role"], scope["store"] or None)

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request

from . import database as db, security


ROLE_PERMISSIONS = {
    "administrator": {"*"},
    "partner": {
        "dashboard.read",
        "finance.read",
        "finance.write",
        "finance.sensitive.read",
        "integrations.manage",
        "inventory.read",
        "inventory.write",
        "settings.manage",
    },
    "manager": {
        "dashboard.read",
        "finance.read",
        "finance.write",
        "inventory.read",
        "inventory.write",
    },
    "viewer": {
        "dashboard.read",
        "finance.read",
        "inventory.read",
    },
}


def role_allows(role: str, permission: str) -> bool:
    granted = ROLE_PERMISSIONS.get(role, set())
    return "*" in granted or permission in granted


def _is_global_admin(user_id: int) -> bool:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT is_admin FROM users WHERE id=? AND active=1", (user_id,)
        ).fetchone()
    return bool(row and row["is_admin"])


def ensure_permission(
    auth: security.AuthContext,
    permission: str,
    company: Optional[int] = None,
    store: Optional[int] = None,
) -> security.AuthContext:
    if _is_global_admin(auth.user_id):
        return auth
    if company is None:
        raise HTTPException(403, "Você não tem permissão para esta ação.")
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT role,store FROM user_scopes
            WHERE user_id=? AND company=? AND store IN (0,?)
            """,
            (auth.user_id, company, store or 0),
        ).fetchall()
    if not any(role_allows(row["role"], permission) for row in rows):
        raise HTTPException(403, "Você não tem permissão para esta empresa ou loja.")
    return auth


def require_permission(permission: str, company_param: str = "company"):
    def dependency(
        request: Request,
        auth: security.AuthContext = Depends(security.authenticate),
    ) -> security.AuthContext:
        raw_company = request.path_params.get(company_param)
        company = int(raw_company) if raw_company is not None else None
        raw_store = request.path_params.get("store")
        store = int(raw_store) if raw_store is not None else None
        return ensure_permission(auth, permission, company, store)

    return dependency


def grant_role(user_id: int, company: int, role: str, store: Optional[int] = None) -> None:
    if role not in ROLE_PERMISSIONS:
        raise ValueError("Perfil inválido.")
    timestamp = db.now()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO user_scopes(user_id,company,store,role,created_at,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id,company,store)
            DO UPDATE SET role=excluded.role,updated_at=excluded.updated_at
            """,
            (user_id, company, store or 0, role, timestamp, timestamp),
        )


def companies_for(auth: security.AuthContext):
    if _is_global_admin(auth.user_id):
        return db.companies()
    with db.connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT DISTINCT c.id,c.name
                FROM companies c
                JOIN user_scopes s ON s.company=c.id
                WHERE s.user_id=?
                ORDER BY c.name
                """,
                (auth.user_id,),
            )
        ]

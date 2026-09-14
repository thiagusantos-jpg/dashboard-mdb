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


def _path_id(raw: Optional[str], label: str) -> Optional[int]:
    # int('null') used to escape as a 500 before FastAPI's own path validation ran.
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(422, f"{label} inválida.") from None


def require_permission(permission: str, company_param: str = "company"):
    def dependency(
        request: Request,
        auth: security.AuthContext = Depends(security.authenticate),
    ) -> security.AuthContext:
        company = _path_id(request.path_params.get(company_param), "Empresa")
        store = _path_id(request.path_params.get("store"), "Loja")
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


def usable_administrators(company: int, *, excluding_user_id: Optional[int] = None) -> int:
    """Active users who can still administer this company: global
    administrators plus holders of the administrator role in its scope."""
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT u.id FROM users u
            LEFT JOIN user_scopes s ON s.user_id=u.id AND s.company=? AND s.role='administrator'
            WHERE u.active=1 AND (u.is_admin=1 OR s.user_id IS NOT NULL)
            """,
            (company,),
        ).fetchall()
    return sum(1 for row in rows if excluding_user_id is None or int(row["id"]) != int(excluding_user_id))


def ensure_administrator_remains(company: int, user_id: int, *, new_role: Optional[str] = None) -> None:
    """Refuses demoting (new_role) or disabling (new_role=None) the last user
    able to administer the company, which would lock everyone out of Usuários."""
    if new_role == "administrator":
        return
    with db.connection() as conn:
        target = conn.execute(
            """
            SELECT u.is_admin, s.role FROM users u
            LEFT JOIN user_scopes s ON s.user_id=u.id AND s.company=?
            WHERE u.id=? AND u.active=1
            """,
            (company, user_id),
        ).fetchone()
    if not target or not (target["is_admin"] or target["role"] == "administrator"):
        return
    if new_role is not None and target["is_admin"]:
        return  # a global administrator keeps administering after a scoped role change
    if usable_administrators(company, excluding_user_id=user_id) == 0:
        raise ValueError("Não é possível remover o último administrador com acesso a esta empresa.")

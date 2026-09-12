from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import database as db, permissions, security
from ..finance import obligations


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])

_VALID_KINDS = ("entry", "loan_installment")


def _error_detail(code: str, message: str, fields=None) -> dict:
    return {"code": code, "message": message, "fields": fields}


def _has_permission(auth: security.AuthContext, permission: str, company: int) -> bool:
    try:
        permissions.ensure_permission(auth, permission, company)
        return True
    except HTTPException:
        return False


def _strip_write_actions(item: dict) -> dict:
    item["allowed_actions"] = [
        action for action in item["allowed_actions"] if action not in obligations.WRITE_ACTIONS
    ]
    return item


@router.get("/obligations")
def list_obligations(
    company: int,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    due_from: Optional[date] = None,
    due_to: Optional[date] = None,
    q: str = "",
    cursor: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    auth: security.AuthContext = Depends(permissions.require_permission("finance.read")),
):
    if kind is not None and kind not in _VALID_KINDS:
        raise HTTPException(
            422, _error_detail("invalid_fields", "Tipo de obrigação inválido.", ["kind"])
        )

    include_sensitive = _has_permission(auth, "finance.sensitive.read", company)
    can_write = _has_permission(auth, "finance.write", company)

    try:
        result = obligations.list_obligations(
            company,
            kind=kind,
            status=status,
            due_from=due_from,
            due_to=due_to,
            q=q,
            cursor=cursor,
            limit=limit,
            include_sensitive=include_sensitive,
        )
    except ValueError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", str(exc), ["cursor"])
        ) from exc

    if not can_write:
        result["items"] = [_strip_write_actions(item) for item in result["items"]]
    return result


@router.get("/obligations/{kind}/{obligation_id}")
def get_obligation(
    company: int,
    kind: str,
    obligation_id: str,
    auth: security.AuthContext = Depends(permissions.require_permission("finance.read")),
):
    if kind not in _VALID_KINDS:
        raise HTTPException(404, _error_detail("not_found", "Obrigação não encontrada."))
    try:
        numeric_id = int(obligation_id)
    except (TypeError, ValueError):
        raise HTTPException(
            422, _error_detail("invalid_fields", "Identificador inválido.", ["id"])
        )

    with db.connection() as conn:
        exists = conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone()
    if not exists:
        raise HTTPException(404, _error_detail("not_found", "Empresa não encontrada."))

    include_sensitive = _has_permission(auth, "finance.sensitive.read", company)
    can_write = _has_permission(auth, "finance.write", company)

    item = obligations.get_obligation(
        company, kind, numeric_id, include_sensitive=include_sensitive
    )
    if item is None:
        raise HTTPException(404, _error_detail("not_found", "Obrigação não encontrada."))

    if not can_write:
        _strip_write_actions(item)
    return item

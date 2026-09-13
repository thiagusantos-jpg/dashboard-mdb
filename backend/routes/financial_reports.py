from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import permissions, security
from ..finance.reporting import management_result


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


@router.get("/management-result")
def get_management_result(
    company: int,
    period: str,
    store: Optional[int] = None,
    auth: security.AuthContext = Depends(permissions.require_permission("finance.read")),
):
    # Sensitive accounts (owner withdrawals, taxes, loans...) only count for
    # profiles allowed to see them; others get totals marked restricted.
    try:
        permissions.ensure_permission(auth, "finance.sensitive.read", company)
        include_sensitive = True
    except HTTPException:
        include_sensitive = False
    return management_result(company, period, store, include_sensitive=include_sensitive)

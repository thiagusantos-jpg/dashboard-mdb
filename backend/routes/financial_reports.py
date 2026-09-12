from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from .. import permissions
from ..finance.reporting import management_result


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


@router.get(
    "/management-result",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def get_management_result(company: int, period: str, store: Optional[int] = None):
    return management_result(company, period, store)

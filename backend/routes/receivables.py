from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import database as db, permissions
from ..finance import accounts, receivables
from ..integrations.stone_receivables import ReceivablesFileError


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


class ContractedRate(BaseModel):
    rate_pct: float = Field(ge=0, le=20)
    effective_from: Optional[date] = None


def _require_company(company: int) -> None:
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone():
            raise HTTPException(404, "Empresa não encontrada.")


@router.post(
    "/cash-accounts/{cash_account_id}/receivables-import",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("integrations.manage"))],
)
async def import_receivables(company: int, cash_account_id: int, file: UploadFile = File(...)):
    _require_company(company)
    with db.connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM cash_accounts WHERE id=? AND company=?", (cash_account_id, company)
        ).fetchone():
            raise HTTPException(404, "Conta de caixa não encontrada.")
    content = await file.read()
    try:
        return receivables.sync_receivables(company, cash_account_id, content)
    except ReceivablesFileError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get(
    "/receivables/expected-settlements",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def get_expected_settlements(company: int, start: date, end: date):
    _require_company(company)
    return receivables.expected_settlements(company, start, end)


@router.get(
    "/receivables/effective-fee-report",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def get_effective_fee_report(company: int, start: date, end: date):
    _require_company(company)
    return receivables.effective_fee_report(company, start, end)


@router.put(
    "/receivables/contracted-rate",
    dependencies=[Depends(permissions.require_permission("settings.manage"))],
)
def put_contracted_rate(company: int, body: ContractedRate):
    """The MDR agreed with Stone, in percent — what the effective rate is compared against."""
    _require_company(company)
    effective_from = (body.effective_from or date.today()).isoformat()
    accounts.set_parameter(company, "contracted_mdr_rate", body.rate_pct, effective_from)
    return {"rate_pct": body.rate_pct, "effective_from": effective_from}

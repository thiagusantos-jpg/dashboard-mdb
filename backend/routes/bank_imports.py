from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import database as db, permissions
from ..integrations.bank_files import BankFileError, import_bank_file, parse_bank_file


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


def _require_company(company: int) -> None:
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone():
            raise HTTPException(404, "Empresa não encontrada.")


@router.post(
    "/bank-imports/preview",
    dependencies=[Depends(permissions.require_permission("integrations.manage"))],
)
async def preview_bank_import(company: int, file: UploadFile = File(...)):
    _require_company(company)
    content = await file.read()
    try:
        transactions = parse_bank_file(content, file.filename)
    except BankFileError as exc:
        raise HTTPException(422, str(exc)) from exc
    dates = [t.date for t in transactions]
    return {
        "count": len(transactions),
        "start": min(dates) if dates else None,
        "end": max(dates) if dates else None,
        "total_cents": sum(t.amount_cents for t in transactions),
    }


@router.post(
    "/cash-accounts/{cash_account_id}/bank-imports",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("integrations.manage"))],
)
async def commit_bank_import(company: int, cash_account_id: int, file: UploadFile = File(...)):
    _require_company(company)
    with db.connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM cash_accounts WHERE id=? AND company=?", (cash_account_id, company)
        ).fetchone():
            raise HTTPException(404, "Conta de caixa não encontrada.")
    content = await file.read()
    try:
        return import_bank_file(company, cash_account_id, file.filename, content)
    except BankFileError as exc:
        raise HTTPException(422, str(exc)) from exc

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .. import database as db, permissions
from ..integrations import bank_files
from ..integrations.bank_files import BankFileError, BankImportConflict


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


def _require_company(company: int) -> None:
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone():
            raise HTTPException(404, "Empresa não encontrada.")


def _require_cash_account(company: int, cash_account_id: int) -> None:
    with db.connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM cash_accounts WHERE id=? AND company=?", (cash_account_id, company)
        ).fetchone():
            raise HTTPException(404, "Conta de caixa não encontrada.")


@router.post(
    "/cash-accounts/{cash_account_id}/bank-imports/preview",
    dependencies=[Depends(permissions.require_permission("integrations.manage"))],
)
async def preview_bank_import(company: int, cash_account_id: int, file: UploadFile = File(...)):
    _require_company(company)
    _require_cash_account(company, cash_account_id)
    content = await file.read()
    try:
        return bank_files.preview_bank_import(company, cash_account_id, file.filename, content)
    except BankFileError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/cash-accounts/{cash_account_id}/bank-imports",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("integrations.manage"))],
)
async def commit_bank_import(
    company: int,
    cash_account_id: int,
    file: UploadFile = File(...),
    preview_hash: str = Form(...),
    decisions: str = Form("{}"),
):
    _require_company(company)
    _require_cash_account(company, cash_account_id)
    content = await file.read()
    try:
        decisions_map = json.loads(decisions) if decisions else {}
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Campo decisions inválido (é esperado um JSON).") from exc
    if not isinstance(decisions_map, dict):
        raise HTTPException(422, "Campo decisions inválido (é esperado um objeto).")
    try:
        return bank_files.commit_bank_import(
            company, cash_account_id, file.filename, content,
            decisions=decisions_map, preview_hash=preview_hash,
        )
    except BankImportConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except BankFileError as exc:
        raise HTTPException(422, str(exc)) from exc

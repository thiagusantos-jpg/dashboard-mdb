from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import database as db, permissions
from ..integrations.open_finance import StoneOpenFinanceProvider
from ..integrations.open_finance_service import OpenFinanceService


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


def _require_company(company: int) -> None:
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone():
            raise HTTPException(404, "Empresa não encontrada.")


def _service() -> OpenFinanceService:
    client_id = os.environ.get("STONE_CLIENT_ID")
    private_key = os.environ.get("STONE_PRIVATE_KEY")
    redirect_uri = os.environ.get("STONE_REDIRECT_URI")
    if not (client_id and private_key and redirect_uri):
        raise HTTPException(
            501,
            "Integração Stone não configurada nesta implantação "
            "(defina STONE_CLIENT_ID, STONE_PRIVATE_KEY e STONE_REDIRECT_URI).",
        )
    provider = StoneOpenFinanceProvider(
        client_id=client_id, private_key_pem=private_key, redirect_uri=redirect_uri,
        sandbox=os.environ.get("STONE_SANDBOX", "1") != "0",
    )
    return OpenFinanceService(provider)


class StartConsent(BaseModel):
    cash_account_id: int
    return_url: str = Field(min_length=1, max_length=500)


class CompleteConsent(BaseModel):
    jti: str = Field(min_length=1, max_length=200)
    external_account_id: str = Field(min_length=1, max_length=200)


def _require_connection(company: int, connection_id: int) -> dict:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM open_finance_connections WHERE id=? AND company=?", (connection_id, company)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Conexão não encontrada.")
    return dict(row)


@router.get(
    "/open-finance/connections",
    dependencies=[Depends(permissions.require_permission("integrations.manage"))],
)
def list_connections(company: int):
    _require_company(company)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM open_finance_connections WHERE company=? ORDER BY created_at DESC", (company,)
        ).fetchall()
    return [dict(row) for row in rows]


@router.post(
    "/open-finance/consent",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("integrations.manage"))],
)
def start_consent(company: int, body: StartConsent):
    _require_company(company)
    try:
        return _service().start_consent(company, body.cash_account_id, body.return_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/open-finance/consent/complete",
    dependencies=[Depends(permissions.require_permission("integrations.manage"))],
)
def complete_consent(company: int, body: CompleteConsent):
    _require_company(company)
    try:
        return _service().complete_consent(body.jti, body.external_account_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/open-finance/connections/{connection_id}/sync",
    dependencies=[Depends(permissions.require_permission("integrations.manage"))],
)
def sync_connection(company: int, connection_id: int):
    _require_connection(company, connection_id)
    return _service().sync(connection_id)


@router.post(
    "/open-finance/connections/{connection_id}/revoke",
    dependencies=[Depends(permissions.require_permission("integrations.manage"))],
)
def revoke_connection(company: int, connection_id: int):
    _require_connection(company, connection_id)
    return _service().revoke(connection_id)

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from .. import database as db, permissions
from ..finance import accounts


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    nature: accounts.AccountNature
    code: Optional[str] = Field(default=None, max_length=40)
    sensitive: bool = False


class CounterpartyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    kind: str = Field(pattern="^(supplier|beneficiary|owner|employee|lender|other)$")
    document: str = Field(default="", max_length=30)


@router.get(
    "/accounts",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def get_accounts(company: int, include_archived: bool = False):
    return accounts.list_accounts(company, include_archived)


@router.post(
    "/accounts",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def add_account(company: int, body: AccountCreate):
    try:
        return accounts.create_account(
            company,
            body.name,
            body.nature,
            code=body.code,
            sensitive=body.sensitive,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete(
    "/accounts/{account_id}",
    status_code=204,
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def remove_account(company: int, account_id: int, version: int):
    try:
        accounts.archive_account(company, account_id, expected_version=version)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(status_code=204)


@router.get(
    "/counterparties",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def get_counterparties(company: int):
    with db.connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM counterparties
                WHERE company=? AND archived=0 ORDER BY name
                """,
                (company,),
            )
        ]


@router.post(
    "/counterparties",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def add_counterparty(company: int, body: CounterpartyCreate):
    import secrets

    counterparty_id = secrets.randbits(63) or 1
    timestamp = db.now()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO counterparties(
                id,company,name,kind,document,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                counterparty_id,
                company,
                body.name.strip(),
                body.kind,
                body.document.strip(),
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM counterparties WHERE id=?", (counterparty_id,)
        ).fetchone()
    return dict(row)

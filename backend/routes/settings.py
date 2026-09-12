from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import organization, permissions


router = APIRouter(
    prefix="/api/companies/{company}/settings",
    tags=["settings"],
    dependencies=[Depends(permissions.require_permission("settings.manage"))],
)


class CompanySettings(BaseModel):
    expected_version: Optional[int] = Field(default=None, ge=1)
    legal_name: Optional[str] = Field(default=None, max_length=180)
    trade_name: Optional[str] = Field(default=None, max_length=180)
    cnpj: Optional[str] = Field(default=None, max_length=18)
    address: Optional[Dict[str, str]] = None
    contacts: Optional[Dict[str, str]] = None
    logo_url: Optional[str] = Field(default=None, max_length=500)


class StoreCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    timezone: str = Field(default="America/Sao_Paulo", max_length=80)


class StoreUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=180)
    timezone: Optional[str] = Field(default=None, max_length=80)
    active: Optional[bool] = None


class BusinessHours(BaseModel):
    expected_version: Optional[int] = Field(default=None, ge=1)
    opens_at: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    closes_at: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    closed: bool = False


class CalendarException(BaseModel):
    date: str
    status: str
    description: str = Field(default="", max_length=200)
    store: Optional[int] = Field(default=None, ge=1)
    expected_version: Optional[int] = Field(default=None, ge=1)


def _translate_error(exc: Exception):
    if isinstance(exc, organization.ConcurrentUpdateError):
        raise HTTPException(409, str(exc)) from exc
    raise HTTPException(422, str(exc)) from exc


@router.get("/company")
def get_company_settings(company: int):
    try:
        return organization.company_profile(company)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/company")
def update_company_settings(company: int, body: CompanySettings):
    changes = body.model_dump(exclude={"expected_version"}, exclude_none=True)
    try:
        return organization.save_company_profile(
            company, changes, expected_version=body.expected_version
        )
    except (ValueError, organization.ConcurrentUpdateError) as exc:
        _translate_error(exc)


@router.get("/stores")
def get_stores(company: int):
    return organization.list_stores(company)


@router.post("/stores", status_code=201)
def add_store(company: int, body: StoreCreate):
    try:
        return organization.create_store(
            company, body.name, timezone=body.timezone
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/stores/{store}")
def update_store(company: int, store: int, body: StoreUpdate):
    changes = body.model_dump(exclude={"expected_version"}, exclude_none=True)
    try:
        return organization.save_store(
            company, store, changes, expected_version=body.expected_version
        )
    except (ValueError, organization.ConcurrentUpdateError) as exc:
        _translate_error(exc)


@router.put("/stores/{store}/hours/{weekday}")
def update_hours(company: int, store: int, weekday: int, body: BusinessHours):
    try:
        return organization.save_business_hours(
            company,
            store,
            weekday,
            opens_at=body.opens_at,
            closes_at=body.closes_at,
            closed=body.closed,
            expected_version=body.expected_version,
        )
    except (ValueError, organization.ConcurrentUpdateError) as exc:
        _translate_error(exc)


@router.get("/calendar")
def get_calendar(company: int, store: Optional[int] = None):
    return organization.calendar_exceptions(company, store)


@router.put("/calendar")
def update_calendar(company: int, body: CalendarException):
    try:
        return organization.save_calendar_exception(
            company,
            body.date,
            body.status,
            description=body.description,
            store=body.store,
            expected_version=body.expected_version,
        )
    except (ValueError, organization.ConcurrentUpdateError) as exc:
        _translate_error(exc)

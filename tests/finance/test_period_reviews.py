"""Monthly review status with data revision tracking (task C6). A review is a
managerial sign-off tied to a hash of everything the month's result reads;
any later change to that data puts the month back "em apuração"."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, identity, permissions, security
from backend.finance import period_reviews
from backend.finance.accounts import list_accounts
from backend.finance.entries import EntryCommand, create_entry, settle_entry
from backend.finance.entry_management import update_entry
from backend.finance.reporting import management_result


COMPANY = 1
PERIOD = "2026-09"


def _sales(revenue=100_000):
    db.put_dataset(
        COMPANY, "sales", PERIOD,
        {
            "raw_count": 1,
            "receipts": [{
                "id": 1, "reference_id": 0, "aliases": [1], "date": "2026-09-02", "status": "V", "species": "CF",
                "revenue": revenue,
                "items": [{"id": 1, "product_id": 10, "quantity": "1", "revenue": revenue, "cost": 60_000,
                           "unit_cost": "600", "status": "V"}],
            }],
            "analysis": [],
        },
        documents=1,
    )


@pytest.fixture
def review_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "period-reviews.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    _sales()
    return {row["system_key"]: row for row in list_accounts(COMPANY)}


def add(account, amount, description="Energia", *, forecast=False):
    return create_entry(EntryCommand(
        company_id=COMPANY, account_id=account["id"], amount_cents=amount, competence=PERIOD,
        due_date=date(2026, 9, 20), source="manual", external_id=None, description=description, forecast=forecast,
    ))


def review(expected=None):
    revision = expected or period_reviews.get_review(COMPANY, PERIOD)["revision"]
    return period_reviews.record_review(
        COMPANY, PERIOD, action="review", expected_revision=revision, reason="", acknowledged=True, actor_id=None,
    )


def status():
    return period_reviews.get_review(COMPANY, PERIOD)["status"]


def test_a_month_is_never_reviewed_on_its_own(review_db):
    assert status() == "in_progress"


def test_review_then_expense_change_returns_to_in_progress(review_db):
    add(review_db["electricity"], 10_000)

    first = review()
    assert first["status"] == "reviewed"
    assert status() == "reviewed"

    add(review_db["electricity"], 5_000, "Energia extra")
    assert status() == "in_progress"


def test_a_new_sales_version_invalidates_the_review(review_db):
    add(review_db["electricity"], 10_000)
    review()

    _sales(revenue=120_000)  # a sync publishes a new version of the same month

    assert status() == "in_progress"


def test_review_with_a_stale_revision_conflicts(review_db):
    add(review_db["electricity"], 10_000)
    stale = period_reviews.get_review(COMPANY, PERIOD)["revision"]
    add(review_db["cleaning"], 2_000, "Limpeza")

    with pytest.raises(period_reviews.ReviewConflict):
        review(expected=stale)
    assert status() == "in_progress"


def test_reclassifying_an_expense_changes_the_revision_even_with_the_same_total(review_db):
    entry = add(review_db["electricity"], 10_000)
    before = period_reviews.get_review(COMPANY, PERIOD)["revision"]
    total_before = management_result(COMPANY, PERIOD)["operating_expenses_cents"]

    update_entry(COMPANY, entry["id"], {"account_id": review_db["cleaning"]["id"]},
                 expected_version=entry["version"], actor_id=None)

    assert management_result(COMPANY, PERIOD)["operating_expenses_cents"] == total_before
    assert period_reviews.get_review(COMPANY, PERIOD)["revision"] != before


def test_review_requires_acknowledgement_and_reopen_requires_a_reason(review_db):
    current = period_reviews.get_review(COMPANY, PERIOD)
    with pytest.raises(ValueError):
        period_reviews.record_review(COMPANY, PERIOD, action="review", expected_revision=current["revision"],
                                     reason="", acknowledged=False, actor_id=None)

    reviewed = review()
    with pytest.raises(ValueError):
        period_reviews.record_review(COMPANY, PERIOD, action="reopen", expected_revision=reviewed["revision"],
                                     reason="  ", acknowledged=False, actor_id=None)
    reopened = period_reviews.record_review(COMPANY, PERIOD, action="reopen", expected_revision=reviewed["revision"],
                                            reason="Faltou a conta de água", acknowledged=False, actor_id=None)

    assert reopened["status"] == "in_progress"
    assert reopened["reason"] == "Faltou a conta de água"


def test_checklist_names_pending_forecasts_and_payments_without_cash_link(review_db):
    add(review_db["cleaning"], 3_000, "Limpeza prevista", forecast=True)
    paid = add(review_db["electricity"], 10_000)
    settle_entry(paid["id"], 10_000, paid_at=date(2026, 9, 12))  # legacy settlement: no cash movement linked

    checks = {check["key"]: check for check in period_reviews.get_review(COMPANY, PERIOD)["checks"]}

    assert checks["sales_available"]["ok"] is True
    assert checks["forecasts_pending"]["count"] == 1
    assert checks["forecasts_pending"]["ok"] is False
    assert checks["payments_without_cash_link"]["count"] == 1
    assert "divergences" in checks


def test_management_result_reports_the_review(review_db):
    add(review_db["electricity"], 10_000)
    assert management_result(COMPANY, PERIOD)["data_status"]["expenses_reviewed"] is False

    review()

    assert management_result(COMPANY, PERIOD)["data_status"]["expenses_reviewed"] is True


# --- API: two sessions and permissions -----------------------------------------


@pytest.fixture
def api_client(review_db, monkeypatch):
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    with TestClient(api.app) as client:
        _login(client, "admin@loja.test", "bootstrap-password")
        yield client


def _login(client, email, password):
    response = client.post("/api/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf"]


URL = f"/api/companies/{COMPANY}/finance/period-reviews/{PERIOD}"


def test_two_sessions_reviewing_different_revisions(api_client, review_db):
    other = TestClient(api.app)
    _login(other, "admin@loja.test", "bootstrap-password")
    seen_by_other = other.get(URL).json()["revision"]
    seen_by_first = api_client.get(URL).json()["revision"]
    assert seen_by_first == seen_by_other

    created = api_client.post(f"/api/companies/{COMPANY}/finance/entries", json={
        "account_id": str(review_db["electricity"]["id"]), "amount_cents": 10_000, "competence": PERIOD,
        "due_date": "2026-09-20", "description": "Energia",
    })
    assert created.status_code == 201, created.text

    stale = other.post(URL, json={"action": "review", "expected_revision": seen_by_other, "reason": "", "acknowledged": True})
    fresh_revision = api_client.get(URL).json()["revision"]
    fresh = api_client.post(URL, json={"action": "review", "expected_revision": fresh_revision, "reason": "", "acknowledged": True})

    assert stale.status_code == 409
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["status"] == "reviewed"
    assert api_client.get(URL).json()["reviewed_by"] is not None


def test_reviewing_requires_settings_and_sensitive_permissions(api_client):
    user = identity.create_user("gerente@loja.test", "senha-segura-1", "Gerente")
    permissions.grant_role(user["id"], COMPANY, "manager")
    manager = TestClient(api.app)
    _login(manager, "gerente@loja.test", "senha-segura-1")

    revision = manager.get(URL)
    denied = manager.post(URL, json={"action": "review", "expected_revision": revision.json()["revision"], "reason": "", "acknowledged": True})

    assert revision.status_code == 200
    assert denied.status_code == 403

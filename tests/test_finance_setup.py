"""First finance package: zero-cost revenue share (item 11), guided fixed expense
setup (item 3), richer monthly review (item 16) and the closing reminder in the
Central de Ações (item 17)."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import actions, api, database as db, models, security
from backend.finance import accounts, fixed_expenses, period_reviews, reporting
from backend.finance.entries import EntryCommand, create_entry
from backend.finance.recurrence import generate_occurrences


COMPANY = 1


@pytest.fixture
def finance_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "finance-setup.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "finance-setup-api.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    with TestClient(api.app) as test_client:
        login = test_client.post("/api/login", json={"email": "admin@loja.test", "password": "bootstrap-password"})
        assert login.status_code == 200, login.text
        test_client.headers["x-csrf-token"] = login.json()["csrf"]
        yield test_client


def _receipt(doc, day, product, revenue, cost):
    return {"id": doc, "reference_id": 0, "aliases": [doc], "date": day, "status": "V", "species": "CF",
            "revenue": revenue,
            "items": [{"id": 1, "product_id": product, "quantity": "1", "revenue": revenue, "cost": cost,
                       "unit_cost": str(cost / 100), "status": "V"}]}


AUGUST = [_receipt(1, "2026-08-05", 10, 1000, 0), _receipt(2, "2026-08-06", 20, 3000, 1500)]


def _sales(period, receipts, *, cached=True):
    summary = {"end": f"{period}-28", "totals": models.summarize(receipts)["totals"]} if cached else None
    db.put_dataset(COMPANY, "sales", period, {"raw_count": len(receipts), "receipts": receipts, "analysis": [],
                                              "start": f"{period}-01", "end": f"{period}-28"},
                   documents=len(receipts), summary=summary)


def _checks(period):
    return {check["key"]: check for check in period_reviews.get_review(COMPANY, period)["checks"]}


# --- item 11: how much of the revenue was sold at a zero cost ------------------------------

def test_summarize_counts_the_revenue_sold_at_zero_cost():
    totals = models.summarize(AUGUST)["totals"]
    assert totals["zero_cost_items"] == 1
    assert totals["zero_cost_revenue"] == 1000


def test_management_result_reports_the_share_of_revenue_sold_at_zero_cost(finance_db):
    _sales("2026-08", AUGUST)
    result = reporting.management_result(COMPANY, "2026-08")
    assert result["cost_quality"] == {"zero_cost_items": 1, "zero_cost_revenue_cents": 1000, "zero_cost_share_pct": 25.0}
    assert reporting.management_result(COMPANY, "2026-07")["cost_quality"] is None


# --- item 16: the monthly review also checks setup and cost quality ------------------------

def test_review_flags_missing_fixed_expenses_missing_stone_file_and_zero_cost_sales(finance_db):
    _sales("2026-08", AUGUST)
    checks = _checks("2026-08")
    assert checks["fixed_expenses"]["ok"] is False
    assert "ponto de equilíbrio" in checks["fixed_expenses"]["detail"]
    assert checks["stone_file"]["ok"] is False
    assert "Recebíveis" in checks["stone_file"]["detail"]
    assert checks["zero_cost"]["ok"] is False
    assert checks["zero_cost"]["count"] == 1
    assert "50%" in checks["zero_cost"]["detail"]


def test_review_skips_the_zero_cost_check_without_a_cached_summary(finance_db):
    _sales("2026-08", AUGUST, cached=False)
    assert "zero_cost" not in _checks("2026-08")


def test_a_fixed_expense_and_the_stone_file_clear_their_checks(finance_db):
    rent = accounts.account_by_key(COMPANY, "rent")
    create_entry(EntryCommand(company_id=COMPANY, account_id=rent["id"], amount_cents=350000, competence="2026-08",
                              due_date=date(2026, 8, 10), source="manual", external_id=None, description="Aluguel"))
    ts = db.now()
    with db.connection() as conn:
        conn.execute("INSERT INTO cash_accounts(id,company,store,name,kind,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                     (77, COMPANY, None, "Stone", "acquirer", ts, ts))
        conn.execute("""INSERT INTO stone_receivables(id,company,cash_account_id,transaction_key,installment_number,
                        gross_cents,fee_cents,net_cents,settlement_date,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                     (1, COMPANY, 77, "tx-1", 1, 10000, 150, 9850, "2026-08-12", ts))
    checks = _checks("2026-08")
    assert checks["fixed_expenses"]["ok"] is True
    assert checks["stone_file"]["ok"] is True


def test_new_checks_do_not_reopen_a_month_already_reviewed(finance_db):
    review = period_reviews.get_review(COMPANY, "2026-08")
    period_reviews.record_review(COMPANY, "2026-08", action="review", expected_revision=review["revision"],
                                 reason="", acknowledged=True, actor_id=None)
    assert period_reviews.get_review(COMPANY, "2026-08")["status"] == "reviewed"


# --- item 17: closing reminder in the Central de Ações ------------------------------------

def test_closing_reminder_waits_until_day_five(finance_db):
    assert actions.ensure_closing_reminder(COMPANY, date(2026, 9, 4))["status"] == "too_early"
    assert actions.list_actions(COMPANY) == []


def test_closing_reminder_is_created_once_and_never_again_after_dismissed(finance_db):
    first = actions.ensure_closing_reminder(COMPANY, date(2026, 9, 5))
    assert first["status"] == "created"
    action = first["action"]
    assert action["alert_key"] == "fechamento:2026-08"
    assert action["title"].startswith("Fechar Ago/2026:")
    assert action["due_date"] == "2026-09-10"

    assert actions.ensure_closing_reminder(COMPANY, date(2026, 9, 6))["status"] == "exists"
    actions.transition_action(action["id"], "dismissed")
    assert actions.ensure_closing_reminder(COMPANY, date(2026, 9, 7))["status"] == "exists"
    assert len([a for a in actions.list_actions(COMPANY) if a["alert_key"] == "fechamento:2026-08"]) == 1


def test_closing_reminder_resolves_itself_once_the_month_is_reviewed(finance_db):
    actions.ensure_closing_reminder(COMPANY, date(2026, 9, 5))
    review = period_reviews.get_review(COMPANY, "2026-08")
    period_reviews.record_review(COMPANY, "2026-08", action="review", expected_revision=review["revision"],
                                 reason="", acknowledged=True, actor_id=None)
    out = actions.ensure_closing_reminder(COMPANY, date(2026, 9, 8))
    assert out["status"] == "resolved"
    assert out["action"]["status"] == "resolved"
    assert out["action"]["status_note"] == "Mês marcado como revisado."


def test_a_month_reviewed_before_the_reminder_never_gets_one(finance_db):
    review = period_reviews.get_review(COMPANY, "2025-12")
    period_reviews.record_review(COMPANY, "2025-12", action="review", expected_revision=review["revision"],
                                 reason="", acknowledged=True, actor_id=None)
    assert actions.ensure_closing_reminder(COMPANY, date(2026, 1, 5)) == {"period": "2025-12", "status": "reviewed", "action": None}


# --- item 3: guided fixed expense setup --------------------------------------------------

def test_fixed_expenses_become_recurrences_confirmed_for_the_month(finance_db):
    out = fixed_expenses.setup_fixed_expenses(COMPANY, "2026-08", [
        {"system_key": "rent", "amount_cents": 350000, "due_day": 10},
        {"system_key": "electricity", "amount_cents": 80000, "due_day": 15},
    ])
    assert sorted(item["system_key"] for item in out["created"]) == ["electricity", "rent"]
    assert out["skipped"] == []
    assert reporting.management_result(COMPANY, "2026-08")["operating_expenses_cents"] == 430000
    with db.connection() as conn:
        rows = conn.execute("SELECT status,due_date FROM financial_entries WHERE company=? ORDER BY amount_cents",
                            (COMPANY,)).fetchall()
    assert [(row["status"], str(row["due_date"])[:10]) for row in rows] == [("open", "2026-08-15"), ("open", "2026-08-10")]

    rent = next(item for item in out["created"] if item["system_key"] == "rent")
    september = generate_occurrences(int(rent["recurrence_id"]), through_competence="2026-09")
    assert [entry["status"] for entry in september] == ["forecast"], "later months arrive as forecasts to confirm"


def test_setup_skips_an_expense_that_already_repeats_and_rejects_bad_rows(finance_db):
    fixed_expenses.setup_fixed_expenses(COMPANY, "2026-08", [{"system_key": "rent", "amount_cents": 350000, "due_day": 10}])
    again = fixed_expenses.setup_fixed_expenses(COMPANY, "2026-09", [
        {"system_key": "rent", "amount_cents": 360000, "due_day": 10},
        {"system_key": "water", "amount_cents": 9000, "due_day": 20},
    ])
    assert again["skipped"] == ["rent"]
    assert [item["system_key"] for item in again["created"]] == ["water"]

    for bad in ([], [{"system_key": "cogs", "amount_cents": 100, "due_day": 1}],
                [{"system_key": "water", "amount_cents": 0, "due_day": 1}],
                [{"system_key": "water", "amount_cents": 100, "due_day": 32}]):
        with pytest.raises(ValueError):
            fixed_expenses.setup_fixed_expenses(COMPANY, "2026-10", bad)


def test_setup_template_lists_the_usual_store_expenses(finance_db):
    rows = fixed_expenses.setup_template(COMPANY)
    assert [row["system_key"] for row in rows][:3] == ["rent", "salaries", "payroll_taxes"]
    assert all(row["label"] and row["account_id"] for row in rows)
    fixed_expenses.setup_fixed_expenses(COMPANY, "2026-08", [{"system_key": "rent", "amount_cents": 350000, "due_day": 10}])
    assert next(row for row in fixed_expenses.setup_template(COMPANY) if row["system_key"] == "rent")["configured"] is True


# --- endpoints ---------------------------------------------------------------------------

def test_setup_and_reminder_endpoints(client):
    template = client.get("/api/companies/1/finance/fixed-expenses")
    assert template.status_code == 200, template.text
    assert template.json()[0]["system_key"] == "rent"

    created = client.post("/api/companies/1/finance/fixed-expenses",
                          json={"period": "2026-08", "items": [{"system_key": "rent", "amount_cents": 350000, "due_day": 10}]})
    assert created.status_code == 201, created.text
    assert len(created.json()["created"]) == 1

    bad = client.post("/api/companies/1/finance/fixed-expenses",
                      json={"period": "2026-08", "items": [{"system_key": "cogs", "amount_cents": 100, "due_day": 1}]})
    assert bad.status_code == 422

    reminder = client.post("/api/companies/1/actions/closing-reminder")
    assert reminder.status_code == 200, reminder.text
    assert reminder.json()["status"] in {"too_early", "created", "exists", "resolved", "reviewed"}

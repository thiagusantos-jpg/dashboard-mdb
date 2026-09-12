from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance.accounts import list_accounts
from backend.finance.entry_management import EntryConflictError, EntryValidationError
from backend.finance.expense_schedules import (
    confirm_entry,
    create_expense_schedule,
    preview_expense_schedule,
)
from backend.finance.recurrence import (
    RecurrenceConflictError,
    RecurrenceValidationError,
    create_recurrence,
    generate_occurrences,
    update_recurrence,
)


@pytest.fixture
def company(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "expense_schedules.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    return 1


@pytest.fixture
def account(company):
    return next(row for row in list_accounts(company) if row["system_key"] == "rent")


# --- preview_expense_schedule: exact sum, short month, single vs distributed -


def test_preview_distributes_remainder_on_first_installment_and_keeps_anchor_day():
    rows = preview_expense_schedule(10000, 3, date(2026, 1, 31), "single", "2026-01")

    assert [r["amount_cents"] for r in rows] == [3334, 3333, 3333]
    assert [r["due_date"] for r in rows] == ["2026-01-31", "2026-02-28", "2026-03-31"]
    assert {r["competence"] for r in rows} == {"2026-01"}
    assert sum(r["amount_cents"] for r in rows) == 10000


def test_preview_distributed_mode_uses_each_installment_due_month():
    rows = preview_expense_schedule(300, 3, date(2026, 1, 31), "distributed", None)

    assert [r["competence"] for r in rows] == ["2026-01", "2026-02", "2026-03"]
    assert [r["amount_cents"] for r in rows] == [100, 100, 100]


def test_preview_single_mode_requires_competence():
    with pytest.raises(ValueError):
        preview_expense_schedule(300, 3, date(2026, 1, 31), "single", None)


def test_preview_rejects_count_out_of_range():
    with pytest.raises(ValueError):
        preview_expense_schedule(1000, 0, date(2026, 1, 31), "single", "2026-01")
    with pytest.raises(ValueError):
        preview_expense_schedule(1000, 121, date(2026, 1, 31), "single", "2026-01")


def test_preview_rejects_non_positive_total():
    with pytest.raises(ValueError):
        preview_expense_schedule(0, 3, date(2026, 1, 31), "single", "2026-01")


def test_preview_accepts_boundary_counts_one_and_120():
    single = preview_expense_schedule(1000, 1, date(2026, 1, 31), "single", "2026-01")
    assert len(single) == 1
    assert single[0]["amount_cents"] == 1000

    many = preview_expense_schedule(12000, 120, date(2026, 1, 31), "single", "2026-01")
    assert len(many) == 120
    assert sum(r["amount_cents"] for r in many) == 12000


# --- create_expense_schedule: atomic generation, forecast status ----------


def test_create_expense_schedule_generates_forecast_entries_atomically(account, company):
    result = create_expense_schedule(
        company=company,
        account_id=account["id"],
        description="Compra parcelada",
        total_cents=10000,
        count=3,
        first_due=date(2026, 1, 31),
        competence_mode="single",
        competence="2026-01",
    )

    assert len(result["entries"]) == 3
    assert [e["status"] for e in result["entries"]] == ["forecast"] * 3
    assert [e["amount_cents"] for e in result["entries"]] == [3334, 3333, 3333]
    assert all(e["expense_schedule_id"] == result["schedule"]["id"] for e in result["entries"])
    assert result["schedule"]["installment_count"] == 3
    assert result["schedule"]["total_cents"] == 10000

    with db.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM financial_entries WHERE expense_schedule_id=?",
            (result["schedule"]["id"],),
        ).fetchone()["n"]
    assert count == 3


def test_create_expense_schedule_distributed_requires_confirmation(account, company):
    with pytest.raises(ValueError):
        create_expense_schedule(
            company=company,
            account_id=account["id"],
            description="Compra parcelada",
            total_cents=300,
            count=3,
            first_due=date(2026, 1, 31),
            competence_mode="distributed",
        )

    result = create_expense_schedule(
        company=company,
        account_id=account["id"],
        description="Compra parcelada",
        total_cents=300,
        count=3,
        first_due=date(2026, 1, 31),
        competence_mode="distributed",
        confirmed=True,
    )
    assert [e["competence"] for e in result["entries"]] == ["2026-01", "2026-02", "2026-03"]


def test_create_expense_schedule_rejects_count_outside_1_to_120(account, company):
    with pytest.raises(ValueError):
        create_expense_schedule(
            company=company,
            account_id=account["id"],
            description="Compra parcelada",
            total_cents=1000,
            count=0,
            first_due=date(2026, 1, 31),
            competence_mode="single",
            competence="2026-01",
        )


# --- confirm_entry: forecast -> open, only from forecast -------------------


def test_confirm_entry_transitions_forecast_to_open(account, company):
    result = create_expense_schedule(
        company=company,
        account_id=account["id"],
        description="Compra parcelada",
        total_cents=9000,
        count=3,
        first_due=date(2026, 1, 10),
        competence_mode="single",
        competence="2026-01",
    )
    entry = result["entries"][0]

    confirmed = confirm_entry(
        company, entry["id"], expected_version=entry["version"]
    )

    assert confirmed["status"] == "open"
    assert confirmed["version"] == entry["version"] + 1


def test_confirm_entry_can_adjust_amount_and_competence(account, company):
    result = create_expense_schedule(
        company=company,
        account_id=account["id"],
        description="Compra parcelada",
        total_cents=300,
        count=3,
        first_due=date(2026, 1, 31),
        competence_mode="distributed",
        confirmed=True,
    )
    entry = result["entries"][1]  # due 2026-02

    confirmed = confirm_entry(
        company,
        entry["id"],
        expected_version=entry["version"],
        amount_cents=150,
        competence="2026-03",
    )

    assert confirmed["status"] == "open"
    assert confirmed["amount_cents"] == 150
    assert confirmed["competence"] == "2026-03"


def test_confirm_entry_rejects_non_forecast_entry(account, company):
    result = create_expense_schedule(
        company=company,
        account_id=account["id"],
        description="Compra parcelada",
        total_cents=9000,
        count=3,
        first_due=date(2026, 1, 10),
        competence_mode="single",
        competence="2026-01",
    )
    entry = result["entries"][0]
    confirm_entry(company, entry["id"], expected_version=entry["version"])

    with pytest.raises(EntryConflictError):
        confirm_entry(company, entry["id"], expected_version=entry["version"] + 1)


def test_confirm_entry_rejects_stale_version(account, company):
    result = create_expense_schedule(
        company=company,
        account_id=account["id"],
        description="Compra parcelada",
        total_cents=9000,
        count=3,
        first_due=date(2026, 1, 10),
        competence_mode="single",
        competence="2026-01",
    )
    entry = result["entries"][0]

    with pytest.raises(EntryConflictError):
        confirm_entry(company, entry["id"], expected_version=entry["version"] + 1)


def test_confirm_entry_rejects_non_positive_amount(account, company):
    result = create_expense_schedule(
        company=company,
        account_id=account["id"],
        description="Compra parcelada",
        total_cents=9000,
        count=3,
        first_due=date(2026, 1, 10),
        competence_mode="single",
        competence="2026-01",
    )
    entry = result["entries"][0]

    with pytest.raises(EntryValidationError):
        confirm_entry(
            company, entry["id"], expected_version=entry["version"], amount_cents=0
        )


# --- update_recurrence: edit scopes, effective_competence, suspension ------


@pytest.fixture
def recurrence(account, company):
    return create_recurrence(
        company=company,
        account_id=account["id"],
        description="Aluguel",
        amount_cents=250_000,
        start_competence="2026-01",
        due_day=10,
    )


def test_scope_single_edits_one_occurrence_without_changing_the_series(recurrence, company):
    generate_occurrences(recurrence["id"], through_competence="2026-03")
    with db.connection() as conn:
        occurrence_version = conn.execute(
            "SELECT version FROM financial_entries WHERE recurrence_id=? AND competence=?",
            (recurrence["id"], "2026-02"),
        ).fetchone()["version"]

    result = update_recurrence(
        company,
        recurrence["id"],
        {"amount_cents": 300_000},
        expected_version=occurrence_version,
        scope="single",
        occurrence_competence="2026-02",
    )

    assert result["occurrence"]["amount_cents"] == 300_000
    assert result["occurrence"]["competence"] == "2026-02"
    # the series definition itself is untouched
    assert result["recurrence"]["amount_cents"] == 250_000
    assert result["recurrence"]["version"] == recurrence["version"]

    with db.connection() as conn:
        untouched = dict(
            conn.execute(
                "SELECT * FROM financial_entries WHERE recurrence_id=? AND competence=?",
                (recurrence["id"], "2026-01"),
            ).fetchone()
        )
    assert untouched["amount_cents"] == 250_000


def test_scope_future_updates_recurrence_and_cascades_to_forecast_occurrences_only(
    recurrence, company
):
    generate_occurrences(recurrence["id"], through_competence="2026-04")

    result = update_recurrence(
        company,
        recurrence["id"],
        {"amount_cents": 275_000},
        expected_version=recurrence["version"],
        scope="future",
        effective_competence="2026-03",
    )

    assert result["recurrence"]["amount_cents"] == 275_000
    assert result["recurrence"]["version"] == recurrence["version"] + 1

    with db.connection() as conn:
        rows = {
            row["competence"]: row["amount_cents"]
            for row in conn.execute(
                "SELECT competence,amount_cents FROM financial_entries WHERE recurrence_id=?",
                (recurrence["id"],),
            ).fetchall()
        }
    # before effective_competence: untouched
    assert rows["2026-01"] == 250_000
    assert rows["2026-02"] == 250_000
    # from effective_competence onward: cascaded
    assert rows["2026-03"] == 275_000
    assert rows["2026-04"] == 275_000


def test_scope_future_never_touches_confirmed_or_paid_occurrences(recurrence, company):
    generate_occurrences(recurrence["id"], through_competence="2026-02")
    with db.connection() as conn:
        jan_entry = dict(
            conn.execute(
                "SELECT * FROM financial_entries WHERE recurrence_id=? AND competence=?",
                (recurrence["id"], "2026-01"),
            ).fetchone()
        )
    confirm_entry(company, jan_entry["id"], expected_version=jan_entry["version"])

    update_recurrence(
        company,
        recurrence["id"],
        {"amount_cents": 999_000},
        expected_version=recurrence["version"],
        scope="future",
        effective_competence="2026-01",
    )

    with db.connection() as conn:
        jan_after = dict(
            conn.execute(
                "SELECT * FROM financial_entries WHERE id=?", (jan_entry["id"],)
            ).fetchone()
        )
    # confirmed (now 'open') occurrence must not be touched by the cascade,
    # even though its competence is >= effective_competence.
    assert jan_after["amount_cents"] == 250_000
    assert jan_after["status"] == "open"


def test_suspend_series_prevents_new_generation(recurrence, company):
    generate_occurrences(recurrence["id"], through_competence="2026-01")

    update_recurrence(
        company,
        recurrence["id"],
        {"active": False},
        expected_version=recurrence["version"],
        scope="future",
        effective_competence="2026-02",
    )

    with pytest.raises(ValueError):
        generate_occurrences(recurrence["id"], through_competence="2026-03")

    with db.connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM financial_entries WHERE recurrence_id=?",
            (recurrence["id"],),
        ).fetchone()["n"]
    assert total == 1


def test_scope_future_stale_version_is_conflict(recurrence, company):
    with pytest.raises(RecurrenceConflictError):
        update_recurrence(
            company,
            recurrence["id"],
            {"amount_cents": 300_000},
            expected_version=recurrence["version"] + 1,
            scope="future",
            effective_competence="2026-01",
        )


def test_scope_future_requires_effective_competence(recurrence, company):
    with pytest.raises(RecurrenceValidationError):
        update_recurrence(
            company,
            recurrence["id"],
            {"amount_cents": 300_000},
            expected_version=recurrence["version"],
            scope="future",
        )


def test_repeated_generation_still_preserves_unique_recurrence_competence(recurrence):
    first = generate_occurrences(recurrence["id"], through_competence="2026-02")
    second = generate_occurrences(recurrence["id"], through_competence="2026-02")

    assert len(first) == 2
    assert second == []
    with db.connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM financial_entries WHERE recurrence_id=?",
            (recurrence["id"],),
        ).fetchone()["n"]
    assert total == 2

from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance import ledger
from backend.integrations.open_finance import AccountBalance, ConsentLink, ExternalTransaction
from backend.integrations.open_finance_service import OpenFinanceService
from backend.integrations.records import external_record_count


COMPANY = 1


class FakeOpenFinanceProvider:
    """A test double satisfying the OpenFinanceProvider protocol — no live
    Stone credentials needed to validate the service's own lifecycle logic."""

    def __init__(self):
        self.revoked_ids = []
        self.fail_next_sync = False

    def create_consent(self, company_id: int, return_url: str) -> ConsentLink:
        return ConsentLink(url="https://sandbox.conta.stone.com.br/consentimento?client_id=x&jwt=y",
                            jti="jti-fake-1", expires_at="2026-09-12T02:00:00+00:00")

    def list_accounts(self) -> list:
        return [{"id": "acc-fake-1"}]

    def get_balance(self, account_id: str) -> AccountBalance:
        if self.fail_next_sync:
            raise RuntimeError("Stone indisponível")
        return AccountBalance(account_id=account_id, balance_cents=10_000_00, blocked_balance_cents=0, scheduled_balance_cents=0)

    def list_transactions(self, account_id: str, start: date, end: date) -> list:
        if self.fail_next_sync:
            raise RuntimeError("Stone indisponível")
        return [
            ExternalTransaction(external_id="stone-tx-1", date="2026-09-10", amount_cents=600_00, description="Venda"),
            ExternalTransaction(external_id="stone-tx-2", date="2026-09-11", amount_cents=-25_00, description="Taxa"),
        ]

    def revoke_consent(self, consent_id: str) -> None:
        self.revoked_ids.append(consent_id)


@pytest.fixture
def open_finance_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "open_finance.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


@pytest.fixture
def service(open_finance_db):
    return OpenFinanceService(FakeOpenFinanceProvider())


def connect_and_sync(service):
    cash_account = ledger.create_account(COMPANY, "Stone", "payment")
    started = service.start_consent(COMPANY, cash_account["id"], "https://app.local/callback")
    connection = service.complete_consent("jti-fake-1", "acc-fake-1")
    return service.sync(connection["id"])


def test_start_consent_persists_a_pending_connection(service):
    cash_account = ledger.create_account(COMPANY, "Stone", "payment")
    started = service.start_consent(COMPANY, cash_account["id"], "https://app.local/callback")

    connection = service.get_connection(started["connection_id"])
    assert connection["status"] == "pending"
    assert "consentimento" in started["consent_url"]


def test_sync_posts_transactions_idempotently(service):
    connection = connect_and_sync(service)

    assert connection["status"] == "active"
    assert connection["last_balance_cents"] == 10_000_00
    assert external_record_count(COMPANY) == 2
    assert ledger.consolidated_balance(COMPANY) == 600_00 - 25_00

    # Syncing again must not double-post the same transactions.
    service.sync(connection["id"])
    assert external_record_count(COMPANY) == 2
    assert ledger.consolidated_balance(COMPANY) == 600_00 - 25_00


def test_provider_failure_preserves_last_valid_balance(service):
    connection = connect_and_sync(service)
    service.provider.fail_next_sync = True

    result = service.sync(connection["id"])

    assert result["status"] == "error"
    assert result["last_balance_cents"] == 10_000_00  # unchanged, not wiped


def test_revocation_stops_sync_and_preserves_history(service):
    connection = connect_and_sync(service)
    count_before = external_record_count(COMPANY)

    revoked = service.revoke(connection["id"])
    assert revoked["status"] == "revoked"
    assert service.provider.revoked_ids == ["jti-fake-1"]

    result = service.sync(connection["id"])
    assert result["status"] == "revoked"
    assert external_record_count(COMPANY) == count_before

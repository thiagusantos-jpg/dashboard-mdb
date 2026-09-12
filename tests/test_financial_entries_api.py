from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance import accounts
from backend.finance.entries import EntryCommand, create_entry
from backend.routes.financial_entries import _stringify_history_item


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "financial-entries-api.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    with TestClient(api.app) as test_client:
        login = test_client.post(
            "/api/login",
            json={"email": "admin@loja.test", "password": "bootstrap-password"},
        )
        assert login.status_code == 200, login.text
        test_client.headers["x-csrf-token"] = login.json()["csrf"]
        yield test_client


def _open_entry(client):
    account = accounts.account_by_key(1, "sales")
    return create_entry(
        EntryCommand(
            company_id=1,
            account_id=account["id"],
            amount_cents=10_000,
            competence="2026-09",
            due_date=date(2026, 9, 10),
            source="manual",
            external_id=None,
            description="Venda",
        )
    )


# --- Finding 1: BIGINT ids inside history before/after snapshots ----------


def test_stringify_history_item_recurses_into_before_and_after_snapshots():
    # Entry/account/counterparty ids are secrets.randbits(63): routinely
    # bigger than 2**53 (JS's safe-integer ceiling). A raw JSON *number*
    # that big silently loses precision in JS JSON.parse, so every
    # BIGINT-ish key inside `before`/`after` must come back as a string,
    # not just the history item's own top-level id/actor_id.
    big_id = 9_999_999_999_999_999  # > 2**53 == 9_007_199_254_740_992
    item = {
        "id": 1,
        "kind": "audit",
        "action": "update",
        "before": {
            "id": big_id,
            "account_id": big_id + 1,
            "counterparty_id": big_id + 2,
            "recurrence_id": None,
            "created_by": big_id + 3,
            "store": big_id + 4,
            "amount_cents": 1000,
        },
        "after": {
            "id": big_id,
            "account_id": big_id + 5,
            "counterparty_id": None,
            "recurrence_id": big_id + 6,
            "created_by": None,
            "store": None,
            "amount_cents": 2000,
        },
        "amount_cents": None,
        "reason": "",
        "actor_id": None,
        "created_at": "2026-09-12T00:00:00",
    }

    shaped = _stringify_history_item(item)

    # Round-trip through json.dumps/json.loads exactly like the real HTTP
    # response body would, to prove no raw big int survives to JSON output.
    round_tripped = json.loads(json.dumps(shaped, ensure_ascii=False, default=str))

    assert round_tripped["before"]["id"] == str(big_id)
    assert isinstance(round_tripped["before"]["id"], str)
    assert round_tripped["before"]["account_id"] == str(big_id + 1)
    assert round_tripped["before"]["counterparty_id"] == str(big_id + 2)
    assert round_tripped["before"]["recurrence_id"] is None
    assert round_tripped["before"]["created_by"] == str(big_id + 3)
    assert round_tripped["before"]["store"] == str(big_id + 4)
    # non-id field is left untouched (still a JSON number)
    assert round_tripped["before"]["amount_cents"] == 1000

    assert round_tripped["after"]["account_id"] == str(big_id + 5)
    assert round_tripped["after"]["counterparty_id"] is None
    assert round_tripped["after"]["recurrence_id"] == str(big_id + 6)
    assert round_tripped["after"]["created_by"] is None
    assert round_tripped["after"]["store"] is None


def test_history_endpoint_stringifies_bigint_ids_inside_before_after(client, monkeypatch):
    # Force the entry's own id past 2**53 deterministically instead of
    # relying on secrets.randbits(63) landing there by chance.
    from backend.finance import entries as entries_module

    big_id = 9_007_199_254_740_993  # 2**53 + 1
    monkeypatch.setattr(entries_module, "_new_id", lambda: big_id)

    entry = _open_entry(client)
    assert entry["id"] == big_id

    update = client.patch(
        f"/api/companies/1/finance/entries/{entry['id']}",
        json={"expected_version": int(entry["version"]), "amount_cents": 20_000},
    )
    assert update.status_code == 200, update.text

    history = client.get(f"/api/companies/1/finance/entries/{entry['id']}/history")
    assert history.status_code == 200, history.text
    items = history.json()

    audit_items = [item for item in items if item["kind"] == "audit"]
    assert audit_items, "expected at least one audit history item"
    for item in audit_items:
        assert isinstance(item["before"]["id"], str)
        assert item["before"]["id"] == str(big_id)
        assert isinstance(item["after"]["id"], str)
        assert item["after"]["id"] == str(big_id)
        assert isinstance(item["before"]["account_id"], str)
        assert isinstance(item["after"]["account_id"], str)


# --- Finding 2: Pydantic body-validation 422s use {code,message,fields} ---


def test_patch_entry_missing_expected_version_uses_shared_error_shape(client):
    entry = _open_entry(client)

    response = client.patch(
        f"/api/companies/1/finance/entries/{entry['id']}",
        json={"amount_cents": 5_000},  # expected_version is required
    )

    assert response.status_code == 422
    body = response.json()
    detail = body["detail"]
    # Not FastAPI's raw default shape: a bare list of {loc,msg,type} dicts.
    assert isinstance(detail, dict)
    assert detail["code"] == "invalid_fields"
    assert isinstance(detail["message"], str) and detail["message"]
    assert "expected_version" in detail["fields"]


def test_patch_entry_non_numeric_amount_uses_shared_error_shape(client):
    entry = _open_entry(client)

    response = client.patch(
        f"/api/companies/1/finance/entries/{entry['id']}",
        json={"expected_version": int(entry["version"]), "amount_cents": "not-a-number"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "invalid_fields"
    assert "amount_cents" in detail["fields"]


def test_cancel_entry_missing_expected_version_uses_shared_error_shape(client):
    entry = _open_entry(client)

    response = client.post(
        f"/api/companies/1/finance/entries/{entry['id']}/cancel",
        json={"reason": "Duplicado"},  # expected_version is required
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "invalid_fields"
    assert "expected_version" in detail["fields"]

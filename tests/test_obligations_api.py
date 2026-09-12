from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, identity, permissions, security
from backend.finance import accounts, loans
from backend.finance.entries import EntryCommand, create_entry, settle_entry


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "obligations-api.sqlite3")
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


def _partial_entry():
    account = accounts.account_by_key(1, "rent")
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=100_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))
    settle_entry(entry["id"], 40_000, paid_at=date(2026, 9, 12))
    return entry


def _two_installment_loan():
    return loans.create_loan(
        1, lender="Banco Local", purpose="Capital de giro",
        principal_cents=200_000, net_disbursement_cents=195_000,
        installments=[
            {"number": 1, "due_date": "2026-09-10", "principal_cents": 100_000, "interest_cents": 5_000},
            {"number": 2, "due_date": "2026-10-10", "principal_cents": 100_000, "interest_cents": 5_000},
        ],
        start_date="2026-09-01",
    )


def _login_as(test_client, email, password):
    other = TestClient(api.app)
    login = other.post("/api/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    other.headers["x-csrf-token"] = login.json()["csrf"]
    return other


def _create_viewer(email="visualizador@loja.test", password="senha-segura-1"):
    user = identity.create_user(email, password, "Visualizador", is_admin=False)
    permissions.grant_role(user["id"], 1, "viewer")
    return user


# --- Basic listing / pagination --------------------------------------------


def test_list_obligations_returns_shared_contract_shape(client):
    _partial_entry()
    _two_installment_loan()

    response = client.get("/api/companies/1/finance/obligations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert set(body.keys()) == {"items", "total", "open_cents", "next_cursor"}
    item = body["items"][0]
    assert set(item.keys()) == {
        "key", "kind", "id", "version", "description", "due_date", "competence",
        "total_cents", "paid_cents", "open_cents", "status", "source", "loan_id",
        "number", "count", "allowed_actions",
    }
    assert item["key"] in (item["kind"] + ":" + item["id"],)
    assert isinstance(item["id"], str)


def test_list_obligations_paginates_with_limit(client):
    _partial_entry()
    _two_installment_loan()

    first = client.get("/api/companies/1/finance/obligations", params={"limit": 1})
    assert first.status_code == 200
    body = first.json()
    assert len(body["items"]) == 1
    assert body["total"] == 3
    assert body["next_cursor"] is not None

    second = client.get(
        "/api/companies/1/finance/obligations",
        params={"limit": 1, "cursor": body["next_cursor"]},
    )
    assert second.status_code == 200
    assert second.json()["total"] == 3
    assert second.json()["items"][0]["key"] != body["items"][0]["key"]


# --- Filters -----------------------------------------------------------------


def test_search_and_combinable_filters_via_query_params(client):
    _partial_entry()
    _two_installment_loan()

    response = client.get("/api/companies/1/finance/obligations", params={"q": "aluguel"})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1

    response = client.get(
        "/api/companies/1/finance/obligations",
        params={
            "kind": "loan_installment",
            "status": "open",
            "due_from": "2026-10-01",
            "due_to": "2026-10-31",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["due_date"] == "2026-10-10"


def test_invalid_kind_filter_is_rejected(client):
    response = client.get("/api/companies/1/finance/obligations", params={"kind": "bogus"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "invalid_fields"


# --- Invalid cursor ----------------------------------------------------------


def test_invalid_cursor_returns_422_with_shared_error_shape(client):
    _partial_entry()
    response = client.get(
        "/api/companies/1/finance/obligations", params={"cursor": "not-a-real-cursor!!"}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "invalid_fields"
    assert "cursor" in detail["fields"]


# --- Read-only viewer: no write actions offered -----------------------------


def test_read_only_viewer_has_no_write_allowed_actions(client):
    _partial_entry()
    _create_viewer()
    viewer_client = _login_as(client, "visualizador@loja.test", "senha-segura-1")

    response = viewer_client.get("/api/companies/1/finance/obligations")
    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        assert "pay" not in item["allowed_actions"]
        assert "edit_description" not in item["allowed_actions"]
        assert "details" in item["allowed_actions"]

    # Compare with the admin's view of the exact same data: it does offer them.
    admin_view = client.get("/api/companies/1/finance/obligations").json()
    assert any("pay" in item["allowed_actions"] for item in admin_view["items"])


def test_viewer_without_finance_read_gets_403(client):
    user = identity.create_user("estranho@loja.test", "senha-segura-2", "Estranho", is_admin=False)
    # No role granted at all for company 1.
    outsider = _login_as(client, "estranho@loja.test", "senha-segura-2")
    response = outsider.get("/api/companies/1/finance/obligations")
    assert response.status_code == 403


# --- Sensitive gating applies to rows AND totals ----------------------------


def test_sensitive_rows_and_totals_hidden_from_manager_without_sensitive_read(client):
    sensitive_account = accounts.account_by_key(1, "salaries")
    create_entry(EntryCommand(
        company_id=1, account_id=sensitive_account["id"], amount_cents=50_000,
        competence="2026-09", due_date=date(2026, 9, 15), source="manual",
        external_id=None, description="Folha de pagamento",
    ))
    _partial_entry()

    manager = identity.create_user("gerente@loja.test", "senha-segura-3", "Gerente", is_admin=False)
    permissions.grant_role(manager["id"], 1, "manager")  # finance.write but not finance.sensitive.read
    manager_client = _login_as(client, "gerente@loja.test", "senha-segura-3")

    response = manager_client.get("/api/companies/1/finance/obligations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["open_cents"] == 60_000
    assert all("Folha" not in item["description"] for item in body["items"])

    # An administrator (global admin, sees everything) still sees both.
    admin_body = client.get("/api/companies/1/finance/obligations").json()
    assert admin_body["total"] == 2


# --- IDs stay strings, even past JS's 2**53 safe-integer limit -------------


def test_ids_are_stringified_in_json_response(client, monkeypatch):
    from backend.finance import entries as entries_module

    big_id = 9_007_199_254_740_993  # 2**53 + 1
    monkeypatch.setattr(entries_module, "_new_id", lambda: big_id)
    account = accounts.account_by_key(1, "rent")
    create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=10_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))

    response = client.get("/api/companies/1/finance/obligations")
    assert response.status_code == 200, response.text
    item = next(i for i in response.json()["items"] if i["key"] == f"entry:{big_id}")
    assert item["id"] == str(big_id)
    # Raw response text must never carry the bare numeric id (would prove a
    # raw JSON number slipped through and lost precision in JS JSON.parse).
    assert f'"id":{big_id}' not in response.text


# --- Detail endpoint ---------------------------------------------------------


def test_get_obligation_detail_entry(client):
    entry = _partial_entry()
    response = client.get(f"/api/companies/1/finance/obligations/entry/{entry['id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["key"] == f"entry:{entry['id']}"
    assert body["open_cents"] == 60_000


def test_get_obligation_detail_preserves_paid_installment_history(client):
    loan = _two_installment_loan()
    installment_id = loans.loan_position(loan["id"])["installments"][0]["id"]
    client.post(
        f"/api/companies/1/finance/loans/installments/{installment_id}/payments",
        json={"principal_cents": 100_000, "interest_cents": 5_000, "paid_at": "2026-09-10"},
    )
    response = client.get(f"/api/companies/1/finance/obligations/loan_installment/{installment_id}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "paid"


def test_get_obligation_detail_404_for_unknown_kind(client):
    response = client.get("/api/companies/1/finance/obligations/bogus/1")
    assert response.status_code == 404


def test_get_obligation_detail_404_for_missing_id(client):
    response = client.get("/api/companies/1/finance/obligations/entry/999999999999")
    assert response.status_code == 404


def test_get_obligation_detail_404_for_sensitive_item_without_permission(client):
    sensitive_account = accounts.account_by_key(1, "salaries")
    entry = create_entry(EntryCommand(
        company_id=1, account_id=sensitive_account["id"], amount_cents=50_000,
        competence="2026-09", due_date=date(2026, 9, 15), source="manual",
        external_id=None, description="Folha de pagamento",
    ))
    manager = identity.create_user("gerente2@loja.test", "senha-segura-4", "Gerente", is_admin=False)
    permissions.grant_role(manager["id"], 1, "manager")
    manager_client = _login_as(client, "gerente2@loja.test", "senha-segura-4")

    response = manager_client.get(f"/api/companies/1/finance/obligations/entry/{entry['id']}")
    assert response.status_code == 404

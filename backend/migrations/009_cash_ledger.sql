CREATE TABLE cash_accounts(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    store BIGINT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX cash_accounts_company_idx ON cash_accounts(company);

CREATE TABLE cash_events(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    cash_account_id BIGINT NOT NULL REFERENCES cash_accounts(id),
    amount_cents BIGINT NOT NULL,
    occurred_at TEXT NOT NULL,
    description TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'entry',
    transfer_group BIGINT,
    reversed_event_id BIGINT REFERENCES cash_events(id),
    entry_id BIGINT,
    created_by BIGINT,
    created_at TEXT NOT NULL
);

CREATE INDEX cash_events_account_idx ON cash_events(cash_account_id,occurred_at);
CREATE INDEX cash_events_company_idx ON cash_events(company,occurred_at);
CREATE INDEX cash_events_transfer_idx ON cash_events(transfer_group);

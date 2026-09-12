CREATE TABLE open_finance_connections(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    cash_account_id BIGINT NOT NULL REFERENCES cash_accounts(id),
    provider TEXT NOT NULL DEFAULT 'stone',
    external_account_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    consent_jti TEXT,
    last_synced_at TEXT,
    last_balance_cents BIGINT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX open_finance_connections_company_idx ON open_finance_connections(company);

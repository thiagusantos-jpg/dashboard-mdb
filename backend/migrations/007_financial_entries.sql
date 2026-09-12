CREATE TABLE financial_entries(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    store BIGINT NOT NULL DEFAULT 0,
    account_id BIGINT NOT NULL REFERENCES finance_accounts(id),
    counterparty_id BIGINT,
    description TEXT NOT NULL,
    amount_cents BIGINT NOT NULL,
    competence TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT,
    recurrence_id BIGINT,
    installment_number INTEGER,
    installment_count INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_by BIGINT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(company,source,external_id),
    UNIQUE(recurrence_id,competence)
);

CREATE INDEX financial_entries_company_idx ON financial_entries(
    company,competence,due_date,status
);

CREATE TABLE financial_events(
    id BIGINT PRIMARY KEY,
    entry_id BIGINT NOT NULL REFERENCES financial_entries(id),
    event_type TEXT NOT NULL,
    amount_cents BIGINT NOT NULL DEFAULT 0,
    occurred_at TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_by BIGINT,
    created_at TEXT NOT NULL
);

CREATE INDEX financial_events_entry_idx ON financial_events(entry_id,occurred_at);

CREATE TABLE financial_recurrences(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    store BIGINT NOT NULL DEFAULT 0,
    account_id BIGINT NOT NULL REFERENCES finance_accounts(id),
    counterparty_id BIGINT,
    description TEXT NOT NULL,
    amount_cents BIGINT NOT NULL,
    start_competence TEXT NOT NULL,
    end_competence TEXT,
    due_day INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_by BIGINT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE financial_attachments(
    id BIGINT PRIMARY KEY,
    entry_id BIGINT NOT NULL REFERENCES financial_entries(id),
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    created_at TEXT NOT NULL
);

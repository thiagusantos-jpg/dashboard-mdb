ALTER TABLE loan_installments ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE obligation_payments(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    obligation_kind TEXT NOT NULL,
    obligation_id BIGINT NOT NULL,
    amount_cents BIGINT NOT NULL,
    principal_cents BIGINT,
    interest_cents BIGINT,
    paid_at TEXT NOT NULL,
    cash_event_id BIGINT REFERENCES cash_events(id),
    owns_cash_event INTEGER NOT NULL DEFAULT 0,
    financial_event_id BIGINT REFERENCES financial_events(id),
    interest_entry_id BIGINT REFERENCES financial_entries(id),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    reversed_at TEXT,
    created_by BIGINT,
    created_at TEXT NOT NULL,
    UNIQUE(company,idempotency_key)
);

CREATE INDEX obligation_payments_obligation_idx ON obligation_payments(
    company,obligation_kind,obligation_id
);

CREATE TABLE bank_cash_links(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    cash_account_id BIGINT NOT NULL,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    cash_event_id BIGINT NOT NULL REFERENCES cash_events(id),
    created_at TEXT NOT NULL,
    UNIQUE(company,cash_account_id,provider,external_id)
);

CREATE INDEX bank_cash_links_cash_event_idx ON bank_cash_links(cash_event_id);

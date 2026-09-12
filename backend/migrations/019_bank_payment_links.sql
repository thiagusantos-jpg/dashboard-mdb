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

-- UNIQUE, not a plain index: "a real cash movement is linked at most once"
-- must hold even under concurrent commits at Postgres READ COMMITTED, where
-- two transactions can both pass an application-level
-- "already claimed?" SELECT before either INSERTs. See
-- backend/integrations/bank_files.py::commit_bank_import, which still does
-- that SELECT as a fast pre-check but now also relies on THIS constraint
-- (via a caught IntegrityError) as the actual race-proof guarantee.
CREATE UNIQUE INDEX bank_cash_links_cash_event_idx ON bank_cash_links(cash_event_id);

-- Task B7: loan disbursements gain the same "generate cash or link existing
-- movement" + idempotency shape as backend/finance/payments.py::record_payment
-- (see backend/migrations/017_obligation_payments.sql for the template this
-- mirrors). `loan_disbursements` (migration 008) previously had no `company`
-- column at all (company was only reachable via a join through `loans`) and
-- no idempotency/cash-link columns whatsoever.
ALTER TABLE loan_disbursements ADD COLUMN company INTEGER;
ALTER TABLE loan_disbursements ADD COLUMN cash_event_id BIGINT REFERENCES cash_events(id);
ALTER TABLE loan_disbursements ADD COLUMN owns_cash_event INTEGER NOT NULL DEFAULT 0;
ALTER TABLE loan_disbursements ADD COLUMN idempotency_key TEXT;
ALTER TABLE loan_disbursements ADD COLUMN request_hash TEXT;
ALTER TABLE loan_disbursements ADD COLUMN response_json TEXT;

-- Mirrors obligation_payments' UNIQUE(company,idempotency_key) constraint
-- (017_obligation_payments.sql) — the actual race-proof idempotency
-- guarantee, not just the application-level pre-check in record_disbursement.
CREATE UNIQUE INDEX loan_disbursements_idempotency_idx ON loan_disbursements(
    company,idempotency_key
);

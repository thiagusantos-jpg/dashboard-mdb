ALTER TABLE obligation_payments ADD COLUMN reversal_reason TEXT;
ALTER TABLE obligation_payments ADD COLUMN reversed_by BIGINT;
ALTER TABLE obligation_payments ADD COLUMN reversal_idempotency_key TEXT;
ALTER TABLE obligation_payments ADD COLUMN reversal_request_hash TEXT;
ALTER TABLE obligation_payments ADD COLUMN reversal_response_json TEXT;

CREATE UNIQUE INDEX obligation_payments_reversal_idempotency_idx ON obligation_payments(
    company,reversal_idempotency_key
);

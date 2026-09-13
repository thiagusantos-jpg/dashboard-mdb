-- Final review C4: allocating one real bank movement (cash_event) to an
-- obligation payment or to a loan disbursement was guarded only by a plain
-- "SELECT 1 ... WHERE cash_event_id=?" pre-check followed by an INSERT, with
-- no constraint behind it. Two concurrent requests carrying DIFFERENT
-- idempotency keys but naming the SAME existing_cash_event_id can both pass
-- that SELECT and both commit, allocating a single bank movement to two
-- different obligations. Neither table had any uniqueness on cash_event_id:
-- obligation_payments (017) is UNIQUE only on (company,idempotency_key) and
-- (company,reversal_idempotency_key), loan_disbursements (021) only on
-- (company,idempotency_key).
--
-- Same shape and rationale as bank_cash_links_cash_event_idx (migration 019):
-- the application-level SELECT stays as a fast, friendly pre-check, and THIS
-- index is the actual race-proof guarantee, surfaced by the callers as the
-- very same validation error they already raise for the non-racy case.
--
-- Partial (WHERE ...) unique indexes are supported by SQLite 3.8.0+ and by
-- PostgreSQL, and this codebase already relies on one on both backends
-- (`one_active_job`, created in backend/database.py's SQLite AND PostgreSQL
-- initializers alike), so the syntax below is portable.
--
-- The "reversed_at IS NULL" predicate encodes the semantics the rest of the
-- codebase already documents and enforces: reversing a payment frees its
-- linked movement for re-allocation without deleting the bank transaction
-- (backend/finance/payments.py::reverse_payment, and record_payment's own
-- "already allocated" pre-check, which filters on reversed_at IS NULL).
CREATE UNIQUE INDEX obligation_payments_active_cash_event_idx
  ON obligation_payments(cash_event_id)
  WHERE cash_event_id IS NOT NULL AND reversed_at IS NULL;

-- loan_disbursements has no reversal concept at all (there is no
-- "undo a disbursement" operation in backend/finance/loans.py), so the
-- allocation is unconditional for every non-NULL cash_event_id.
CREATE UNIQUE INDEX loan_disbursements_cash_event_idx
  ON loan_disbursements(cash_event_id)
  WHERE cash_event_id IS NOT NULL;

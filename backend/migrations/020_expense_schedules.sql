CREATE TABLE expense_schedules(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    account_id BIGINT NOT NULL REFERENCES finance_accounts(id),
    store BIGINT NOT NULL DEFAULT 0,
    counterparty_id BIGINT,
    description TEXT NOT NULL,
    total_cents BIGINT NOT NULL,
    installment_count INTEGER NOT NULL,
    competence_mode TEXT NOT NULL,
    first_due TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_by BIGINT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX expense_schedules_company_idx ON expense_schedules(company,created_at);

ALTER TABLE financial_entries ADD COLUMN expense_schedule_id BIGINT;

CREATE INDEX financial_entries_expense_schedule_idx ON financial_entries(expense_schedule_id);

CREATE TABLE loans(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    store BIGINT,
    lender TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    principal_cents BIGINT NOT NULL,
    net_disbursement_cents BIGINT NOT NULL,
    cet_bps INTEGER,
    rate_bps INTEGER,
    grace_days INTEGER NOT NULL DEFAULT 0,
    start_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    active_schedule_id BIGINT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX loans_company_idx ON loans(company);

CREATE TABLE loan_schedules(
    id BIGINT PRIMARY KEY,
    loan_id BIGINT NOT NULL REFERENCES loans(id),
    status TEXT NOT NULL DEFAULT 'active',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX loan_schedules_loan_idx ON loan_schedules(loan_id);

CREATE TABLE loan_installments(
    id BIGINT PRIMARY KEY,
    schedule_id BIGINT NOT NULL REFERENCES loan_schedules(id),
    loan_id BIGINT NOT NULL REFERENCES loans(id),
    number INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    principal_cents BIGINT NOT NULL,
    interest_cents BIGINT NOT NULL,
    total_cents BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    paid_principal_cents BIGINT,
    paid_interest_cents BIGINT,
    paid_at TEXT,
    entry_id BIGINT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX loan_installments_schedule_idx ON loan_installments(schedule_id);
CREATE INDEX loan_installments_loan_idx ON loan_installments(loan_id,due_date);

CREATE TABLE loan_disbursements(
    id BIGINT PRIMARY KEY,
    loan_id BIGINT NOT NULL REFERENCES loans(id),
    amount_cents BIGINT NOT NULL,
    disbursed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX loan_disbursements_loan_idx ON loan_disbursements(loan_id);

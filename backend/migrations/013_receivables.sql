CREATE TABLE stone_receivables(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    cash_account_id BIGINT NOT NULL REFERENCES cash_accounts(id),
    transaction_key TEXT NOT NULL,
    installment_number INTEGER NOT NULL,
    brand_id TEXT,
    gross_cents BIGINT NOT NULL,
    fee_cents BIGINT NOT NULL,
    net_cents BIGINT NOT NULL,
    settlement_date TEXT,
    fee_entry_id BIGINT,
    created_at TEXT NOT NULL,
    UNIQUE(company,transaction_key,installment_number)
);

CREATE INDEX stone_receivables_company_idx ON stone_receivables(company,settlement_date);

CREATE TABLE reconciliation_groups(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'unmatched',
    difference_cents BIGINT NOT NULL DEFAULT 0,
    tolerance_cents BIGINT NOT NULL DEFAULT 0,
    method TEXT,
    candidates_json TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    confirmed_by BIGINT,
    confirmed_at TEXT
);

CREATE INDEX reconciliation_groups_company_idx ON reconciliation_groups(company,status);

CREATE TABLE reconciliation_links(
    id BIGINT PRIMARY KEY,
    group_id BIGINT NOT NULL REFERENCES reconciliation_groups(id),
    item_type TEXT NOT NULL,
    item_id BIGINT NOT NULL,
    amount_cents BIGINT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(item_type,item_id)
);

CREATE INDEX reconciliation_links_group_idx ON reconciliation_links(group_id);

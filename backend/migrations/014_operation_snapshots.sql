CREATE TABLE operation_snapshots(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    store BIGINT NOT NULL DEFAULT 0,
    product_id BIGINT NOT NULL,
    observed_at TEXT NOT NULL,
    stock_quantity REAL,
    price_cents BIGINT,
    cost_cents BIGINT,
    created_at TEXT NOT NULL,
    UNIQUE(company,store,product_id,observed_at)
);

CREATE INDEX operation_snapshots_lookup_idx ON operation_snapshots(company,product_id,observed_at);

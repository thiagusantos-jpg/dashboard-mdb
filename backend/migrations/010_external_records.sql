CREATE TABLE external_records(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    source TEXT NOT NULL,
    account_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(company,source,account_id,external_id,version)
);

CREATE INDEX external_records_lookup_idx ON external_records(company,source,account_id,external_id);

CREATE TABLE external_record_quarantine(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    source TEXT NOT NULL,
    account_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    version TEXT NOT NULL,
    existing_hash TEXT NOT NULL,
    conflicting_payload_json TEXT NOT NULL,
    conflicting_hash TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX external_record_quarantine_company_idx ON external_record_quarantine(company,resolved);

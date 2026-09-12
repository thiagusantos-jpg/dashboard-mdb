CREATE TABLE finance_audit(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id BIGINT,
    created_at TEXT NOT NULL
);

CREATE INDEX finance_audit_lookup_idx ON finance_audit(company,entity_type,entity_id,created_at);

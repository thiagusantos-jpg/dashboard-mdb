CREATE TABLE actions(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    alert_key TEXT NOT NULL,
    alert_version TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT NOT NULL DEFAULT 'medium',
    assignee BIGINT,
    due_date TEXT,
    evidence TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX actions_company_idx ON actions(company,status);
CREATE INDEX actions_alert_idx ON actions(company,alert_key);

CREATE TABLE action_events(
    id BIGINT PRIMARY KEY,
    action_id BIGINT NOT NULL REFERENCES actions(id),
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_by BIGINT,
    created_at TEXT NOT NULL
);

CREATE INDEX action_events_action_idx ON action_events(action_id,created_at);

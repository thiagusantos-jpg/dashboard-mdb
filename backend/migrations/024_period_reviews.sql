-- Monthly managerial review (plan C6, proposed as 017 and renumbered because
-- plan B already used 016-023). One row per company and competence month.
-- `revision` is the hash of the data the month's result read when it was
-- reviewed; the status shown is recomputed against the current hash on every
-- read, so any later change puts the month back in review without callers
-- having to remember to invalidate anything. Not an accounting close: nothing
-- is locked by this table.
CREATE TABLE period_reviews(
    company INTEGER NOT NULL,
    period TEXT NOT NULL,
    revision TEXT NOT NULL,
    status TEXT NOT NULL,
    reviewed_by BIGINT,
    reviewed_at TEXT,
    reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(company,period)
);

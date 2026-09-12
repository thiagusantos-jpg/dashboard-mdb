CREATE TABLE users(
    id BIGINT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

ALTER TABLE sessions ADD COLUMN user_id BIGINT REFERENCES users(id);

CREATE INDEX sessions_user_id_idx ON sessions(user_id);

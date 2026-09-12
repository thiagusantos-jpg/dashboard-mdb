CREATE TABLE roles(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

INSERT INTO roles(id,name) VALUES('administrator','Administrador');
INSERT INTO roles(id,name) VALUES('partner','Sócio');
INSERT INTO roles(id,name) VALUES('manager','Gerente');
INSERT INTO roles(id,name) VALUES('viewer','Consulta');

CREATE TABLE user_scopes(
    user_id BIGINT NOT NULL REFERENCES users(id),
    company INTEGER NOT NULL,
    store INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL REFERENCES roles(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id,company,store)
);

CREATE INDEX user_scopes_company_idx ON user_scopes(company,store);

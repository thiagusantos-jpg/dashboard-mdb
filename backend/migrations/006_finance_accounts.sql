CREATE TABLE finance_accounts(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    nature TEXT NOT NULL,
    system_key TEXT,
    sensitive INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(company,code),
    UNIQUE(company,system_key)
);

CREATE INDEX finance_accounts_company_idx ON finance_accounts(company,archived);

CREATE TABLE counterparties(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    document TEXT NOT NULL DEFAULT '',
    contact_json TEXT NOT NULL DEFAULT '{}',
    archived INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX counterparties_company_idx ON counterparties(company,kind,archived);

CREATE TABLE management_parameters(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    store BIGINT NOT NULL DEFAULT 0,
    category BIGINT NOT NULL DEFAULT 0,
    product BIGINT NOT NULL DEFAULT 0,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    responsible_user BIGINT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(company,key,store,category,product,effective_from)
);

CREATE INDEX management_parameters_lookup_idx ON management_parameters(
    company,key,effective_from,effective_to,store,category,product
);

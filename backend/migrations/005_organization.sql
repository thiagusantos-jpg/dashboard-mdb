CREATE TABLE organization_profiles(
    company INTEGER PRIMARY KEY,
    legal_name TEXT NOT NULL DEFAULT '',
    trade_name TEXT NOT NULL DEFAULT '',
    cnpj TEXT NOT NULL DEFAULT '',
    address_json TEXT NOT NULL DEFAULT '{}',
    contacts_json TEXT NOT NULL DEFAULT '{}',
    logo_url TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE stores(
    id BIGINT PRIMARY KEY,
    company INTEGER NOT NULL,
    mobne_id INTEGER,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'America/Sao_Paulo',
    active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(company,mobne_id)
);

CREATE INDEX stores_company_idx ON stores(company,active);

CREATE TABLE business_hours(
    store BIGINT NOT NULL REFERENCES stores(id),
    weekday INTEGER NOT NULL,
    opens_at TEXT,
    closes_at TEXT,
    closed INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(store,weekday)
);

CREATE TABLE calendar_exceptions(
    company INTEGER NOT NULL,
    store BIGINT NOT NULL DEFAULT 0,
    date TEXT NOT NULL,
    status TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(company,store,date)
);

CREATE INDEX calendar_exceptions_date_idx ON calendar_exceptions(company,date);

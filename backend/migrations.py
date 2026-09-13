from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Tuple


BASELINE_VERSION = 2
MIGRATION_DIRECTORY = Path(__file__).with_name("migrations")

# The first two schema versions predate the SQL migration runner and remain
# installed by backend.database so existing databases keep their history.
# New migrations start at version 3 and must be appended in order.
MIGRATIONS: Tuple[Tuple[int, str], ...] = (
    (3, "003_users.sql"),
    (4, "004_permissions.sql"),
    (5, "005_organization.sql"),
    (6, "006_finance_accounts.sql"),
    (7, "007_financial_entries.sql"),
    (8, "008_loans.sql"),
    (9, "009_cash_ledger.sql"),
    (10, "010_external_records.sql"),
    (11, "011_reconciliation.sql"),
    (12, "012_open_finance.sql"),
    (13, "013_receivables.sql"),
    (14, "014_operation_snapshots.sql"),
    (15, "015_actions.sql"),
    (16, "016_finance_audit.sql"),
    (17, "017_obligation_payments.sql"),
    (18, "018_payment_reversals.sql"),
    (19, "019_bank_payment_links.sql"),
    (20, "020_expense_schedules.sql"),
    (21, "021_loan_contracts.sql"),
    (22, "022_cash_event_allocation.sql"),
    (23, "023_dataset_summary.sql"),
    (24, "024_period_reviews.sql"),
    (25, "025_cost_behavior.sql"),
)


class MigrationError(RuntimeError):
    """Raised when the recorded schema cannot be migrated safely."""


def current_version(conn) -> int:
    row = conn.execute("SELECT MAX(version) AS version FROM schema_versions").fetchone()
    return int(row["version"] or 0)


def _validated_migrations() -> Iterable[Tuple[int, Path]]:
    versions = [version for version, _ in MIGRATIONS]
    expected = list(range(BASELINE_VERSION + 1, BASELINE_VERSION + 1 + len(versions)))
    if versions != expected:
        raise MigrationError(
            f"migration registry must contain consecutive versions after {BASELINE_VERSION}"
        )

    for version, filename in MIGRATIONS:
        path = MIGRATION_DIRECTORY / filename
        if not path.is_file():
            raise MigrationError(f"migration file is missing: {path}")
        yield version, path


def _strip_comments(sql: str) -> str:
    """Drop whole-line `--` comments before the SQL reaches the driver.

    backend/database.py's Postgres adapter rewrites this codebase's SQLite-style
    `?` placeholders into psycopg's `%s` across the entire statement text, so a
    question mark written in prose inside a migration comment becomes a
    placeholder with no value behind it: psycopg then refuses the statement and
    every later migration stops applying. Migrations carry a lot of rationale in
    comments and never take parameters, so the safe fix is to keep the prose in
    the file and out of the wire.

    Only lines that are entirely a comment are removed — never a trailing `--`,
    which could otherwise sit inside a string literal.
    """
    return "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )


def _statements(sql: str) -> Iterable[str]:
    for fragment in _strip_comments(sql).split(";"):
        statement = fragment.strip()
        if statement:
            yield statement


def migrate(conn) -> int:
    """Apply every unapplied migration exactly once and return the version."""
    registered = tuple(_validated_migrations())
    latest = registered[-1][0] if registered else BASELINE_VERSION
    installed = current_version(conn)
    if installed > latest:
        raise MigrationError(
            f"database schema {installed} is newer than this application ({latest})"
        )

    for version, path in registered:
        if version <= installed:
            continue
        for statement in _statements(path.read_text(encoding="utf-8")):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_versions(version, applied_at) VALUES(?, ?)",
            (version, datetime.now(timezone.utc).isoformat()),
        )
        installed = version
    return installed

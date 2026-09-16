"""Todo SQL do backend é escrito com placeholders '?' do SQLite, e
backend/database.py::_PGConn.execute troca '?' por '%s' antes de entregar a query ao
psycopg. Consequência: qualquer '%' que sobre no TEXTO do SQL é lido pelo psycopg como
um placeholder e derruba a query — mas só no Postgres, porque o SQLite ignora a
conversão inteira.

Foi assim que `alert_key LIKE 'vencimento:%'` (backend/actions.py) passou por toda a
suíte e quebrou em produção: os testes de due-reminders fixam `db.PG = False`, então
nunca exercitam esse adaptador. Valores com '%' vão como parâmetro ('LIKE ?'), que
funciona igual nos dois bancos.
"""
from __future__ import annotations

import ast
import pathlib
import re

BACKEND = pathlib.Path(__file__).resolve().parent.parent / "backend"

# Basta um destes para tratar a string como SQL; nenhum aparece em texto de tela.
SQL_STATEMENT = re.compile(
    r"\b(SELECT|INSERT INTO|UPDATE |DELETE FROM|CREATE TABLE|ALTER TABLE)\b", re.IGNORECASE
)


def _sql_literals():
    """Cada string literal do backend que é um comando SQL, com onde ela está."""
    for path in sorted(BACKEND.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and SQL_STATEMENT.search(node.value)
            ):
                yield path.relative_to(BACKEND.parent), node.lineno, node.value


def test_the_scan_actually_finds_the_sql_it_is_guarding():
    """Sem isto, um regex quebrado faria o teste abaixo passar varrendo nada."""
    found = list(_sql_literals())
    assert len(found) > 50, f"a varredura só achou {len(found)} comandos SQL — regex furado?"


def test_no_sql_carries_a_literal_percent_that_postgres_would_read_as_a_placeholder():
    offenders = [
        f"{path}:{line}: {sql.strip()[:120]}"
        for path, line, sql in _sql_literals()
        if "%" in sql
    ]
    assert not offenders, (
        "SQL com '%' no texto quebra no Postgres (psycopg lê como placeholder) mesmo "
        "passando no SQLite. Passe o valor como parâmetro — 'LIKE ?' com '...%' nos "
        "params:\n  " + "\n  ".join(offenders)
    )

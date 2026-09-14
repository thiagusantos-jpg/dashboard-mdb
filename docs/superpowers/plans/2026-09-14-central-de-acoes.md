# Central de Ações Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transformar a Central de Ações numa lista de trabalho clara, com os itens 1 a 14 do design aprovado:
- contadores, filtros e cartões legíveis;
- situação de hoje do alerta;
- concluir com anotação e histórico;
- responsável e prazo, e ação manual;
- resultado medido depois;
- contador no menu;
- aviso no Resumo.

**Architecture:**
- **Backend:** ganha uma coluna (`baseline_count`), edição de responsável, prazo e prioridade, lista de responsáveis possíveis, nomes no histórico, contagem em cada alerta do `/dashboard` e um endpoint de resultado para ações de preço.
- **Frontend:** a página é reescrita em `web/assets/actions.js`.
  - Regras puras testadas em vm: título curto, situação de hoje, atalhos, contadores e filtros.
  - Renderização em cartões, com gavetas (`openDrawer`) para concluir, descartar, histórico, editar e criar.
- **Onde lê os dados:** a Central passa a ler sempre o mês sincronizado mais recente e deixa de ter seletor de mês.

**Tech Stack:**
- FastAPI com pydantic 2.13, SQLite local e Postgres (Neon) em produção.
- Migrações SQL em `backend/migrations`.
- Vanilla JS sem bundler, com CSP `script-src 'self'; style-src 'self'`.
- Testes com `pytest` e `node --test`.

**Spec:** `docs/superpowers/specs/2026-09-14-central-de-acoes-design.md`. Simulação: https://claude.ai/code/artifact/758ae999-61a2-46bd-a09c-684c54a65869

## Global Constraints

- **Fora do escopo:** o item 15 (resumo semanal por e-mail/WhatsApp). O sócio gostou da ideia e ela está registrada na spec para outro momento.
- **CSP:** sem `<script>` inline, sem atributo `style=` e sem handlers inline. Eventos só com `addEventListener`.
- **Escopo global dos scripts:** todos os `web/assets/*.js` compartilham o escopo global, na ordem de `web/index.html`. `actions.js` carrega **antes** de `app.js`, então só pode usar funções de `app.js` (`api`, `esc`, `num`, `money`, `pct`, `routeHash`, `beginPage`, `ALERT_FILTERS`) **dentro** de funções, nunca no topo do arquivo. `sentenceCase` vem de `insights.js`, que carrega antes, e `openDrawer` vem de `finance-forms.js`, que também carrega antes.
- **Atributos reservados:** o clique global de `app.js` (`web/assets/app.js:236-265`) captura `data-tab`, `data-period`, `data-year`, `data-sync`, `data-nav`, `data-sort`, `data-explain` e `data-create-action`. A Central **não** pode usar esses atributos; use `data-actions-tab`, `data-action-*` e `data-new-action`.
- **Ids como texto:** ids de ações e usuários são `secrets.randbits(63)`. A API devolve os maiores que 2^53 como **string** (`BigIntSafeJSONResponse` em `backend/api.py:47-60`). No JS, compare ids sempre com `String(x) === String(y)` e nunca converta id com `Number()`. O pydantic 2 aceita string numérica em campo `int`.
- **Textos:** textos visíveis em português, com frases em caixa normal. Sem emoji em títulos; ícones com `icon(name)` de `web/assets/icons.js`. Nomes disponíveis: `star dollar-sign search triangle-alert map trending-up sparkles package link sliders-horizontal store menu x lightbulb calendar circle-check gauge rocket frown target megaphone users tag flame snowflake chevron-left chevron-right clipboard-list`.
- **Tema:** toda cor nova precisa funcionar no tema claro (padrão) e no escuro (`:root[data-theme="dark"]`). Superfícies brancas ganham a regra escura com `#1E1E1E` e `box-shadow: 0 0 0 1px #2A2A2A`, como `.change-card` no fim de `style.css`.
- **Acessibilidade:** alvos clicáveis com pelo menos 40px de altura (44px quando for botão isolado), foco visível e mensagens de estado em `role="status"`.
- **Testes:**
  - backend: `.venv/bin/python -m pytest -q tests` (a pasta `legacy/` fica fora);
  - frontend: `node --test tests/*.cjs`;
  - rodar os dois antes de cada checkpoint de commit.
- **Commits:** o sócio aprova cada commit e cada push. Nos passos "Checkpoint de commit", mostre o `git status` e **peça aprovação** antes de rodar `git commit`. Push só quando ele pedir. Mensagem terminando com `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Cache:** ao mexer em asset, atualizar o `?v=` correspondente em `web/index.html` (Task 8).

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `backend/migrations/026_action_tracking.sql` (novo) | coluna `actions.baseline_count` |
| `backend/migrations.py` | registrar a migração 26 |
| `backend/actions.py` | criar com contagem, assumir define o responsável, editar, responsáveis possíveis, nomes e anotação na listagem |
| `backend/routes/actions.py` | `baseline_count` e prazo como data na criação, `PATCH /actions/{id}`, `GET /actions/assignees`, `GET /actions/{id}/result` |
| `backend/api.py` | `dashboard_alerts()` pura com `count` em cada alerta; usa `receipts_between` do novo módulo |
| `backend/operations/sales_window.py` (novo) | `receipts_between()`, movido de `api.py` |
| `backend/operations/action_results.py` (novo) | `price_action_result()`: 30 dias antes e depois |
| `web/assets/actions.js` | regras puras, página, gavetas, contador do menu, aviso do Resumo |
| `web/assets/app.js` | alertas enviam prioridade e contagem; Resumo mostra o aviso; contador no login; Central lê o mês mais recente |
| `web/assets/insights.js` | ação de grupo do Mapa envia a contagem |
| `web/assets/navigation.js` | Central sem seletor de mês |
| `web/assets/style.css` | estilos da Central, gavetas, contador e aviso |
| `web/index.html` | contador no item do menu; versões dos assets |
| `tests/test_actions.py`, `tests/test_actions_api.py`, `tests/test_api.py`, `tests/test_action_results.py` (novo) | backend |
| `tests/test_actions_center_frontend.cjs` (novo), `tests/test_actions_frontend.cjs` | frontend |

---

### Task 1: Ações guardam contagem, responsável e anotação

**Files:**
- Create: `backend/migrations/026_action_tracking.sql`
- Modify: `backend/migrations.py:38` (tupla `MIGRATIONS`)
- Modify: `backend/actions.py` (arquivo inteiro, 110 linhas)
- Test: `tests/test_actions.py`

**Interfaces:**
- Produces:
  - `actions.create_from_alert(company, alert_key, alert_version, title, *, priority="medium", assignee=None, due_date=None, evidence="", baseline_count=None, created_by=None) -> dict`
  - `actions.transition_action(action_id, to_status, *, note="", created_by=None) -> dict`: ao ir para `in_progress` sem responsável, o responsável vira `created_by`.
  - `actions.update_action(action_id, changes: dict, *, created_by=None) -> dict`: aceita as chaves `assignee`, `due_date` (`"YYYY-MM-DD"` ou `None`) e `priority`; levanta `ValueError` se a ação estiver encerrada ou não existir.
  - `actions.assignees(company) -> list[{"id", "name"}]` e `actions.is_assignable(company, user_id) -> bool`.
  - Toda ação devolvida (listagem, criação, transição, edição) traz também `assignee_name` e `status_note` (anotação do último evento que levou à situação atual).
  - `actions.list_events(action_id)` traz `created_by_name`.

- [ ] **Step 1: Write the failing tests**

Acrescente ao fim de `tests/test_actions.py`:

```python
def _user(user_id, name, company=COMPANY, admin=0):
    ts = db.now()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO users(id,email,name,password_hash,password_salt,is_admin,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (user_id, f"u{user_id}@loja.test", name, "x", "x", admin, ts, ts),
        )
        if company is not None:
            conn.execute(
                "INSERT INTO user_scopes(user_id,company,store,role,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (user_id, company, None, "partner", ts, ts),
            )


def test_baseline_count_is_kept(actions_db):
    action = actions.create_from_alert(COMPANY, "estoque", "v1", "170 produto(s) sem estoque", baseline_count=170)
    assert action["baseline_count"] == 170
    assert actions.list_actions(COMPANY)[0]["baseline_count"] == 170


def test_taking_an_action_makes_the_partner_its_assignee(actions_db):
    _user(10, "Ana Sócia")
    action = actions.create_from_alert(COMPANY, "estoque", "v1", "Ruptura")

    taken = actions.transition_action(action["id"], "in_progress", created_by=10)

    assert taken["assignee"] == 10
    assert taken["assignee_name"] == "Ana Sócia"


def test_taking_keeps_an_existing_assignee(actions_db):
    _user(10, "Ana")
    _user(11, "Bia")
    action = actions.create_from_alert(COMPANY, "estoque", "v1", "Ruptura", assignee=11)

    taken = actions.transition_action(action["id"], "in_progress", created_by=10)

    assert taken["assignee"] == 11


def test_update_sets_assignee_due_date_and_priority_and_logs_it(actions_db):
    _user(10, "Ana Sócia")
    action = actions.create_from_alert(COMPANY, "preco", "v1", "44 produtos")

    updated = actions.update_action(
        action["id"], {"assignee": 10, "due_date": "2026-09-20", "priority": "high"}, created_by=10
    )

    assert (updated["assignee"], updated["due_date"], updated["priority"]) == (10, "2026-09-20", "high")
    last = actions.list_events(action["id"])[-1]
    assert last["event_type"] == "updated"
    assert last["note"] == "Responsável: Ana Sócia; Prazo: 20/09/2026; Prioridade: alta"
    assert last["created_by_name"] == "Ana Sócia"


def test_update_can_clear_the_due_date(actions_db):
    action = actions.create_from_alert(COMPANY, "preco", "v1", "44 produtos", due_date="2026-09-20")
    cleared = actions.update_action(action["id"], {"due_date": None})
    assert cleared["due_date"] is None
    assert actions.list_events(action["id"])[-1]["note"] == "Prazo removido"


def test_update_without_changes_logs_nothing(actions_db):
    action = actions.create_from_alert(COMPANY, "preco", "v1", "44 produtos")
    actions.update_action(action["id"], {"priority": "medium"})
    assert len(actions.list_events(action["id"])) == 1


def test_closed_action_cannot_be_edited(actions_db):
    action = actions.create_from_alert(COMPANY, "preco", "v1", "44 produtos")
    actions.transition_action(action["id"], "resolved")
    with pytest.raises(ValueError):
        actions.update_action(action["id"], {"priority": "high"})


def test_status_note_is_the_note_that_closed_the_action(actions_db):
    action = actions.create_from_alert(COMPANY, "preco", "v1", "44 produtos")
    actions.transition_action(action["id"], "in_progress", note="Vou ver com o fornecedor")
    actions.transition_action(action["id"], "resolved", note="Preço corrigido")
    assert actions.list_actions(COMPANY)[0]["status_note"] == "Preço corrigido"


def test_assignees_are_the_company_partners_and_global_admins(actions_db):
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(2,'Loja 2')")
    _user(10, "Ana")
    _user(11, "Bia sem acesso", company=None)
    _user(12, "Admin geral", company=None, admin=1)
    _user(13, "Caio da outra loja", company=2)

    names = {u["name"] for u in actions.assignees(COMPANY)}

    assert {"Ana", "Admin geral"} <= names
    assert "Bia sem acesso" not in names
    assert "Caio da outra loja" not in names
    assert actions.is_assignable(COMPANY, 10)
    assert not actions.is_assignable(COMPANY, 13)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_actions.py`
Expected: FAIL. `create_from_alert()` recebe `baseline_count` inesperado, e não existem `update_action`/`assignees`.

- [ ] **Step 3: Create the migration**

`backend/migrations/026_action_tracking.sql`:

```sql
-- How big the problem behind an action was when it was created (170 produtos sem
-- estoque), so the Central de Ações can show how it moved since. NULL for older
-- rows and for actions that are not about a count (one product's price, a manual task).
ALTER TABLE actions ADD COLUMN baseline_count INTEGER;
```

Em `backend/migrations.py`, depois de `(25, "025_cost_behavior.sql"),` acrescente:

```python
    (26, "026_action_tracking.sql"),
```

- [ ] **Step 4: Rewrite `backend/actions.py`**

Substitua o arquivo inteiro por:

```python
from __future__ import annotations

import secrets
from typing import Optional

from . import database as db


_ACTIVE_STATUSES = ("open", "in_progress")
_TERMINAL_STATUSES = ("resolved", "dismissed")
_ALLOWED_TRANSITIONS = {
    "open": {"in_progress", "resolved", "dismissed"},
    "in_progress": {"resolved", "dismissed", "open"},
}
_EDITABLE = ("assignee", "due_date", "priority")
_PRIORITY_WORDS = {"high": "alta", "medium": "média", "low": "baixa"}

# Every action handed to the UI carries its assignee's name and the note written
# when it reached its current status ("O que foi feito" / motivo do descarte).
_SELECT_ACTION = """
    SELECT a.*, u.name AS assignee_name,
        (SELECT e.note FROM action_events e
         WHERE e.action_id=a.id AND e.to_status=a.status
         ORDER BY e.created_at DESC, e.id DESC LIMIT 1) AS status_note
    FROM actions a LEFT JOIN users u ON u.id=a.assignee
"""


def _new_id() -> int:
    return secrets.randbits(63) or 1


def _get(conn, action_id: int) -> Optional[dict]:
    row = conn.execute(_SELECT_ACTION + " WHERE a.id=?", (action_id,)).fetchone()
    return dict(row) if row else None


def create_from_alert(
    company: int,
    alert_key: str,
    alert_version: str,
    title: str,
    *,
    priority: str = "medium",
    assignee: Optional[int] = None,
    due_date: Optional[str] = None,
    evidence: str = "",
    baseline_count: Optional[int] = None,
    created_by: Optional[int] = None,
) -> dict:
    """Same alert, same version, already active → same action (no duplicate
    work items every time the alert re-fires). A new version of a condition
    that was already resolved is a genuinely new problem and gets a new id —
    closing an action never blocks the same alert from being raised again."""
    with db.connection() as conn:
        existing = conn.execute(
            """
            SELECT id FROM actions
            WHERE company=? AND alert_key=? AND alert_version=? AND status IN ('open','in_progress')
            """,
            (company, alert_key, alert_version),
        ).fetchone()
        if existing:
            return _get(conn, existing["id"])

        action_id = _new_id()
        timestamp = db.now()
        conn.execute(
            """
            INSERT INTO actions(
                id,company,alert_key,alert_version,title,status,priority,assignee,due_date,
                evidence,baseline_count,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                action_id, company, alert_key, alert_version, title.strip(), "open", priority,
                assignee, due_date, evidence, baseline_count, timestamp, timestamp,
            ),
        )
        conn.execute(
            "INSERT INTO action_events(id,action_id,event_type,to_status,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (_new_id(), action_id, "created", "open", created_by, timestamp),
        )
        return _get(conn, action_id)


def transition_action(action_id: int, to_status: str, *, note: str = "", created_by: Optional[int] = None) -> dict:
    timestamp = db.now()
    with db.connection() as conn:
        action = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if not action:
            raise ValueError("Ação não encontrada.")
        from_status = action["status"]
        allowed = _ALLOWED_TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise ValueError(f"Transição inválida: {from_status} → {to_status}.")
        resolved_at = timestamp if to_status in _TERMINAL_STATUSES else action["resolved_at"]
        # "Assumir" is how a partner takes an action: without an assignee, it becomes theirs.
        assignee = action["assignee"]
        if to_status == "in_progress" and assignee is None and created_by is not None:
            assignee = created_by
        conn.execute(
            "UPDATE actions SET status=?,resolved_at=?,assignee=?,updated_at=? WHERE id=?",
            (to_status, resolved_at, assignee, timestamp, action_id),
        )
        conn.execute(
            """
            INSERT INTO action_events(id,action_id,event_type,from_status,to_status,note,created_by,created_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (_new_id(), action_id, "status_changed", from_status, to_status, note.strip(), created_by, timestamp),
        )
        return _get(conn, action_id)


def _describe(changed: dict, conn) -> str:
    parts = []
    if "assignee" in changed:
        if changed["assignee"] is None:
            parts.append("Responsável removido")
        else:
            row = conn.execute("SELECT name FROM users WHERE id=?", (changed["assignee"],)).fetchone()
            parts.append(f"Responsável: {row['name'] if row else 'usuário removido'}")
    if "due_date" in changed:
        due = changed["due_date"]
        parts.append(f"Prazo: {due[8:10]}/{due[5:7]}/{due[:4]}" if due else "Prazo removido")
    if "priority" in changed:
        parts.append(f"Prioridade: {_PRIORITY_WORDS[changed['priority']]}")
    return "; ".join(parts)


def update_action(action_id: int, changes: dict, *, created_by: Optional[int] = None) -> dict:
    """Assignee, due date and priority of an open action. Each real change is one
    'updated' event whose note says what changed, in the partner's words."""
    fields = {key: value for key, value in changes.items() if key in _EDITABLE}
    timestamp = db.now()
    with db.connection() as conn:
        action = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if not action:
            raise ValueError("Ação não encontrada.")
        if action["status"] not in _ACTIVE_STATUSES:
            raise ValueError("Ação encerrada não pode ser alterada.")
        changed = {key: value for key, value in fields.items() if action[key] != value}
        if not changed:
            return _get(conn, action_id)
        assignments = ",".join(f"{key}=?" for key in changed)
        conn.execute(
            f"UPDATE actions SET {assignments},updated_at=? WHERE id=?",
            (*changed.values(), timestamp, action_id),
        )
        conn.execute(
            "INSERT INTO action_events(id,action_id,event_type,note,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (_new_id(), action_id, "updated", _describe(changed, conn), created_by, timestamp),
        )
        return _get(conn, action_id)


def assignees(company: int) -> list:
    """People who can own an action of this company: its active users and global admins."""
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT u.id,u.name FROM users u
            WHERE u.active=1 AND (
                u.is_admin=1 OR EXISTS(SELECT 1 FROM user_scopes s WHERE s.user_id=u.id AND s.company=?)
            )
            ORDER BY u.name,u.id
            """,
            (company,),
        ).fetchall()
    return [dict(row) for row in rows]


def is_assignable(company: int, user_id: int) -> bool:
    return any(person["id"] == user_id for person in assignees(company))


def list_events(action_id: int) -> list:
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT e.*, u.name AS created_by_name FROM action_events e
            LEFT JOIN users u ON u.id=e.created_by
            WHERE e.action_id=? ORDER BY e.created_at,e.id
            """,
            (action_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_actions(company: int, *, status: Optional[str] = None) -> list:
    where = " AND a.status=?" if status else ""
    params = (company, status) if status else (company,)
    with db.connection() as conn:
        rows = conn.execute(
            _SELECT_ACTION + " WHERE a.company=?" + where + " ORDER BY a.created_at DESC", params
        ).fetchall()
    return [dict(row) for row in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_actions.py tests/test_actions_api.py tests/test_migrations.py`
Expected: PASS em todos, inclusive os 4 testes antigos de `test_actions.py`.

---

### Task 2: API de ações e contagem nos alertas

**Files:**
- Modify: `backend/routes/actions.py`
- Modify: `backend/api.py:380-398` (bloco `alerts=[]` dentro de `dashboard()`)
- Test: `tests/test_actions_api.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `actions.update_action`, `actions.assignees`, `actions.is_assignable` (Task 1).
- Produces:
  - `POST /api/companies/{c}/actions` aceita `baseline_count` (int ≥ 0 ou null) e `due_date` (`YYYY-MM-DD` ou null). Recusa com 422 um `assignee` sem acesso à empresa.
  - `PATCH /api/companies/{c}/actions/{id}` recebe `{assignee?, due_date?, priority?}`. Só as chaves enviadas mudam, e `null` limpa o responsável ou o prazo. Devolve a ação.
  - `GET /api/companies/{c}/actions/assignees` devolve `[{id, name}]`.
  - `api.dashboard_alerts(inventory, stock_synced, unknown_items) -> list`: cada alerta tem `severity`, `type`, `message` e `count` (int, ou `None` para `integracao`).

- [ ] **Step 1: Write the failing tests**

Acrescente ao fim de `tests/test_actions_api.py`:

```python
def _create(client, **extra):
    body = {"alert_key": "estoque", "alert_version": "v1", "title": "170 produto(s) sem estoque", **extra}
    created = client.post("/api/companies/1/actions", json=body)
    assert created.status_code == 201, created.text
    return created.json()


def test_create_keeps_the_count_behind_the_alert(client):
    assert _create(client, baseline_count=170)["baseline_count"] == 170


def test_assign_set_and_clear_the_due_date(client):
    action = _create(client)
    people = client.get("/api/companies/1/actions/assignees").json()
    assert people, "the logged-in admin can own actions"

    patched = client.patch(
        f"/api/companies/1/actions/{action['id']}",
        json={"assignee": people[0]["id"], "due_date": "2026-09-20", "priority": "high"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["due_date"] == "2026-09-20"
    assert patched.json()["priority"] == "high"

    cleared = client.patch(f"/api/companies/1/actions/{action['id']}", json={"due_date": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["due_date"] is None
    assert str(cleared.json()["assignee"]) == str(people[0]["id"])  # untouched: not sent


def test_assignee_without_access_is_refused(client):
    action = _create(client)
    response = client.patch(f"/api/companies/1/actions/{action['id']}", json={"assignee": 999})
    assert response.status_code == 422


def test_priority_cannot_be_cleared(client):
    action = _create(client)
    response = client.patch(f"/api/companies/1/actions/{action['id']}", json={"priority": None})
    assert response.status_code == 422


def test_taking_an_action_assigns_the_logged_partner(client):
    action = _create(client)
    taken = client.post(f"/api/companies/1/actions/{action['id']}/transition", json={"to_status": "in_progress"})
    assert taken.status_code == 200, taken.text
    assert taken.json()["assignee"] is not None
    assert taken.json()["assignee_name"] is not None


def test_closing_note_is_listed(client):
    action = _create(client)
    client.post(f"/api/companies/1/actions/{action['id']}/transition", json={"to_status": "resolved", "note": "Pedido feito"})
    listed = client.get("/api/companies/1/actions").json()
    assert listed[0]["status_note"] == "Pedido feito"
```

Acrescente ao fim de `tests/test_api.py`. Se o arquivo ainda não importa `api`, adicione `from backend import api` junto aos imports do topo.

```python
def test_dashboard_alerts_carry_the_count_behind_each_message():
    inventory = [
        {'id': 1, 'name': 'A', 'abc': 'A', 'stock': 0, 'current_price': 500, 'current_cost': 300},
        {'id': 2, 'name': 'B', 'abc': 'A', 'stock': -2, 'current_price': 200, 'current_cost': 250},
        {'id': 3, 'name': 'C', 'abc': 'B', 'stock': 0, 'current_price': None, 'current_cost': 100},
    ]
    alerts = {a['type']: a for a in api.dashboard_alerts(inventory, True, 12)}
    assert alerts['estoque']['count'] == 2
    assert alerts['estoque']['message'].startswith('2 produto(s) da curva A')
    assert alerts['preco']['count'] == 1
    assert alerts['custo']['count'] == 12
    assert 'integracao' not in alerts

    unsynced = api.dashboard_alerts([], False, 0)
    assert [(a['type'], a['count']) for a in unsynced] == [('integracao', None)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_actions_api.py tests/test_api.py -k "count or assign or priority or taking or closing_note or dashboard_alerts"`
Expected: FAIL. Faltam as rotas (405/404) e `api.dashboard_alerts`.

- [ ] **Step 3: Rewrite `backend/routes/actions.py`**

Substitua o bloco que vai do início do arquivo até o fim de `get_action_events` (linhas 1-91). As rotas `promotions/result` e `baskets` ficam como estão.

```python
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import actions, database as db, permissions, security
from ..operations.baskets import basket_pairs
from ..operations.promotions import promotion_result


router = APIRouter(prefix="/api/companies/{company}", tags=["operations"])


def _require_company(company: int) -> None:
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone():
            raise HTTPException(404, "Empresa não encontrada.")


def _require_action(company: int, action_id: int) -> None:
    with db.connection() as conn:
        row = conn.execute("SELECT company FROM actions WHERE id=?", (action_id,)).fetchone()
    if not row or row["company"] != company:
        raise HTTPException(404, "Ação não encontrada.")


def _require_assignable(company: int, assignee: Optional[int]) -> None:
    if assignee is not None and not actions.is_assignable(company, assignee):
        raise HTTPException(422, "Responsável não tem acesso a esta empresa.")


class ActionCreate(BaseModel):
    alert_key: str = Field(min_length=1, max_length=200)
    alert_version: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=240)
    priority: str = Field(default="medium", pattern="^(low|medium|high)$")
    assignee: Optional[int] = None
    due_date: Optional[date] = None
    evidence: str = Field(default="", max_length=2000)
    baseline_count: Optional[int] = Field(default=None, ge=0)


class ActionTransition(BaseModel):
    to_status: str = Field(pattern="^(open|in_progress|resolved|dismissed)$")
    note: str = Field(default="", max_length=2000)


class ActionUpdate(BaseModel):
    # Only the keys actually sent change (model_fields_set); an explicit null clears.
    assignee: Optional[int] = None
    due_date: Optional[date] = None
    priority: Optional[str] = Field(default=None, pattern="^(low|medium|high)$")


@router.get(
    "/actions",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def list_company_actions(company: int, status: Optional[str] = None):
    _require_company(company)
    return actions.list_actions(company, status=status)


@router.get(
    "/actions/assignees",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def list_action_assignees(company: int):
    _require_company(company)
    return actions.assignees(company)


@router.post(
    "/actions",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def create_company_action(
    company: int, body: ActionCreate,
    auth: security.AuthContext = Depends(permissions.require_permission("dashboard.read")),
):
    _require_company(company)
    _require_assignable(company, body.assignee)
    return actions.create_from_alert(
        company, body.alert_key, body.alert_version, body.title,
        priority=body.priority, assignee=body.assignee,
        due_date=body.due_date.isoformat() if body.due_date else None,
        evidence=body.evidence, baseline_count=body.baseline_count, created_by=auth.user_id,
    )


@router.patch(
    "/actions/{action_id}",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def update_company_action(
    company: int, action_id: int, body: ActionUpdate,
    auth: security.AuthContext = Depends(permissions.require_permission("dashboard.read")),
):
    _require_action(company, action_id)
    changes = {}
    for field in body.model_fields_set:
        value = getattr(body, field)
        changes[field] = value.isoformat() if isinstance(value, date) else value
    if "priority" in changes and changes["priority"] is None:
        raise HTTPException(422, "A prioridade não pode ficar vazia.")
    _require_assignable(company, changes.get("assignee"))
    try:
        return actions.update_action(action_id, changes, created_by=auth.user_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/actions/{action_id}/transition",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def transition_company_action(
    company: int, action_id: int, body: ActionTransition,
    auth: security.AuthContext = Depends(permissions.require_permission("dashboard.read")),
):
    _require_action(company, action_id)
    try:
        return actions.transition_action(action_id, body.to_status, note=body.note, created_by=auth.user_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get(
    "/actions/{action_id}/events",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def get_action_events(company: int, action_id: int):
    _require_action(company, action_id)
    return actions.list_events(action_id)
```

- [ ] **Step 4: Extract `dashboard_alerts` in `backend/api.py`**

Logo acima de `@app.get('/api/companies/{company}/dashboard',...` (hoje na linha 321), acrescente:

```python
def dashboard_alerts(inventory,stock_synced,unknown_items):
    """Resumo attention points. `count` is the number behind each message, so an action
    created from it can later say how the problem moved (Central de Ações)."""
    alerts=[]
    low_stock=[p for p in inventory if p['abc']=='A' and p['stock'] is not None and p['stock']<=0]
    if not stock_synced:
        alerts.append({'severity':'medium','type':'integracao','count':None,
            'message':'Estoque ainda não sincronizado. Consulte a integração Mobne para concluir a carga.'})
    if low_stock:
        names=', '.join(p['name'] for p in low_stock[:5])+('…' if len(low_stock)>5 else '')
        alerts.append({'severity':'high','type':'estoque','count':len(low_stock),
            'message':f'{len(low_stock)} produto(s) da curva A com estoque zerado ou negativo no Mobne: {names}.'})
    underpriced=[p for p in inventory if p['current_price'] is not None and p['current_cost'] is not None
                 and p['current_price']<p['current_cost']]
    if underpriced:
        names=', '.join(p['name'] for p in underpriced[:5])+('…' if len(underpriced)>5 else '')
        alerts.append({'severity':'high','type':'preco','count':len(underpriced),
            'message':f'{len(underpriced)} produto(s) vendendo abaixo do custo atual do Mobne: {names}.'})
    if unknown_items>0:
        alerts.append({'severity':'medium','type':'custo','count':unknown_items,
            'message':f'{unknown_items} item(ns) vendido(s) sem custo conhecido — lucro do período não pôde ser calculado para eles.'})
    return alerts
```

Dentro de `dashboard()`, apague o bloco que começa em `alerts=[]` e termina na última linha do `if data['totals']['unknown']>0:` (antes de `return {**data,...`). Troque por:

```python
    alerts=dashboard_alerts(inventory,stock is not None,data['totals']['unknown'])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_actions_api.py tests/test_api.py tests/test_actions.py`
Expected: PASS.

---

### Task 3: Resultado da ação de preço (item 12)

**Files:**
- Create: `backend/operations/sales_window.py`
- Create: `backend/operations/action_results.py`
- Modify: `backend/api.py:19` (import) e `backend/api.py:277-286` (remover `_receipts_between`)
- Modify: `backend/routes/actions.py` (nova rota)
- Test: `tests/test_action_results.py` (novo)

**Interfaces:**
- Produces:
  - `sales_window.receipts_between(company, start, end, conn) -> (receipts, analysis)`: mesmo contrato do antigo `api._receipts_between`.
  - `action_results.price_action_result(company, action: dict, today: date, conn) -> dict`, com `kind` em:
    - `'none'`: não é ação `preco:<id>`;
    - `'pending'`: ainda não foi concluída;
    - `'measuring'`: menos de 30 dias depois; `after` pode ser `None` no dia da conclusão;
    - `'measured'`.
  - Chaves: `days`, `total` (30), `ready_on` (ISO), `before` e `after` (`{start, end, revenue, quantity, margin}`).
  - `GET /api/companies/{c}/actions/{id}/result` devolve esse dict.

- [ ] **Step 1: Write the failing tests**

`tests/test_action_results.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from backend import actions, database as db
from backend.operations.action_results import price_action_result


COMPANY = 1


@pytest.fixture
def results_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "results.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def _receipt(doc, day, product, revenue, cost):
    return {"id": doc, "reference_id": 0, "aliases": [doc], "date": day, "status": "V", "species": "CF",
            "revenue": revenue,
            "items": [{"id": 1, "product_id": product, "quantity": "1", "revenue": revenue, "cost": cost,
                       "unit_cost": str(cost / 100), "status": "V"}]}


def _month(period, receipts):
    db.put_dataset(COMPANY, "sales", period, {
        "raw_count": len(receipts), "receipts": receipts, "analysis": [],
        "start": f"{period}-01", "end": f"{period}-28",
    })


def _price_action(created_at, resolved_at=None, key="preco:77"):
    action = actions.create_from_alert(COMPANY, key, "259", "Reajustar preço")
    with db.connection() as conn:
        if resolved_at:
            conn.execute("UPDATE actions SET status='resolved',created_at=?,resolved_at=? WHERE id=?",
                         (created_at, resolved_at, action["id"]))
        else:
            conn.execute("UPDATE actions SET created_at=? WHERE id=?", (created_at, action["id"]))
        return dict(conn.execute("SELECT * FROM actions WHERE id=?", (action["id"],)).fetchone())


def _seed():
    # Before (07-17..08-15): two sales at a 20% margin. After (08-21..09-19): two at 33,33%.
    _month("2026-07", [_receipt(1, "2026-07-20", 77, 1000, 800)])
    _month("2026-08", [_receipt(2, "2026-08-10", 77, 1000, 800), _receipt(3, "2026-08-25", 77, 1200, 800),
                       _receipt(4, "2026-08-26", 99, 5000, 1000)])
    _month("2026-09", [_receipt(5, "2026-09-05", 77, 1200, 800)])


def test_measured_after_thirty_days(results_db):
    _seed()
    action = _price_action("2026-08-16T15:00:00+00:00", "2026-08-20T15:00:00+00:00")
    with db.connection() as conn:
        result = price_action_result(COMPANY, action, date(2026, 10, 1), conn)
    assert result["kind"] == "measured"
    assert result["days"] == 30
    assert (result["before"]["start"], result["before"]["end"]) == ("2026-07-17", "2026-08-15")
    assert (result["after"]["start"], result["after"]["end"]) == ("2026-08-21", "2026-09-19")
    assert (result["before"]["revenue"], result["before"]["margin"]) == (2000, 20.0)
    assert (result["after"]["revenue"], result["after"]["margin"]) == (2400, 33.33)


def test_partial_while_the_thirty_days_have_not_passed(results_db):
    _seed()
    action = _price_action("2026-08-16T15:00:00+00:00", "2026-08-20T15:00:00+00:00")
    with db.connection() as conn:
        partial = price_action_result(COMPANY, action, date(2026, 8, 26), conn)
        same_day = price_action_result(COMPANY, action, date(2026, 8, 21), conn)
    assert (partial["kind"], partial["days"], partial["after"]["revenue"]) == ("measuring", 5, 1200)
    assert partial["ready_on"] == "2026-09-20"
    assert (same_day["kind"], same_day["days"], same_day["after"]) == ("measuring", 0, None)


def test_other_actions_have_no_price_result(results_db):
    open_price = _price_action("2026-08-16T15:00:00+00:00")
    stock = _price_action("2026-08-16T15:00:00+00:00", "2026-08-20T15:00:00+00:00", key="estoque")
    with db.connection() as conn:
        assert price_action_result(COMPANY, open_price, date(2026, 10, 1), conn) == {"kind": "pending"}
        assert price_action_result(COMPANY, stock, date(2026, 10, 1), conn) == {"kind": "none"}
```

Acrescente ao fim de `tests/test_actions_api.py`:

```python
def test_result_endpoint_answers_for_any_action(client):
    action = _create(client)
    response = client.get(f"/api/companies/1/actions/{action['id']}/result")
    assert response.status_code == 200, response.text
    assert response.json() == {"kind": "none"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_action_results.py tests/test_actions_api.py -k "result or measured or partial or other_actions"`
Expected: FAIL com `ModuleNotFoundError: backend.operations.action_results` e 404 na rota.

- [ ] **Step 3: Move `receipts_between` to its own module**

`backend/operations/sales_window.py`:

```python
"""Receipts dated in a day range, read across every synced sales month the range spans.
Shared by the product map (backend/api.py) and the price action result."""
from __future__ import annotations

from .. import database as db, sync


def receipts_between(company, start, end, conn):
    """Receipts dated start..end (ISO days) and their analysis rows, across the sales months they span."""
    receipts, analysis = [], []
    for key in sync.month_range(start[:7], end[:7]):
        ds = db.dataset(company, 'sales', key, conn)
        if not ds or not ds['payload'].get('raw_count'):
            continue
        receipts += [r for r in ds['payload']['receipts'] if start <= r['date'] <= end]
        analysis += ds['payload'].get('analysis') or []
    return receipts, analysis
```

Em `backend/api.py`:
1. Apague a função `_receipts_between` inteira (linhas 277-286).
2. Depois de `from .operations.goals import current_goal_progress`, acrescente:

```python
from .operations.sales_window import receipts_between as _receipts_between
```

- [ ] **Step 4: Write `backend/operations/action_results.py`**

```python
"""What a price action changed: the product's sales and margin in the 30 days before
the action was created against the 30 days after it was concluded. Partial while those
30 days have not passed yet, so the partner sees it moving instead of waiting a month."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import models
from .sales_window import receipts_between

RESULT_DAYS = 30
_PRICE_KEY = re.compile(r"^preco:(\d+)$")


def _local_day(iso):
    return datetime.fromisoformat(iso).astimezone(ZoneInfo('America/Sao_Paulo')).date()


def _window(company, product_id, start, end, conn):
    receipts, analysis = receipts_between(company, start.isoformat(), end.isoformat(), conn)
    products = models.summarize(receipts, None, analysis)['products']
    p = next((x for x in products if x['id'] == product_id), None)
    return {'start': start.isoformat(), 'end': end.isoformat(),
            'revenue': p['revenue'] if p else 0, 'quantity': p['quantity'] if p else 0.0,
            'margin': p['margin'] if p else None}


def price_action_result(company, action, today, conn):
    match = _PRICE_KEY.match(action['alert_key'] or '')
    if not match:
        return {'kind': 'none'}
    if action['status'] != 'resolved' or not action['resolved_at']:
        return {'kind': 'pending'}
    product_id = int(match.group(1))
    created = _local_day(action['created_at'])
    resolved = _local_day(action['resolved_at'])
    before = _window(company, product_id, created - timedelta(days=RESULT_DAYS), created - timedelta(days=1), conn)
    after_start = resolved + timedelta(days=1)
    after_end = min(resolved + timedelta(days=RESULT_DAYS), today - timedelta(days=1))
    ready_on = (resolved + timedelta(days=RESULT_DAYS + 1)).isoformat()
    if after_end < after_start:
        return {'kind': 'measuring', 'days': 0, 'total': RESULT_DAYS, 'ready_on': ready_on, 'before': before, 'after': None}
    days = (after_end - after_start).days + 1
    return {'kind': 'measured' if days >= RESULT_DAYS else 'measuring', 'days': days, 'total': RESULT_DAYS,
            'ready_on': ready_on, 'before': before,
            'after': _window(company, product_id, after_start, after_end, conn)}
```

- [ ] **Step 5: Add the route**

Em `backend/routes/actions.py`:
1. Troque `from datetime import date` por `from datetime import date, datetime`.
2. Acrescente `from zoneinfo import ZoneInfo` abaixo dele.
3. Acrescente `from ..operations import action_results` junto aos imports de `..operations`.
4. Depois de `get_action_events`, acrescente:

```python
@router.get(
    "/actions/{action_id}/result",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def get_action_result(company: int, action_id: int):
    _require_action(company, action_id)
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    with db.connection() as conn:
        action = dict(conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())
        return action_results.price_action_result(company, action, today, conn)
```

- [ ] **Step 6: Run the whole backend suite**

Run: `.venv/bin/python -m pytest -q tests`
Expected: PASS em tudo, inclusive `test_product_map_groups_the_last_30_days_and_lists_what_changed_group`, que agora usa o módulo movido.

- [ ] **Step 7: Checkpoint de commit (backend)**

Mostre `git status` e peça aprovação ao sócio. Com o "pode":

```bash
git add backend/migrations/026_action_tracking.sql backend/migrations.py backend/actions.py backend/routes/actions.py backend/api.py backend/operations/sales_window.py backend/operations/action_results.py tests/test_actions.py tests/test_actions_api.py tests/test_api.py tests/test_action_results.py
git commit -m "feat: track assignee, due date, alert count and price result for actions"
```

---

### Task 4: Regras puras da Central (títulos, situação de hoje, atalhos, contadores)

**Files:**
- Modify: `web/assets/actions.js`: inserir as regras depois de `'use strict';` (linha 3). O resto do arquivo antigo fica até a Task 5.
- Test: `tests/test_actions_center_frontend.cjs` (novo)

**Interfaces:**
- Consumes, em tempo de execução (globais de `app.js`/`insights.js`): `esc`, `num`, `money`, `pct`, `icon`, `routeHash`, `ALERT_FILTERS`, `sentenceCase`.
- Produces (funções globais):
  - Constantes: `ACTION_ORIGINS`, `ACTION_PRIORITY_LABELS`, `ACTION_TABS`, `ACTION_TAB_STATUSES`.
  - Datas: `isoDay(date) -> 'YYYY-MM-DD'`, `shiftIsoDay(iso, days) -> iso`, `daysBetween(isoA, isoB) -> int`, `shortDay(iso) -> 'DD/MM'`, `actionAge(isoTimestamp, today) -> 'hoje'|'ontem'|'há N dias'`.
  - Identificação da ação: `actionOrigin(alertKey)` (`'estoque'|'preco'|'custo'|'integracao'|'mapa'|'manual'`), `actionBaseline(action) -> int|null`, `actionUnit(action) -> [singular, plural]`.
  - Exibição: `actionDisplay(action) -> {title, products: string[], more: int}`.
  - Situação de hoje: `actionLiveState(action, dashboardData) -> null | {kind:'count', now, was, gone} | {kind:'price', price, cost, margin}` e `actionLiveHtml(live, unit, closed) -> html`.
  - Atalho e repetição: `actionSource(action, period) -> null | {href, label}`, `previousHandled(action, list) -> action|null`.
  - Listas e contadores: `isActionOverdue(action, today) -> bool`, `actionStats(list, today) -> {open, inProgress, overdue, resolved30}`, `filterActions(list, tab, origin, today) -> action[]` (tab em `pendentes|atrasadas|concluidas|descartadas|todas`), `sortActions(list, today) -> action[]`, `pendingActionCount(list) -> int`.
  - Resumo: `actionDigest(list, today) -> {pending, overdue, stale, oldestDays}` e `actionsNoticeText(digest) -> string`.

- [ ] **Step 1: Write the failing tests**

`tests/test_actions_center_frontend.cjs`:

```js
/* Central de Ações: regras puras — título curto, situação de hoje, atalhos, contadores.
 * Runs the real web/assets/actions.js in a vm with the few app.js globals it reads. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

function loadActions() {
  const context = vm.createContext({
    console, URLSearchParams, Date, Math, Intl,
    APP: {company: 1, period: '2026-09'},
    ALERT_FILTERS: {estoque: 'ruptura', preco: 'abaixo-custo', custo: 'sem-custo'},
    routeHash: (page, period, params) => `#/${page}${period ? '/' + period : ''}${params && params.toString() ? '?' + params.toString() : ''}`,
    esc: (s) => String(s == null ? '' : s),
    num: (v) => String(v),
    money: (cents) => `R$ ${(cents / 100).toFixed(2)}`,
    pct: (v) => `${v.toFixed(2)}%`,
    icon: () => '',
  });
  vm.runInContext(read('web/assets/insights.js').match(/function sentenceCase\([\s\S]*?\n}/)[0], context);
  vm.runInContext(read('web/assets/actions.js'), context);
  return context;
}

const STOCK_TITLE = '170 produto(s) da curva A com estoque zerado ou negativo no Mobne: REFRIGERANTE COCA-COLA ORIGINAL RETORNAVEL PET 2L, RODO PLASTICO 40CM BRUBALAR, SUCO DE LARANJA INTEGRAL PRATS 900ML, UVA THOMPSON (BANDEJA), TAPIOCA DA TERRINHA 500G….';

test('a Resumo alert becomes a short title with a few product names', () => {
  const c = loadActions();
  const view = c.actionDisplay({alert_key: 'estoque', title: STOCK_TITLE, baseline_count: null});
  assert.equal(view.title, '170 produtos da curva A sem estoque');
  assert.deepEqual(Array.from(view.products), [
    'Refrigerante coca-cola original retornavel pet 2l', 'Rodo plastico 40cm brubalar', 'Suco de laranja integral prats 900ml']);
  assert.equal(view.more, 167);
  assert.equal(c.actionDisplay({alert_key: 'estoque', title: '1 produto(s) da curva A com estoque zerado ou negativo no Mobne: UVA.'}).title,
    '1 produto da curva A sem estoque');
  const manual = c.actionDisplay({alert_key: 'manual:abc', title: 'Negociar prazo com a distribuidora'});
  assert.equal(manual.title, 'Negociar prazo com a distribuidora');
  assert.equal(manual.products.length, 0);
});

test('the origin comes from the alert key', () => {
  const c = loadActions();
  assert.equal(c.actionOrigin('estoque'), 'estoque');
  assert.equal(c.actionOrigin('preco:8123'), 'preco');
  assert.equal(c.actionOrigin('mapa:baixo-giro'), 'mapa');
  assert.equal(c.actionOrigin('manual:k2'), 'manual');
});

test("today's state compares the newest month with the count at creation", () => {
  const c = loadActions();
  const data = {
    alerts: [{type: 'preco', count: 38, message: '38 produto(s) vendendo abaixo do custo atual do Mobne: X.'}],
    product_map: {products: [
      {classification: 'Baixo giro', cost: 10, revenue: 20},
      {classification: 'Baixo giro', cost: 0, revenue: 20},
      {classification: 'Estrela', cost: 5, revenue: 20}]},
    inventory: [{id: '8123', current_price: 349, current_cost: 259}],
  };
  let live = c.actionLiveState({alert_key: 'preco', title: '44 produto(s) vendendo…', baseline_count: 44}, data);
  assert.deepEqual([live.kind, live.now, live.was, live.gone], ['count', 38, 44, false]);
  live = c.actionLiveState({alert_key: 'estoque', title: STOCK_TITLE}, data);
  assert.deepEqual([live.now, live.was, live.gone], [0, 170, true]);
  live = c.actionLiveState({alert_key: 'mapa:baixo-giro', title: 'Avaliar retirada', baseline_count: 3}, data);
  assert.deepEqual([live.now, live.was], [1, 3]);
  live = c.actionLiveState({alert_key: 'preco:8123', title: 'Reajustar'}, data);
  assert.equal(live.kind, 'price');
  assert.equal(Math.round(live.margin), 26);
  assert.equal(c.actionLiveState({alert_key: 'manual:x', title: 'Ligar'}, data), null);
  assert.equal(c.actionLiveState({alert_key: 'estoque', title: STOCK_TITLE}, null), null);
});

test("today's state reads as a sentence, and a closed action is not told to conclude", () => {
  const c = loadActions();
  const unit = ['produto', 'produtos'];
  assert.match(c.actionLiveHtml({kind: 'count', now: 38, was: 44, gone: false}, unit, false), /No painel hoje: <b>38 produtos<\/b>.*−6 desde a criação/);
  assert.match(c.actionLiveHtml({kind: 'count', now: 0, was: 44, gone: true}, unit, false), /pode concluir/);
  assert.doesNotMatch(c.actionLiveHtml({kind: 'count', now: 0, was: 44, gone: true}, unit, true), /pode concluir/);
  assert.match(c.actionLiveHtml({kind: 'count', now: 1, was: null, gone: false}, unit, false), /<b>1 produto<\/b>/);
  assert.equal(c.actionLiveHtml(null, unit, false), '');
});

test('each action links back to where its alert came from', () => {
  const c = loadActions();
  assert.equal(c.actionSource({alert_key: 'estoque'}, '2026-09').href, '#/estoque/2026-09?filtro=ruptura');
  assert.equal(c.actionSource({alert_key: 'custo'}, '2026-09').href, '#/estoque/2026-09?filtro=sem-custo');
  assert.equal(c.actionSource({alert_key: 'mapa:gerador'}, '2026-09').href, '#/mapa/2026-09');
  assert.equal(c.actionSource({alert_key: 'preco:77'}, '2026-09').href, '#/produto/2026-09?id=77');
  assert.equal(c.actionSource({alert_key: 'integracao'}, '2026-09').href, '#/configuracoes/integracoes');
  assert.equal(c.actionSource({alert_key: 'manual:x'}, '2026-09'), null);
});

test('a repeated alert points to the last time it was handled', () => {
  const c = loadActions();
  const list = [
    {id: '3', alert_key: 'estoque', status: 'open', created_at: '2026-09-14T03:47:00+00:00'},
    {id: '2', alert_key: 'estoque', status: 'resolved', created_at: '2026-09-12T23:08:00+00:00', resolved_at: '2026-09-14T03:40:00+00:00'},
    {id: '1', alert_key: 'estoque', status: 'dismissed', created_at: '2026-09-01T10:00:00+00:00', resolved_at: '2026-09-02T10:00:00+00:00'},
    {id: '4', alert_key: 'preco', status: 'resolved', created_at: '2026-09-10T10:00:00+00:00', resolved_at: '2026-09-11T10:00:00+00:00'},
  ];
  assert.equal(c.previousHandled(list[0], list).id, '2');
  assert.equal(c.previousHandled(list[3], list), null);
});

test('counters, filters, order and the Resumo digest', () => {
  const c = loadActions();
  const today = '2026-09-14';
  const list = [
    {id: '1', alert_key: 'estoque', status: 'open', priority: 'high', due_date: '2026-09-13', created_at: '2026-09-12T15:00:00Z', updated_at: '2026-09-12T15:00:00Z'},
    {id: '2', alert_key: 'mapa:gerador', status: 'in_progress', priority: 'medium', due_date: '2026-09-20', created_at: '2026-09-01T15:00:00Z', updated_at: '2026-09-02T15:00:00Z'},
    {id: '3', alert_key: 'preco:7', status: 'resolved', priority: 'medium', created_at: '2026-09-01T15:00:00Z', updated_at: '2026-09-10T15:00:00Z', resolved_at: '2026-09-10T15:00:00Z'},
    {id: '4', alert_key: 'custo', status: 'resolved', priority: 'low', created_at: '2026-06-01T15:00:00Z', updated_at: '2026-07-01T15:00:00Z', resolved_at: '2026-07-01T15:00:00Z'},
  ];
  const s = c.actionStats(list, today);
  assert.deepEqual([s.open, s.inProgress, s.overdue, s.resolved30], [1, 1, 1, 1]);
  // Array.from: arrays built inside the vm have another realm's prototype, which deepEqual rejects.
  assert.deepEqual(Array.from(c.filterActions(list, 'atrasadas', '', today), (a) => a.id), ['1']);
  assert.deepEqual(Array.from(c.filterActions(list, 'concluidas', 'preco', today), (a) => a.id), ['3']);
  assert.deepEqual(Array.from(c.filterActions(list, 'todas', 'mapa', today), (a) => a.id), ['2']);
  assert.equal(c.pendingActionCount(list), 2);

  const d = c.actionDigest(list, today);
  assert.deepEqual([d.pending, d.overdue, d.stale, d.oldestDays], [2, 1, 1, 13]);
  assert.equal(c.actionsNoticeText(d), '1 ação atrasada · 1 parada há mais de 7 dias');
  assert.equal(c.actionsNoticeText(c.actionDigest([], today)), '');

  const order = Array.from(c.sortActions([
    {id: 'a', status: 'open', priority: 'high', created_at: '2026-09-10T15:00:00Z'},
    {id: 'b', status: 'open', priority: 'medium', due_date: '2026-09-01', created_at: '2026-09-05T15:00:00Z'},
    {id: 'c', status: 'open', priority: 'high', created_at: '2026-09-12T15:00:00Z'},
  ], today), (a) => a.id);
  assert.deepEqual(order, ['b', 'c', 'a']);
  assert.equal(c.actionAge('2026-09-14T15:00:00Z', today), 'hoje');
  assert.equal(c.actionAge('2026-09-13T15:00:00Z', today), 'ontem');
  assert.equal(c.actionAge('2026-09-12T15:00:00Z', today), 'há 2 dias');
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/test_actions_center_frontend.cjs`
Expected: FAIL com `c.actionDisplay is not a function`.

- [ ] **Step 3: Insert the pure rules into `web/assets/actions.js`**

Logo depois da linha `'use strict';`, acrescente:

```js
/* ---------------------------------------------------------------- Regras puras
 * No DOM here: everything below is exercised by tests/test_actions_center_frontend.cjs.
 * Globals from app.js/insights.js (esc, num, money, pct, icon, routeHash, ALERT_FILTERS,
 * sentenceCase) are only read inside functions — this file loads before app.js. */

const ACTION_ORIGINS = {
  estoque: {label: 'Estoque', icon: 'package'},
  preco: {label: 'Preço', icon: 'tag'},
  custo: {label: 'Custo', icon: 'dollar-sign'},
  integracao: {label: 'Integração', icon: 'link'},
  mapa: {label: 'Mapa de produtos', icon: 'map'},
  manual: {label: 'Manual', icon: 'clipboard-list'},
};
const ACTION_PRIORITY_LABELS = {high: 'Alta', medium: 'Média', low: 'Baixa'};
const ACTION_PRIORITY_RANK = {high: 0, medium: 1, low: 2};
const ACTION_TABS = [['pendentes', 'Pendentes'], ['concluidas', 'Concluídas'], ['descartadas', 'Descartadas'], ['todas', 'Todas']];
const ACTION_TAB_STATUSES = {
  pendentes: ['open', 'in_progress'],
  concluidas: ['resolved'],
  descartadas: ['dismissed'],
  todas: ['open', 'in_progress', 'resolved', 'dismissed'],
};
// Resumo alerts are stored with their whole message; the card shows this instead.
const ACTION_SHORT_TITLES = {
  estoque: (n) => `${n} ${n === 1 ? 'produto' : 'produtos'} da curva A sem estoque`,
  preco: (n) => `${n} ${n === 1 ? 'produto vendendo' : 'produtos vendendo'} abaixo do custo`,
  custo: (n) => `${n} ${n === 1 ? 'item vendido' : 'itens vendidos'} sem custo conhecido`,
};

const isPendingAction = (a) => a.status === 'open' || a.status === 'in_progress';

function isoDay(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function shiftIsoDay(iso, days) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return isoDay(d);
}

function daysBetween(a, b) {
  return Math.round((Date.parse(`${b}T12:00:00`) - Date.parse(`${a}T12:00:00`)) / 86400000);
}

function shortDay(iso) {
  return `${String(iso).slice(8, 10)}/${String(iso).slice(5, 7)}`;
}

function actionAge(timestamp, today) {
  const d = daysBetween(isoDay(new Date(timestamp)), today);
  return d <= 0 ? 'hoje' : d === 1 ? 'ontem' : `há ${d} dias`;
}

function actionOrigin(alertKey) {
  const base = String(alertKey || '').split(':')[0];
  return ACTION_ORIGINS[base] ? base : 'manual';
}

// Count at creation; actions older than baseline_count still start with it ("170 produto(s)…").
function actionBaseline(a) {
  if (a.baseline_count != null) return Number(a.baseline_count);
  const m = String(a.title || '').match(/^(\d+)\s/);
  return m ? Number(m[1]) : null;
}

function actionUnit(a) {
  return a.alert_key === 'custo' ? ['item', 'itens'] : ['produto', 'produtos'];
}

function actionDisplay(a) {
  const n = actionBaseline(a);
  const short = ACTION_SHORT_TITLES[a.alert_key];
  const text = String(a.title || '');
  const at = text.indexOf(': ');
  const names = short && at >= 0
    ? text.slice(at + 2).replace(/[….]+$/, '').split(', ').map((s) => sentenceCase(s.trim())).filter(Boolean)
    : [];
  const products = names.slice(0, 3);
  return {
    title: short && n != null ? short(n) : text,
    products,
    more: n != null && names.length ? Math.max(0, n - products.length) : 0,
  };
}

// How the problem behind an action looks in the newest synced month (null = nothing to compare).
function actionLiveState(a, data) {
  if (!data) return null;
  const key = String(a.alert_key || '');
  if (ACTION_SHORT_TITLES[key]) {
    const alert = (data.alerts || []).find((x) => x.type === key);
    const now = !alert ? 0 : alert.count != null ? alert.count : actionBaseline({title: alert.message});
    return {kind: 'count', now, was: actionBaseline(a), gone: !alert};
  }
  if (key === 'integracao') {
    return (data.alerts || []).some((x) => x.type === 'integracao') ? null : {kind: 'count', now: 0, was: null, gone: true};
  }
  if (key === 'mapa:gerador' || key === 'mapa:baixo-giro') {
    const group = key === 'mapa:gerador' ? 'Gerador de caixa' : 'Baixo giro';
    const products = (data.product_map ? data.product_map.products : data.products) || [];
    // Same exclusion as the Mapa de produtos: a zero cost fakes a 100% margin.
    const now = products.filter((p) => !(p.cost === 0 && p.revenue > 0) && p.classification === group).length;
    return {kind: 'count', now, was: actionBaseline(a), gone: now === 0};
  }
  const price = key.match(/^preco:(\d+)$/);
  if (price) {
    const item = (data.inventory || []).find((p) => String(p.id) === price[1]);
    if (!item || item.current_price == null || item.current_cost == null) return null;
    const margin = item.current_price ? (item.current_price - item.current_cost) / item.current_price * 100 : null;
    return {kind: 'price', price: item.current_price, cost: item.current_cost, margin};
  }
  return null;
}

function actionLiveHtml(live, unit, closed) {
  if (!live) return '';
  if (live.kind === 'price') {
    return `<p class="action-live">Hoje no Mobne: preço <b>${money(live.price)}</b>, custo <b>${money(live.cost)}</b>${
      live.margin == null ? '' : `, margem de <b>${pct(live.margin)}</b>`}</p>`;
  }
  if (live.gone) {
    return `<p class="action-live done">${icon('circle-check')} ${closed
      ? 'O alerta não voltou a aparecer no painel' : 'O alerta não aparece mais no painel: pode concluir'}</p>`;
  }
  const words = (n) => `${num(n)} ${n === 1 ? unit[0] : unit[1]}`;
  const diff = live.was == null ? null : live.now - live.was;
  const delta = diff == null ? ''
    : diff === 0 ? `<span class="action-delta flat">${closed ? 'igual a quando foi criada' : 'sem mudança desde a criação'}</span>`
      : `<span class="action-delta ${diff < 0 ? 'good' : 'bad'}">${diff > 0 ? '+' : '−'}${num(Math.abs(diff))} desde a criação</span>`;
  return `<p class="action-live">No painel hoje: <b>${words(live.now)}</b> ${delta}</p>`;
}

function actionSource(a, period) {
  const key = String(a.alert_key || '');
  if (ALERT_FILTERS[key]) {
    return {href: routeHash('estoque', period, new URLSearchParams({filtro: ALERT_FILTERS[key]})), label: 'Ver produtos'};
  }
  if (key === 'integracao') return {href: '#/configuracoes/integracoes', label: 'Abrir integrações'};
  if (key.startsWith('mapa:')) return {href: routeHash('mapa', period), label: 'Abrir no mapa'};
  const price = key.match(/^preco:(\d+)$/);
  if (price) return {href: `#/produto/${period}?id=${price[1]}`, label: 'Ver produto'};
  return null;
}

// The same alert already handled before this action was opened ("Já tratado antes").
function previousHandled(a, list) {
  return (list || [])
    .filter((x) => String(x.id) !== String(a.id) && x.alert_key === a.alert_key && !isPendingAction(x)
      && x.resolved_at && x.resolved_at <= a.created_at)
    .sort((x, y) => (x.resolved_at < y.resolved_at ? 1 : -1))[0] || null;
}

function isActionOverdue(a, today) {
  return isPendingAction(a) && !!a.due_date && a.due_date < today;
}

function actionStats(list, today) {
  const since = shiftIsoDay(today, -30);
  return {
    open: list.filter((a) => a.status === 'open').length,
    inProgress: list.filter((a) => a.status === 'in_progress').length,
    overdue: list.filter((a) => isActionOverdue(a, today)).length,
    resolved30: list.filter((a) => a.status === 'resolved' && a.resolved_at && isoDay(new Date(a.resolved_at)) >= since).length,
  };
}

function filterActions(list, tab, origin, today) {
  return (list || []).filter((a) => {
    if (origin && actionOrigin(a.alert_key) !== origin) return false;
    if (tab === 'atrasadas') return isActionOverdue(a, today);
    return (ACTION_TAB_STATUSES[tab] || ACTION_TAB_STATUSES.pendentes).includes(a.status);
  });
}

// Overdue first, then priority, then newest.
function sortActions(list, today) {
  return list.slice().sort((x, y) => (isActionOverdue(y, today) - isActionOverdue(x, today))
    || ((ACTION_PRIORITY_RANK[x.priority] ?? 1) - (ACTION_PRIORITY_RANK[y.priority] ?? 1))
    || (x.created_at < y.created_at ? 1 : x.created_at > y.created_at ? -1 : 0));
}

function pendingActionCount(list) {
  return (list || []).filter(isPendingAction).length;
}

// What deserves a line on the Resumo: overdue actions and pending ones untouched for a week.
function actionDigest(list, today) {
  const pending = (list || []).filter(isPendingAction);
  const staleSince = shiftIsoDay(today, -7);
  const oldest = pending.slice().sort((x, y) => (x.created_at < y.created_at ? -1 : 1))[0];
  return {
    pending: pending.length,
    overdue: pending.filter((a) => isActionOverdue(a, today)).length,
    stale: pending.filter((a) => !isActionOverdue(a, today) && isoDay(new Date(a.updated_at)) <= staleSince).length,
    oldestDays: oldest ? daysBetween(isoDay(new Date(oldest.created_at)), today) : 0,
  };
}

function actionsNoticeText(digest) {
  const bits = [];
  if (digest.overdue) bits.push(`${digest.overdue} ${digest.overdue === 1 ? 'ação atrasada' : 'ações atrasadas'}`);
  if (digest.stale) bits.push(`${digest.stale} ${digest.stale === 1 ? 'parada' : 'paradas'} há mais de 7 dias`);
  return bits.join(' · ');
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/test_actions_center_frontend.cjs tests/test_actions_frontend.cjs`
Expected: PASS. O teste antigo ainda passa porque o `renderAcoes` antigo continua no arquivo.

---

### Task 5: Página em cartões com contadores e filtros (itens 1, 2, 3, 4, 5, 7, 8)

**Files:**
- Modify: `web/assets/actions.js`: apagar do `const ACTION_STATUS_LABELS` até o fim do arquivo (o antigo `renderAcoes`/`onTransitionAction`) e colocar o código abaixo no lugar.
- Modify: `web/assets/style.css` (acrescentar ao fim)
- Test: `tests/test_actions_frontend.cjs`

**Interfaces:**
- Consumes: todas as regras da Task 4; `api`, `esc`, `num`, `icon`, `beginPage`, `routeHash`, `APP`.
- Produces:
  - `ACTIONS_STATE = {list, people, data, tab, origin}`.
  - `renderAcoes(data, token)`, `renderActionsBody()`, `actionCardHtml(a, today)`, `actionRowHtml(a, today)`.
  - `setActionsFilter(tab, origin)`, `transitionAction(a, toStatus, note, message) -> Promise` (lança erro em falha), `reloadActions(message) -> Promise`, `announceActions(message, isError)`.
  - O clique chama `openConcludeDrawer(a, trigger)`, `openDismissDrawer(a, trigger)`, `openEditDrawer(a, trigger)`, `openHistoryDrawer(a, trigger)` e `openNewActionDrawer(trigger)`, todas escritas na Task 6. **As Tasks 5 e 6 dividem um único checkpoint de commit, no fim da Task 6.**

- [ ] **Step 1: Update the source test**

Em `tests/test_actions_frontend.cjs`, troque o teste `'action rows expose status transitions'` inteiro por:

```js
test('action cards take, conclude and dismiss through the transition endpoint', () => {
  const actions = fs.readFileSync(path.join(root, 'web/assets/actions.js'), 'utf8');
  assert.match(actions, /data-action-take=/);
  assert.match(actions, /data-action-conclude=/);
  assert.match(actions, /data-action-dismiss=/);
  assert.match(actions, /data-actions-tab=/);
  assert.match(actions, /actions\/\$\{a\.id\}\/transition/);
  assert.match(actions, /id="actions-stats"/);
  assert.match(actions, /id="actions-origin"/);
  assert.doesNotMatch(actions, /📣/);
  // app.js's global click handler owns these attributes (web/assets/app.js, document click).
  assert.doesNotMatch(actions, /data-tab=|data-period=|data-nav=/);
  assert.doesNotMatch(actions, /style="/);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test tests/test_actions_frontend.cjs`
Expected: FAIL em `data-action-take`.

- [ ] **Step 3: Replace the old page code**

Em `web/assets/actions.js`, apague desde `const ACTION_STATUS_LABELS = {` até o fim do arquivo. Antes, rode `grep -rn "ACTION_STATUS_LABELS\|ACTION_TRANSITIONS\|onTransitionAction" web/assets tests` e confirme que só `actions.js` usa esses nomes. No lugar, cole:

```js
/* ---------------------------------------------------------------- Página */

const ACTIONS_STATE = {list: [], people: [], data: null, tab: 'pendentes', origin: ''};
const ACTION_GROUP_LABELS = {open: 'A fazer', in_progress: 'Em andamento', resolved: 'Concluídas', dismissed: 'Descartadas'};
const ACTION_EMPTY = {
  open: 'Nada esperando alguém assumir.',
  in_progress: 'Ninguém está cuidando de uma ação agora.',
  resolved: 'Nenhuma ação concluída.',
  dismissed: 'Nenhuma ação descartada.',
};

function actionsHeader() {
  return `<div class="actions-head">
    <div>
      <h1 class="page-title">Central de ações</h1>
      <p class="actions-lead">O que precisa ser resolvido, quem está cuidando e o que já foi feito.</p>
    </div>
    <button type="button" class="btn-primary" data-new-action>Nova ação</button>
  </div>`;
}

async function renderAcoes(data, token) {
  token = token || beginPage();
  const params = APP.routeParams || new URLSearchParams();
  const tab = params.get('situacao');
  const origin = params.get('origem');
  Object.assign(ACTIONS_STATE, {
    data: data || APP.dashboard || null,
    tab: tab === 'atrasadas' || ACTION_TAB_STATUSES[tab] ? tab : 'pendentes',
    origin: ACTION_ORIGINS[origin] ? origin : '',
  });
  const content = document.getElementById('content');
  content.innerHTML = `<div class="actions-page">${actionsHeader()}<div class="skeleton-block" aria-label="Carregando ações"></div></div>`;
  let list, people;
  try {
    [list, people] = await Promise.all([
      api(`/api/companies/${APP.company}/actions`),
      api(`/api/companies/${APP.company}/actions/assignees`).catch(() => []),
    ]);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;  // usuário já saiu desta rota
    content.innerHTML = `<div class="actions-page">${actionsHeader()}
      <div class="story-box">Não foi possível carregar as ações: ${esc(e.message)}</div></div>`;
    return;
  }
  if (!APP.pageState.isCurrent(token)) return;  // resposta obsoleta: descarta em silêncio
  Object.assign(ACTIONS_STATE, {list, people});
  content.innerHTML = `<div class="actions-page">
    ${actionsHeader()}
    <div class="actions-stats" id="actions-stats"></div>
    <div class="actions-toolbar" id="actions-toolbar"></div>
    <p class="actions-status" id="actions-status" role="status" aria-live="polite"></p>
    <div class="actions-body" id="actions-body"></div>
  </div>`;
  const page = content.querySelector('.actions-page');
  page.addEventListener('click', onActionsClick);
  page.addEventListener('change', (ev) => {
    if (ev.target.id === 'actions-origin') setActionsFilter(ACTIONS_STATE.tab, ev.target.value);
  });
  renderActionsBody();
}

function renderActionsBody() {
  const s = ACTIONS_STATE;
  const statsEl = document.getElementById('actions-stats');
  if (!statsEl) return;  // the partner already left the Central
  const today = isoDay(new Date());
  const stats = actionStats(s.list, today);
  const stat = (tab, n, label, sub, pressed, extra) => `<button type="button" class="action-stat${extra}" data-actions-tab="${tab}" aria-pressed="${pressed}">
      <span class="action-stat-value">${num(n)}</span><span class="action-stat-label">${label}</span><span class="action-stat-sub">${sub}</span></button>`;
  statsEl.innerHTML =
    stat('pendentes', stats.open, 'A fazer', 'ninguém assumiu ainda', false, '') +
    stat('pendentes', stats.inProgress, 'Em andamento', 'alguém está cuidando', false, '') +
    stat('atrasadas', stats.overdue, 'Atrasadas', stats.overdue ? 'passaram do prazo' : 'nenhuma no momento', s.tab === 'atrasadas', stats.overdue ? ' alert' : '') +
    stat('concluidas', stats.resolved30, 'Concluídas', 'nos últimos 30 dias', s.tab === 'concluidas', '');

  const present = new Set(s.list.map((a) => actionOrigin(a.alert_key)));
  document.getElementById('actions-toolbar').innerHTML = `
    <div class="actions-tabs-scroll"><div class="actions-tabs" role="group" aria-label="Situação">${ACTION_TABS.map(([key, label]) =>
      `<button type="button" class="actions-tab" data-actions-tab="${key}" aria-pressed="${s.tab === key}">${label}
        <span class="actions-tab-count">${num(filterActions(s.list, key, s.origin, today).length)}</span></button>`).join('')}</div></div>
    <label class="visually-hidden" for="actions-origin">Origem</label>
    <select id="actions-origin" class="actions-select">
      <option value="">Todas as origens</option>
      ${Object.entries(ACTION_ORIGINS).filter(([key]) => present.has(key) || key === s.origin)
        .map(([key, o]) => `<option value="${key}"${s.origin === key ? ' selected' : ''}>${esc(o.label)}</option>`).join('')}
    </select>
    ${s.tab === 'atrasadas' ? '<span class="actions-filter-note">Só atrasadas <button type="button" class="btn-link" data-actions-tab="pendentes">limpar filtro</button></span>' : ''}`;

  const visible = filterActions(s.list, s.tab, s.origin, today);
  const statuses = ACTION_TAB_STATUSES[s.tab] || ACTION_TAB_STATUSES.pendentes;
  document.getElementById('actions-body').innerHTML = !s.list.length
    ? `<div class="actions-empty">Nenhuma ação criada ainda. Use “Criar ação” nos pontos de atenção do Resumo executivo,
        em Preços e margens ou no Mapa de produtos, ou clique em “Nova ação”.</div>`
    : statuses.map((status) => {
      const items = sortActions(visible.filter((a) => a.status === status), today);
      const terminal = status === 'resolved' || status === 'dismissed';
      const body = !items.length ? `<div class="actions-empty">${ACTION_EMPTY[status]}</div>`
        : terminal ? `<div class="action-done-list">${items.map((a) => actionRowHtml(a, today)).join('')}</div>`
          : items.map((a) => actionCardHtml(a, today)).join('');
      return `<section class="action-group" aria-labelledby="action-group-${status}">
        <h2 class="action-group-title" id="action-group-${status}">${ACTION_GROUP_LABELS[status]}
          <span class="action-group-count">${num(items.length)}</span></h2>${body}</section>`;
    }).join('');
}

function actionCardHtml(a, today) {
  const s = ACTIONS_STATE;
  const origin = ACTION_ORIGINS[actionOrigin(a.alert_key)];
  const view = actionDisplay(a);
  const period = s.data ? s.data.period : APP.period;
  const source = actionSource(a, period);
  const previous = previousHandled(a, s.list);
  const late = isActionOverdue(a, today) ? daysBetween(a.due_date, today) : 0;
  const chips = view.products.length ? `<ul class="action-chips" aria-label="Alguns produtos">${
    view.products.map((p) => `<li title="${esc(p)}">${esc(p)}</li>`).join('')}${view.more ? `<li class="more">+${num(view.more)}</li>` : ''}</ul>` : '';
  const initial = (a.assignee_name || '?').trim().charAt(0).toUpperCase() || '?';
  const who = a.assignee
    ? `<span class="action-who"><span class="action-avatar" aria-hidden="true">${esc(initial)}</span>${esc(a.assignee_name || 'Responsável')}</span>`
    : '<span class="action-who none">Sem responsável</span>';
  const due = a.due_date
    ? `<span class="action-due${late ? ' late' : ''}">${icon('calendar')} Prazo ${shortDay(a.due_date)}${late ? ` · atrasada ${late} ${late === 1 ? 'dia' : 'dias'}` : ''}</span>`
    : '';
  const primary = a.status === 'open'
    ? `<button type="button" class="btn-primary" data-action-take="${esc(a.id)}">Assumir</button>`
    : `<button type="button" class="btn-primary" data-action-conclude="${esc(a.id)}">Concluir</button>`;
  const secondStep = a.status === 'open'
    ? `<button type="button" data-action-conclude="${esc(a.id)}">Concluir</button>`
    : `<button type="button" data-action-reopen="${esc(a.id)}">Voltar para “A fazer”</button>`;
  return `<article class="action-card" id="action-${esc(a.id)}">
    <div class="action-origin-icon" aria-hidden="true">${icon(origin.icon, {size: 20})}</div>
    <div class="action-main">
      <div class="action-top"><span>${esc(origin.label)}</span>
        <span class="action-priority ${esc(a.priority)}">${esc(ACTION_PRIORITY_LABELS[a.priority] || a.priority)}</span></div>
      <h3 class="action-title">${esc(view.title)}</h3>
      ${chips}
      ${actionLiveHtml(actionLiveState(a, s.data), actionUnit(a), false)}
      <p class="action-meta"><span>Criada ${actionAge(a.created_at, today)}</span>${who}${due}</p>
      ${previous ? `<p class="action-previous">${icon('link')} Já tratado antes: ${previous.status === 'resolved' ? 'concluída' : 'descartada'}
        em ${shortDay(isoDay(new Date(previous.resolved_at)))} ·
        <button type="button" class="btn-link" data-action-history="${esc(previous.id)}">ver histórico</button></p>` : ''}
    </div>
    <div class="action-side">
      ${primary}
      ${source ? `<a class="action-source" href="${esc(source.href)}">${esc(source.label)} →</a>` : ''}
      <details class="action-menu">
        <summary class="btn-secondary">Mais<span class="visually-hidden"> opções para esta ação</span></summary>
        <div class="action-menu-list">
          ${secondStep}
          <button type="button" data-action-edit="${esc(a.id)}">Responsável e prazo</button>
          <button type="button" data-action-history="${esc(a.id)}">Histórico</button>
          <button type="button" class="danger" data-action-dismiss="${esc(a.id)}">Descartar…</button>
        </div>
      </details>
    </div>
  </article>`;
}

function actionRowHtml(a, today) {
  const origin = ACTION_ORIGINS[actionOrigin(a.alert_key)];
  const resolved = a.status === 'resolved';
  const live = actionLiveState(a, ACTIONS_STATE.data);
  return `<div class="action-done" id="action-${esc(a.id)}">
    <div class="action-origin-icon small" aria-hidden="true">${icon(origin.icon)}</div>
    <div class="action-done-text">
      <p class="action-done-title">${esc(actionDisplay(a).title)}</p>
      <p class="action-done-note">${a.status_note
        ? `<b>${resolved ? 'O que foi feito:' : 'Motivo:'}</b> ${esc(a.status_note)}` : 'Sem anotação'}</p>
      ${resolved && live && live.kind === 'count' ? actionLiveHtml(live, actionUnit(a), true) : ''}
      <div class="action-result" data-action-result="${esc(a.id)}" hidden></div>
    </div>
    <div class="action-done-side">
      <span class="action-status ${esc(a.status)}">${resolved ? 'Concluída' : 'Descartada'} em ${a.resolved_at ? shortDay(isoDay(new Date(a.resolved_at))) : '—'}</span>
      <button type="button" class="btn-secondary" data-action-history="${esc(a.id)}">Histórico</button>
    </div>
  </div>`;
}

function onActionsClick(event) {
  const target = event.target.closest('button');
  if (!target) return;
  const d = target.dataset;
  if (d.actionsTab) return setActionsFilter(d.actionsTab, ACTIONS_STATE.origin);
  if ('newAction' in d) return openNewActionDrawer(target);
  const id = d.actionTake || d.actionConclude || d.actionReopen || d.actionEdit || d.actionHistory || d.actionDismiss;
  if (!id) return;
  const menu = target.closest('details');
  if (menu) menu.open = false;
  const a = ACTIONS_STATE.list.find((x) => String(x.id) === String(id));
  if (!a) return;
  if (d.actionTake) return quickTransition(a, 'in_progress', target, 'Você assumiu a ação. Ela está em “Em andamento”.');
  if (d.actionReopen) return quickTransition(a, 'open', target, 'A ação voltou para “A fazer”.');
  if (d.actionConclude) return openConcludeDrawer(a, target);
  if (d.actionDismiss) return openDismissDrawer(a, target);
  if (d.actionEdit) return openEditDrawer(a, target);
  if (d.actionHistory) return openHistoryDrawer(a, target);
}

// The filter lives in the URL (Voltar, favoritos) without refetching the list.
function setActionsFilter(tab, origin) {
  Object.assign(ACTIONS_STATE, {tab, origin});
  const params = new URLSearchParams();
  if (tab !== 'pendentes') params.set('situacao', tab);
  if (origin) params.set('origem', origin);
  APP.routeParams = params;
  history.replaceState(null, '', routeHash('acoes', null, params));
  if (APP.moduleRoutes) APP.moduleRoutes.remember(location.hash);
  renderActionsBody();
}

async function transitionAction(a, toStatus, note, message) {
  await api(`/api/companies/${APP.company}/actions/${a.id}/transition`, {
    method: 'POST',
    body: JSON.stringify({to_status: toStatus, note: note || ''}),
  });
  await reloadActions(message);
}

async function quickTransition(a, toStatus, trigger, message) {
  trigger.disabled = true;
  try {
    await transitionAction(a, toStatus, '', message);
  } catch (e) {
    if (trigger.isConnected) trigger.disabled = false;
    announceActions('Não foi possível atualizar a ação: ' + e.message, true);
  }
}

async function reloadActions(message) {
  ACTIONS_STATE.list = await api(`/api/companies/${APP.company}/actions`);
  renderActionsBody();
  if (message) announceActions(message, false);
}

function announceActions(message, isError) {
  const box = document.getElementById('actions-status');
  if (!box) return;
  box.textContent = message;
  box.classList.toggle('error', !!isError);
}
```

- [ ] **Step 4: Append the page styles to `web/assets/style.css`**

```css
/* ============================================================
   CENTRAL DE AÇÕES
   ============================================================ */
:root {
    --action-good-bg: #D5F5E3; --action-good-text: #17613A;
    --action-bad-bg: #FADBD8; --action-bad-text: #943126;
    --action-warn-bg: #FDEBD0; --action-warn-text: #7A4F00;
}
:root[data-theme="dark"] {
    --action-good-bg: rgba(74, 222, 128, 0.14); --action-good-text: #86EFAC;
    --action-bad-bg: rgba(248, 113, 113, 0.15); --action-bad-text: #FCA5A5;
    --action-warn-bg: rgba(251, 191, 36, 0.14); --action-warn-text: #FCD34D;
}
.actions-page { display: grid; gap: 18px; }
.actions-head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-end; gap: 12px 20px; }
.actions-lead { font-size: 14px; color: var(--text-muted); max-width: 60ch; }
.actions-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.action-stat { display: grid; min-height: 44px; padding: 12px 16px; border: 1px solid transparent; border-radius: 12px; background: white;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06); color: var(--dark); font: inherit; text-align: left; cursor: pointer; }
.action-stat:hover, .action-stat[aria-pressed="true"] { border-color: var(--amber); }
.action-stat[aria-pressed="true"] { background: var(--yellow-light); }
.action-stat-value { font-family: var(--font-display); font-size: 26px; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; }
.action-stat.alert .action-stat-value { color: var(--negative); }
.action-stat-label { font-size: 13px; font-weight: 600; }
.action-stat-sub { font-size: 12px; color: var(--text-muted); }
.actions-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 12px; }
.actions-tabs-scroll { max-width: 100%; overflow-x: auto; }
.actions-tabs { display: inline-flex; gap: 4px; padding: 4px; border-radius: 10px; background: var(--surface-muted); }
.actions-tab { min-height: 36px; padding: 0 12px; border: 0; border-radius: 7px; background: transparent; color: var(--text-muted);
    font: inherit; font-size: 14px; font-weight: 600; white-space: nowrap; cursor: pointer; }
.actions-tab[aria-pressed="true"] { background: white; color: var(--dark); box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08); }
.actions-tab-count { font-variant-numeric: tabular-nums; }
.actions-select { min-height: 40px; padding: 0 10px; border: 1px solid var(--border); border-radius: 8px; background: white; color: var(--dark); font: inherit; font-size: 14px; }
.actions-filter-note { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; color: var(--negative); }
.actions-filter-note .btn-link { margin: 0; }
.actions-status { font-size: 14px; font-weight: 600; color: var(--positive); }
.actions-status:empty { display: none; }
.actions-status.error { color: var(--negative); }
.actions-body { display: grid; gap: 22px; }
.actions-empty { padding: 16px; border: 1px dashed var(--border); border-radius: 12px; color: var(--text-muted); font-size: 14px; }
.action-group { display: grid; gap: 10px; }
.action-group-title { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 700; letter-spacing: 0.4px; text-transform: uppercase; color: var(--text-muted); }
.action-group-count { padding: 0 8px; border-radius: 10px; background: var(--surface-muted); color: var(--dark); font-size: 12px; }
.action-card { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; gap: 4px 16px; align-items: start; padding: 16px 18px;
    border-radius: 12px; background: white; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06); }
.action-origin-icon { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 10px; background: var(--yellow-light); color: var(--amber); }
.action-origin-icon.small { width: 32px; height: 32px; border-radius: 8px; background: var(--surface-muted); color: var(--text-muted); }
.action-main { display: grid; gap: 8px; min-width: 0; }
.action-top { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; font-size: 12px; font-weight: 600; color: var(--text-muted); }
.action-priority { display: inline-flex; align-items: center; gap: 5px; padding: 1px 8px; border-radius: 10px; font-size: 12px; font-weight: 700; }
.action-priority::before { content: ''; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.action-priority.high { background: var(--action-bad-bg); color: var(--action-bad-text); }
.action-priority.medium { background: var(--action-warn-bg); color: var(--action-warn-text); }
.action-priority.low { background: var(--surface-muted); color: var(--text-muted); }
.action-title { font-size: 16px; font-weight: 700; line-height: 1.35; color: var(--dark); }
.action-chips { list-style: none; display: flex; flex-wrap: wrap; gap: 6px; }
.action-chips li { max-width: 100%; padding: 2px 9px; border-radius: 12px; background: var(--surface-muted); font-size: 12.5px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.action-chips li.more { font-weight: 700; color: var(--text-muted); }
.action-live { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; padding: 7px 10px; border-radius: 8px; background: var(--surface-muted); font-size: 13px; }
.action-live b { font-variant-numeric: tabular-nums; }
.action-live.done { background: var(--action-good-bg); color: var(--action-good-text); font-weight: 600; }
.action-delta { padding: 0 7px; border-radius: 10px; font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums; }
.action-delta.good { background: var(--action-good-bg); color: var(--action-good-text); }
.action-delta.bad { background: var(--action-bad-bg); color: var(--action-bad-text); }
.action-delta.flat { color: var(--text-muted); }
.action-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 16px; font-size: 13px; color: var(--text-muted); }
.action-who, .action-due { display: inline-flex; align-items: center; gap: 6px; }
.action-who { color: var(--dark); font-weight: 600; }
.action-who.none { color: var(--text-muted); font-weight: 400; }
.action-avatar { display: inline-grid; place-items: center; width: 20px; height: 20px; border-radius: 50%; background: var(--yellow); color: #1A1A1A;
    font: 700 10px var(--font-display); }
.action-due.late { padding: 0 8px; border-radius: 10px; background: var(--action-bad-bg); color: var(--action-bad-text); font-weight: 700; }
.action-previous { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 12.5px; color: var(--text-muted); }
.action-previous .btn-link { margin: 0; }
.action-side { display: flex; flex-direction: column; align-items: stretch; gap: 8px; min-width: 150px; }
.action-source { display: inline-flex; align-items: center; justify-content: center; min-height: 36px; color: var(--link); font-size: 13.5px; font-weight: 700; }
.action-menu { position: relative; }
.action-menu summary { display: flex; align-items: center; justify-content: center; min-height: 40px; list-style: none; cursor: pointer; }
.action-menu summary::-webkit-details-marker { display: none; }
.action-menu-list { position: absolute; top: calc(100% + 4px); right: 0; z-index: 20; display: grid; min-width: 210px; padding: 6px;
    border: 1px solid var(--border); border-radius: 10px; background: white; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.16); }
.action-menu-list button { min-height: 40px; padding: 0 10px; border: 0; border-radius: 6px; background: transparent; color: var(--dark);
    font: inherit; font-size: 14px; text-align: left; cursor: pointer; }
.action-menu-list button:hover, .action-menu-list button:focus-visible { background: var(--surface-muted); }
.action-menu-list button.danger { color: var(--negative); }
.action-done-list { border-radius: 12px; background: white; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06); }
.action-done { display: grid; grid-template-columns: 32px minmax(0, 1fr) auto; gap: 4px 14px; align-items: center; padding: 12px 16px; border-top: 1px solid var(--divider); }
.action-done:first-child { border-top: 0; }
.action-done-text { display: grid; gap: 4px; min-width: 0; }
.action-done-title { font-size: 14.5px; font-weight: 600; }
.action-done-note, .action-result-line { font-size: 13px; color: var(--text-muted); }
.action-done-note b, .action-result-line b { color: var(--dark); font-weight: 600; }
.action-done-side { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.action-status { padding: 1px 8px; border-radius: 10px; font-size: 12px; font-weight: 700; white-space: nowrap; }
.action-status.resolved { background: var(--action-good-bg); color: var(--action-good-text); }
.action-status.dismissed { background: var(--surface-muted); color: var(--text-muted); }
@media (max-width: 760px) {
    .actions-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .action-card { grid-template-columns: 40px minmax(0, 1fr); }
    .action-side { grid-column: 1 / -1; flex-direction: row; flex-wrap: wrap; min-width: 0; }
    .action-side > * { flex: 1 1 auto; }
    .action-done { grid-template-columns: 32px minmax(0, 1fr); }
    .action-done-side { grid-column: 2; }
}
:root[data-theme="dark"] .action-stat,
:root[data-theme="dark"] .action-card,
:root[data-theme="dark"] .action-done-list,
:root[data-theme="dark"] .action-menu-list,
:root[data-theme="dark"] .actions-select,
:root[data-theme="dark"] .actions-tab[aria-pressed="true"] { background: #1E1E1E; color: var(--dark); box-shadow: 0 0 0 1px #2A2A2A; }
:root[data-theme="dark"] .action-stat[aria-pressed="true"] { background: var(--yellow-light); }
```

- [ ] **Step 5: Run the frontend tests**

Run: `node --test tests/test_actions_frontend.cjs tests/test_actions_center_frontend.cjs`
Expected: PASS. As gavetas ainda não existem; o clique nelas só funciona depois da Task 6.

---

### Task 6: Gavetas de concluir, descartar, histórico, responsável/prazo e nova ação; resultado (itens 4, 6, 9, 11, 12)

**Files:**
- Modify: `web/assets/actions.js` (acrescentar ao fim; uma linha em `renderActionsBody`)
- Modify: `web/assets/style.css` (acrescentar ao fim)
- Test: `tests/test_actions_center_frontend.cjs`

**Interfaces:**
- Consumes: `openDrawer({title, trigger, body}) -> {dialog, close(), setBody(html)}` de `web/assets/finance-forms.js:325`; `transitionAction`, `reloadActions`, `setActionsFilter`, `ACTIONS_STATE` (Task 5); `PATCH` e `GET .../result` (Tasks 2 e 3).
- Produces:
  - Gavetas: `openConcludeDrawer(a, trigger)`, `openDismissDrawer(a, trigger)`, `openHistoryDrawer(a, trigger)`, `openEditDrawer(a, trigger)`, `openNewActionDrawer(trigger)`.
  - Montagem de payload e texto: `dismissNote(reason, note) -> string`, `actionEditPayload(fields) -> {assignee, due_date, priority}`, `manualActionPayload(fields, stamp) -> body` (lança `Error` sem título), `actionEventText(event) -> string`.
  - Resultado: `actionResultHtml(result) -> html` e `loadActionResults()`.

- [ ] **Step 1: Write the failing tests**

Acrescente ao fim de `tests/test_actions_center_frontend.cjs`:

```js
test('forms send what the backend expects', () => {
  const c = loadActions();
  const field = (value) => ({value});
  assert.deepEqual({...c.actionEditPayload({assignee: field(''), due_date: field('2026-09-20'), priority: field('high')})},
    {assignee: null, due_date: '2026-09-20', priority: 'high'});
  const manual = c.manualActionPayload({title: field('  Negociar prazo '), priority: field('medium'),
    assignee: field('4611686018427387905'), due_date: field('')}, 1700000000000);
  assert.equal(manual.alert_key, 'manual:' + (1700000000000).toString(36));
  assert.equal(manual.title, 'Negociar prazo');
  assert.equal(manual.assignee, '4611686018427387905');  // big ids travel as text, never Number()
  assert.equal(manual.due_date, null);
  assert.throws(() => c.manualActionPayload({title: field(' '), priority: field('low'), assignee: field(''), due_date: field('')}, 1));
  assert.equal(c.dismissNote('Não vale o esforço agora', '  volta em outubro '), 'Não vale o esforço agora. volta em outubro');
  assert.equal(c.dismissNote('Outro motivo', ''), 'Outro motivo');
});

test('history events read as sentences', () => {
  const c = loadActions();
  assert.equal(c.actionEventText({event_type: 'created'}), 'Ação criada');
  assert.equal(c.actionEventText({event_type: 'status_changed', to_status: 'in_progress'}), 'Assumida');
  assert.equal(c.actionEventText({event_type: 'status_changed', to_status: 'resolved'}), 'Concluída');
  assert.equal(c.actionEventText({event_type: 'updated', note: 'Prazo: 20/09/2026'}), 'Prazo: 20/09/2026');
});

test('a price action shows its measured result', () => {
  const c = loadActions();
  const before = {margin: 20, revenue: 2000}, after = {margin: 33.33, revenue: 2400};
  assert.match(c.actionResultHtml({kind: 'measured', days: 30, total: 30, ready_on: '2026-09-20', before, after}),
    /margem de <b>20\.00%<\/b> para <b>33\.33%<\/b>/);
  assert.match(c.actionResultHtml({kind: 'measuring', days: 5, total: 30, ready_on: '2026-09-20', before, after}),
    /parcial: 5 de 30 dias/);
  assert.match(c.actionResultHtml({kind: 'measuring', days: 0, total: 30, ready_on: '2026-09-20', before, after: null}),
    /pronto em 20\/09/);
  assert.equal(c.actionResultHtml({kind: 'none'}), '');
  assert.equal(c.actionResultHtml({kind: 'pending'}), '');
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/test_actions_center_frontend.cjs`
Expected: FAIL com `c.actionEditPayload is not a function`.

- [ ] **Step 3: Append the drawers to `web/assets/actions.js`**

```js
/* ---------------------------------------------------------------- Gavetas */

const ACTION_DISMISS_REASONS = ['Não é um problema de verdade', 'Já foi resolvido por outro caminho', 'Não vale o esforço agora', 'Outro motivo'];
const ACTION_EVENT_TEXT = {open: 'Voltou para “A fazer”', in_progress: 'Assumida', resolved: 'Concluída', dismissed: 'Descartada'};

function actionEventText(e) {
  if (e.event_type === 'created') return 'Ação criada';
  if (e.event_type === 'updated') return e.note || 'Ação atualizada';
  return ACTION_EVENT_TEXT[e.to_status] || 'Situação alterada';
}

function actionWhen(timestamp) {
  return new Date(timestamp).toLocaleString('pt-BR', {dateStyle: 'short', timeStyle: 'short'});
}

function dismissNote(reason, note) {
  return [reason, String(note || '').trim()].filter(Boolean).join('. ').slice(0, 2000);
}

function actionEditPayload(fields) {
  return {assignee: fields.assignee.value || null, due_date: fields.due_date.value || null, priority: fields.priority.value};
}

function manualActionPayload(fields, stamp) {
  const title = fields.title.value.trim();
  if (!title) throw new Error('Escreva o que precisa ser feito.');
  return {
    alert_key: `manual:${stamp.toString(36)}`, alert_version: '1', title,
    priority: fields.priority.value, assignee: fields.assignee.value || null, due_date: fields.due_date.value || null,
  };
}

function priorityOptions(selected) {
  return Object.entries(ACTION_PRIORITY_LABELS)
    .map(([key, label]) => `<option value="${key}"${key === selected ? ' selected' : ''}>${label}</option>`).join('');
}

function peopleOptions(selected) {
  return '<option value="">Sem responsável</option>' + (ACTIONS_STATE.people || [])
    .map((p) => `<option value="${esc(p.id)}"${String(p.id) === String(selected) ? ' selected' : ''}>${esc(p.name || 'Sem nome')}</option>`).join('');
}

function drawerButtons(cancelLabel, submitLabel, submitClass) {
  return `<p class="form-error" role="alert" data-form-error></p>
    <div class="drawer-actions"><button type="button" class="btn-secondary" data-drawer-close>${cancelLabel}</button>
      <button type="submit" class="${submitClass}">${submitLabel}</button></div>`;
}

// Submit runs `run(form)`; success closes the drawer, a failure stays visible inside it.
function bindDrawerForm(drawer, run) {
  const form = drawer.dialog.querySelector('form');
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const button = form.querySelector('[type="submit"]');
    const error = form.querySelector('[data-form-error]');
    button.disabled = true;
    error.textContent = '';
    try {
      await run(form);
      drawer.close();
    } catch (e) {
      button.disabled = false;
      error.textContent = e.message;
    }
  });
}

function openConcludeDrawer(a, trigger) {
  const drawer = openDrawer({title: 'Concluir ação', trigger, body: `
    <form class="drawer-form action-form">
      <p class="action-form-lead">${esc(actionDisplay(a).title)}</p>
      ${actionLiveHtml(actionLiveState(a, ACTIONS_STATE.data), actionUnit(a), false)}
      <label for="action-note">O que foi feito?</label>
      <textarea id="action-note" name="note" class="login-input" rows="4" maxlength="2000"
        placeholder="Ex.: pedido feito à distribuidora, chega na quinta"></textarea>
      <p class="field-help">Opcional. Fica no histórico para os outros sócios.</p>
      ${drawerButtons('Cancelar', 'Concluir ação', 'btn-primary')}
    </form>`});
  bindDrawerForm(drawer, (form) =>
    transitionAction(a, 'resolved', form.elements.note.value.trim(), 'Ação concluída. Ela está em “Concluídas”.'));
}

function openDismissDrawer(a, trigger) {
  const drawer = openDrawer({title: 'Descartar ação?', trigger, body: `
    <form class="drawer-form action-form">
      <p class="action-form-lead">${esc(actionDisplay(a).title)}</p>
      <p class="field-help">A ação sai das pendentes. Se o alerta voltar, dá para criar outra.</p>
      <label for="action-reason">Motivo</label>
      <select id="action-reason" name="reason" class="login-input">${ACTION_DISMISS_REASONS.map((r) => `<option>${esc(r)}</option>`).join('')}</select>
      <label for="action-dismiss-note">Anotação</label>
      <textarea id="action-dismiss-note" name="note" class="login-input" rows="3" maxlength="1800" placeholder="Opcional"></textarea>
      ${drawerButtons('Manter ação', 'Descartar', 'btn-secondary action-danger')}
    </form>`});
  bindDrawerForm(drawer, (form) =>
    transitionAction(a, 'dismissed', dismissNote(form.elements.reason.value, form.elements.note.value), 'Ação descartada.'));
}

async function openHistoryDrawer(a, trigger) {
  const lead = `<p class="action-form-lead">${esc(actionDisplay(a).title)}</p>`;
  const drawer = openDrawer({title: 'Histórico da ação', trigger, body: `${lead}<div class="skeleton-block" aria-label="Carregando histórico"></div>`});
  try {
    const events = await api(`/api/companies/${APP.company}/actions/${a.id}/events`);
    drawer.setBody(`${lead}<ol class="action-timeline">${events.map((e) => `<li>
      <p>${esc(actionEventText(e))}${e.created_by_name ? ` <span class="action-timeline-who">· ${esc(e.created_by_name)}</span>` : ''}</p>
      ${e.event_type !== 'updated' && e.note ? `<p class="action-timeline-note">“${esc(e.note)}”</p>` : ''}
      <p class="action-timeline-when">${esc(actionWhen(e.created_at))}</p></li>`).join('')}</ol>`);
  } catch (e) {
    drawer.setBody(`${lead}<p class="form-error" role="alert">Não foi possível carregar o histórico: ${esc(e.message)}</p>`);
  }
}

function openEditDrawer(a, trigger) {
  const drawer = openDrawer({title: 'Responsável e prazo', trigger, body: `
    <form class="drawer-form action-form">
      <p class="action-form-lead">${esc(actionDisplay(a).title)}</p>
      <label for="action-assignee">Responsável</label>
      <select id="action-assignee" name="assignee" class="login-input">${peopleOptions(a.assignee)}</select>
      <label for="action-due">Prazo</label>
      <input id="action-due" name="due_date" type="date" class="login-input" value="${esc(a.due_date || '')}">
      <label for="action-priority">Prioridade</label>
      <select id="action-priority" name="priority" class="login-input">${priorityOptions(a.priority)}</select>
      ${drawerButtons('Cancelar', 'Salvar', 'btn-primary')}
    </form>`});
  bindDrawerForm(drawer, async (form) => {
    await api(`/api/companies/${APP.company}/actions/${a.id}`, {method: 'PATCH', body: JSON.stringify(actionEditPayload(form.elements))});
    await reloadActions('Ação atualizada.');
  });
}

function openNewActionDrawer(trigger) {
  const drawer = openDrawer({title: 'Nova ação', trigger, body: `
    <form class="drawer-form action-form">
      <label for="new-action-title">O que precisa ser feito?</label>
      <input id="new-action-title" name="title" class="login-input" required maxlength="240" placeholder="Ex.: Negociar prazo com a distribuidora">
      <label for="new-action-priority">Prioridade</label>
      <select id="new-action-priority" name="priority" class="login-input">${priorityOptions('medium')}</select>
      <label for="new-action-assignee">Responsável</label>
      <select id="new-action-assignee" name="assignee" class="login-input">${peopleOptions(null)}</select>
      <label for="new-action-due">Prazo</label>
      <input id="new-action-due" name="due_date" type="date" class="login-input">
      ${drawerButtons('Cancelar', 'Criar ação', 'btn-primary')}
    </form>`});
  bindDrawerForm(drawer, async (form) => {
    await api(`/api/companies/${APP.company}/actions`, {method: 'POST', body: JSON.stringify(manualActionPayload(form.elements, Date.now()))});
    setActionsFilter('pendentes', '');
    await reloadActions('Ação criada.');
  });
}

/* ---------------------------------------------------------------- Resultado depois de concluir */

function actionResultHtml(r) {
  if (!r || r.kind === 'none' || r.kind === 'pending') return '';
  const margin = (m) => (m == null ? 'sem custo' : pct(m));
  if (!r.after) return `<p class="action-result-line">Resultado: começa a medir amanhã, pronto em ${shortDay(r.ready_on)}.</p>`;
  const partial = r.kind === 'measuring' ? ` (parcial: ${num(r.days)} de ${num(r.total)} dias, completo em ${shortDay(r.ready_on)})` : '';
  return `<p class="action-result-line">Resultado${partial}: margem de <b>${margin(r.before.margin)}</b> para <b>${margin(r.after.margin)}</b>;
    faturamento do produto de <b>${money(r.before.revenue)}</b> nos 30 dias antes para <b>${money(r.after.revenue)}</b> depois.</p>`;
}

async function loadActionResults() {
  for (const slot of document.querySelectorAll('[data-action-result]')) {
    const a = ACTIONS_STATE.list.find((x) => String(x.id) === slot.dataset.actionResult);
    if (!a || a.status !== 'resolved' || !/^preco:\d+$/.test(a.alert_key)) continue;
    try {
      const html = actionResultHtml(await api(`/api/companies/${APP.company}/actions/${a.id}/result`));
      if (!slot.isConnected) continue;
      slot.innerHTML = html;
      slot.hidden = !html;
    } catch (e) {
      /* the row simply stays without a result line */
    }
  }
}
```

Em `renderActionsBody()`, última linha da função, logo depois do `document.getElementById('actions-body').innerHTML = ...;`, acrescente:

```js
  loadActionResults();
```

- [ ] **Step 4: Append the drawer styles to `web/assets/style.css`**

```css
.action-form { display: grid; gap: 6px; }
.action-form label { margin-top: 8px; font-size: 13px; font-weight: 700; }
.action-form .field-help { margin: 0; }
.action-form-lead { margin-bottom: 6px; font-weight: 600; }
.action-danger { color: var(--negative); }
.action-timeline { list-style: none; display: grid; margin-top: 8px; }
.action-timeline li { position: relative; padding: 0 0 14px 22px; font-size: 14px; }
.action-timeline li::before { content: ''; position: absolute; top: 6px; left: 4px; width: 9px; height: 9px; border-radius: 50%; background: var(--yellow); }
.action-timeline li::after { content: ''; position: absolute; top: 18px; bottom: 0; left: 8px; width: 1px; background: var(--border); }
.action-timeline li:last-child::after { display: none; }
.action-timeline-note { font-size: 13px; font-style: italic; color: var(--text-muted); }
.action-timeline-when, .action-timeline-who { font-size: 12px; color: var(--text-muted); }
```

- [ ] **Step 5: Run the whole frontend suite**

Run: `node --test tests/*.cjs`
Expected: PASS.

- [ ] **Step 6: Verify in the browser**

1. Suba o servidor de desenvolvimento com `preview_start` e a configuração existente em `.claude/launch.json`.
2. Entre e abra `#/acoes`. Com dados locais sem ações, crie uma pelo botão "Nova ação".
3. Confira, com `read_page` e um `screenshot` no fim:
   - contadores e abas mudam a lista, e a URL recebe `?situacao=`;
   - "Assumir" leva o cartão para "Em andamento", com o nome do responsável;
   - "Concluir" abre a gaveta; com anotação, a ação vai para "Concluídas" com "O que foi feito: …";
   - "Descartar…" pede motivo antes de descartar;
   - "Histórico" mostra os eventos com nome e data;
   - "Responsável e prazo" com data passada mostra "atrasada N dias" e soma em "Atrasadas";
   - no tema escuro (botão do rodapé do menu), cartões, menu "Mais" e gavetas continuam legíveis;
   - com a largura em 400px, os cartões ficam em uma coluna, sem rolagem horizontal.
4. Rode `read_console_messages` com `onlyErrors` e confirme que não há erros.

- [ ] **Step 7: Checkpoint de commit (página)**

Mostre `git status` e peça aprovação. Com o "pode":

```bash
git add web/assets/actions.js web/assets/style.css tests/test_actions_frontend.cjs tests/test_actions_center_frontend.cjs
git commit -m "feat: rebuild the Central de Ações as cards with filters, drawers and history"
```

---

### Task 7: Alertas e grupos do Mapa criam ações com prioridade e contagem (itens 3 e 5)

**Files:**
- Modify: `web/assets/app.js:969-970` (botão "Criar ação" em `alertsBlock`) e `web/assets/app.js:1009-1023` (`onCreateActionFromAlert`)
- Modify: `web/assets/insights.js:753-754` (`onGroupAction`)
- Test: `tests/test_actions_frontend.cjs`

**Interfaces:**
- Consumes: `count` e `severity` de cada alerta (Task 2); `baseline_count` e `priority` no `POST` (Task 2); `refreshActionsBadge()` (definida na Task 8; os ramos que a chamam só ligam na Task 8 e ficam protegidos por `typeof`, como `disposeEcharts` em `app.js:735`).

- [ ] **Step 1: Write the failing test**

Em `tests/test_actions_frontend.cjs`, dentro do teste `'alerts can be turned into actions'`, acrescente antes do `});`:

```js
  assert.match(app, /data-action-priority="\$\{a\.severity === 'high' \? 'high' : 'medium'\}"/);
  assert.match(app, /data-action-baseline=/);
  assert.match(app, /baseline_count: baselineCount/);
  const insights = fs.readFileSync(path.join(root, 'web/assets/insights.js'), 'utf8');
  assert.match(insights, /baseline_count: info\.count/);
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test tests/test_actions_frontend.cjs`
Expected: FAIL em `data-action-priority`.

- [ ] **Step 3: Send priority and count from the Resumo alerts**

Em `alertsBlock` (`web/assets/app.js`), troque a linha do botão:

```js
          <button type="button" class="btn-secondary" data-create-action="${esc(a.type)}" data-action-title="${esc(a.message)}">Criar ação</button></div>
```

por:

```js
          <button type="button" class="btn-secondary" data-create-action="${esc(a.type)}" data-action-title="${esc(a.message)}"
            data-action-priority="${a.severity === 'high' ? 'high' : 'medium'}" data-action-baseline="${a.count == null ? '' : esc(a.count)}">Criar ação</button></div>
```

Em `onCreateActionFromAlert`, troque:

```js
  const title = btn.dataset.actionTitle;
```

por:

```js
  const title = btn.dataset.actionTitle;
  const baselineCount = btn.dataset.actionBaseline ? Number(btn.dataset.actionBaseline) : null;
```

E troque:

```js
      body: JSON.stringify({alert_key: alertKey, alert_version: title, title}),
    });
    btn.textContent = 'Ação criada ✓';
    loadAlertActions();  // the new action now counts on its alert
```

por:

```js
      body: JSON.stringify({alert_key: alertKey, alert_version: title, title: title.slice(0, 240),
        priority: btn.dataset.actionPriority || 'medium', baseline_count: baselineCount}),
    });
    btn.textContent = 'Ação criada ✓';
    loadAlertActions();  // the new action now counts on its alert
    if (typeof refreshActionsBadge === 'function') refreshActionsBadge();
```

- [ ] **Step 4: Send the group count from the Mapa**

Em `onGroupAction` (`web/assets/insights.js`), troque:

```js
      alert_key: btn.dataset.groupAction, alert_version: String(s.version), title: info.title.slice(0, 240), priority: 'medium'})});
    btn.textContent = 'Ação criada ✓';
    loadGroupActions();
```

por:

```js
      alert_key: btn.dataset.groupAction, alert_version: String(s.version), title: info.title.slice(0, 240), priority: 'medium',
      baseline_count: info.count})});
    btn.textContent = 'Ação criada ✓';
    loadGroupActions();
    if (typeof refreshActionsBadge === 'function') refreshActionsBadge();
```

Em `onPriceAction` (`web/assets/insights.js`), depois de `loadPriceActions();`, acrescente:

```js
    if (typeof refreshActionsBadge === 'function') refreshActionsBadge();
```

- [ ] **Step 5: Run the frontend suite**

Run: `node --test tests/*.cjs`
Expected: PASS.

---

### Task 8: Sem seletor de mês, contador no menu e aviso no Resumo (itens 10, 13 e 14)

**Files:**
- Modify: `web/assets/navigation.js:39-43` (`periodModeForPage`)
- Modify: `web/assets/app.js`: `renderPage` (linha ~747), `onAuthenticated` (linha ~389), `renderResumo` (linhas ~1348 e ~1388)
- Modify: `web/assets/actions.js` (acrescentar ao fim; uma linha em `renderAcoes` e uma em `reloadActions`)
- Modify: `web/index.html:85` (item do menu) e as versões dos assets
- Modify: `web/assets/style.css` (acrescentar ao fim)
- Test: `tests/test_actions_center_frontend.cjs`

**Interfaces:**
- Consumes: `pendingActionCount`, `actionDigest`, `actionsNoticeText`, `isoDay` (Task 4).
- Produces:
  - `updateActionsBadge(list)`, `refreshActionsBadge()`, `loadResumoActionsNotice()`;
  - `latestSalesPeriod()` em `app.js`;
  - `periodModeForPage('acoes') === 'none'`.

- [ ] **Step 1: Write the failing tests**

Acrescente ao fim de `tests/test_actions_center_frontend.cjs`:

```js
test('the Central de Ações has no month selector and reads the newest month', () => {
  const nav = vm.createContext({console, URLSearchParams, window: {}, sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}}});
  // If navigation.js ever needs more globals at load, copy the stubs from tests/test_navigation_frontend.cjs.
  vm.runInContext(read('web/assets/navigation.js'), nav);
  assert.equal(nav.periodModeForPage('acoes'), 'none');
  assert.equal(nav.periodModeForPage('resumo'), 'sales');
  const app = read('web/assets/app.js');
  assert.match(app, /const period = page === 'acoes' \? latestSalesPeriod\(\) : APP\.period;/);
});

test('the menu counts pending actions and the Resumo points to the overdue ones', () => {
  const html = read('web/index.html');
  assert.match(html, /data-page="acoes"[^\n]*data-actions-badge/);
  const app = read('web/assets/app.js');
  assert.match(app, /<p class="actions-notice" id="resumo-actions" hidden><\/p>/);
  assert.match(app, /loadResumoActionsNotice\(\);/);
  assert.match(app, /refreshActionsBadge\(\);/);
  const actions = read('web/assets/actions.js');
  assert.match(actions, /function updateActionsBadge\(/);
  assert.match(actions, /situacao: 'atrasadas'/);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/test_actions_center_frontend.cjs`
Expected: FAIL. `periodModeForPage('acoes')` ainda devolve `'sales'`.

- [ ] **Step 3: Remove the month selector from the Central**

Em `web/assets/navigation.js`, troque:

```js
function periodModeForPage(page) {
  if (MODULE_PAGES.analises.includes(page)) return 'sales';
```

por:

```js
// Pages of a module that do not depend on the chosen month (their data is always current).
var PERIODLESS_PAGES = ['acoes'];

function periodModeForPage(page) {
  if (PERIODLESS_PAGES.includes(page)) return 'none';
  if (MODULE_PAGES.analises.includes(page)) return 'sales';
```

Em `web/assets/app.js`, `renderPage()`, troque:

```js
  const company = APP.company, period = APP.period, page = APP.page;
```

por:

```js
  const company = APP.company, page = APP.page;
  // The Central de Ações has no month selector: "no painel hoje" always reads the newest synced month.
  const period = page === 'acoes' ? latestSalesPeriod() : APP.period;
```

e acrescente, logo acima de `async function renderPage() {`:

```js
function latestSalesPeriod() {
  const withData = (APP.periods || []).find((p) => p.documents > 0);  // APP.periods is newest first
  return withData ? withData.period : APP.period;
}
```

- [ ] **Step 4: Add the menu counter**

Em `web/index.html`, linha 85, troque o final `Central de ações</a>` por:

```html
 Central de ações<span class="nav-badge" data-actions-badge hidden></span></a>
```

Acrescente ao fim de `web/assets/actions.js`:

```js
/* ---------------------------------------------------------------- Menu e Resumo */

function updateActionsBadge(list) {
  const badge = document.querySelector('[data-actions-badge]');
  if (!badge) return;
  const n = pendingActionCount(list);
  badge.innerHTML = `${n > 99 ? '99+' : n}<span class="visually-hidden"> ${n === 1 ? 'ação pendente' : 'ações pendentes'}</span>`;
  badge.hidden = n === 0;
}

async function refreshActionsBadge() {
  if (APP.company == null) return;
  try {
    updateActionsBadge(await api(`/api/companies/${APP.company}/actions`));
  } catch (e) {
    /* the badge keeps its last value */
  }
}

// One line under the Resumo welcome, only when something is overdue or stuck for a week.
async function loadResumoActionsNotice() {
  const box = document.getElementById('resumo-actions');
  if (!box) return;
  let list;
  try {
    list = await api(`/api/companies/${APP.company}/actions`);
  } catch (e) {
    return;
  }
  if (!box.isConnected) return;
  updateActionsBadge(list);
  const digest = actionDigest(list, isoDay(new Date()));
  const text = actionsNoticeText(digest);
  if (!text) return;
  const params = new URLSearchParams(digest.overdue ? {situacao: 'atrasadas'} : {});
  box.innerHTML = `${icon('triangle-alert')} ${esc(text)} <a href="${routeHash('acoes', null, params)}">Ver ações →</a>`;
  box.hidden = false;
}
```

Em `renderAcoes`, logo depois de `Object.assign(ACTIONS_STATE, {list, people});`, acrescente:

```js
  updateActionsBadge(list);
```

Em `reloadActions`, logo depois de `ACTIONS_STATE.list = await api(...);`, acrescente:

```js
  updateActionsBadge(ACTIONS_STATE.list);
```

Em `web/assets/app.js`, `onAuthenticated`, logo depois de `await refreshStatus();`, acrescente:

```js
  refreshActionsBadge();
```

- [ ] **Step 5: Add the Resumo notice**

Em `renderResumo` (`web/assets/app.js`), troque:

```js
    ${welcomeBlock(data)}

    <div class="kpi-grid kpi-grid-4">
```

por:

```js
    ${welcomeBlock(data)}
    <p class="actions-notice" id="resumo-actions" hidden></p>

    <div class="kpi-grid kpi-grid-4">
```

E, logo depois de `loadAlertActions();` no fim de `renderResumo`, acrescente:

```js
  loadResumoActionsNotice();
```

- [ ] **Step 6: Styles and asset versions**

Acrescente ao fim de `web/assets/style.css`:

```css
.nav-badge { min-width: 22px; margin-left: auto; padding: 0 7px; border-radius: 10px; background: var(--yellow); color: #1A1A1A;
    font-size: 12px; font-weight: 700; line-height: 20px; text-align: center; font-variant-numeric: tabular-nums; }
.nav-item.active .nav-badge { background: #1A1A1A; color: var(--yellow); }
.actions-notice { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; margin: -4px 0 16px; padding: 8px 12px; border-radius: 10px;
    background: var(--action-bad-bg); color: var(--action-bad-text); font-size: 14px; font-weight: 600; }
.actions-notice a { color: inherit; }
```

Se o `.nav-item` não for `display: flex` (confira em `style.css:220`), troque `margin-left: auto` por `float: right`.

Em `web/index.html`, atualize as versões:
- `assets/style.css`, `assets/insights.js` e `assets/app.js`: `?v=20260914-m2` → `?v=20260914-ac1`;
- `assets/actions.js`: `?v=20260912-a4` → `?v=20260914-ac1`;
- `assets/navigation.js`: `?v=20260913-c3` → `?v=20260914-ac1`.

- [ ] **Step 7: Run everything**

Run: `.venv/bin/python -m pytest -q tests` e `node --test tests/*.cjs`
Expected: PASS nos dois.

- [ ] **Step 8: Verify in the browser**

Com o servidor de desenvolvimento aberto (`preview_start` e `.claude/launch.json`), recarregue com `?fresh=1` para escapar do cache e confira:
1. `#/acoes`: o seletor de mês some do topo e o link de status de sincronização continua.
2. **Situação de hoje:** "No painel hoje: N produtos" usa o mês mais recente, mesmo que outro mês estivesse escolhido antes de abrir a Central.
3. **Contador no menu:** "Central de ações" mostra o número de pendentes. Ele diminui ao concluir uma ação e some com zero.
4. **Resumo executivo:** com uma ação de prazo vencido, aparece a linha "1 ação atrasada · Ver ações →", e o link abre a Central já filtrada em "Atrasadas".
5. **Mapa de produtos:** "Criar ação" em Baixo giro cria um cartão com "No painel hoje" e a contagem do grupo.
6. Não há erros em `read_console_messages` (com `onlyErrors`) e há `screenshot` da Central nos temas claro e escuro.

- [ ] **Step 9: Checkpoint de commit (menu, Resumo e origens)**

Mostre `git status` e peça aprovação. Com o "pode":

```bash
git add web/assets/navigation.js web/assets/app.js web/assets/insights.js web/assets/actions.js web/assets/style.css web/index.html tests/test_actions_frontend.cjs tests/test_actions_center_frontend.cjs
git commit -m "feat: count pending actions in the menu, flag overdue ones on the Resumo and drop the month selector"
```

O push só acontece quando o sócio pedir.

---

## Backlog registrado (fora deste plano)

- **15. Resumo semanal das ações por e-mail ou WhatsApp.**
  - O sócio gostou da ideia em 14/09/2026 e pediu para implementar em outro momento.
  - Juntar com a ideia 7 do Resumo executivo (resumo semanal do faturamento).
  - Vai precisar de um serviço de envio e de um cron da Vercel.
  - Detalhes em `docs/superpowers/specs/2026-09-14-central-de-acoes-design.md`.

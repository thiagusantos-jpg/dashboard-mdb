/* Financeiro, Custos e Despesas e Contas a Pagar. Loaded before app.js and
 * uses its shared api(), esc(), money(), APP and MONTHS globals only when a
 * route opens (same convention as settings.js). Every form opens in the shared
 * drawer from finance-forms.js; these pages list first and act per row. */
'use strict';

const FINANCE_SOURCE_LABELS = {
  manual: 'Lançamento manual',
  mobne: 'Mobne',
  multiple: 'Múltiplas origens',
  budget: 'Somente orçado',
  loan: 'Empréstimo',
};

const FINANCE_STATUS_LABELS = {
  forecast: 'Previsto',
  open: 'Em aberto',
  partially_paid: 'Parcialmente pago',
  paid: 'Pago',
  overdue: 'Vencido',
  cancelled: 'Cancelado',
  reversed: 'Estornado',
};

const FINANCE_STATUS_BADGES = {
  forecast: 'badge-info', open: 'badge-muted', partially_paid: 'badge-warning', paid: 'badge-success',
  overdue: 'badge-error', cancelled: 'badge-muted', reversed: 'badge-muted',
};

function financePeriodLabel(period) {
  const [y, m] = (period || '').split('-').map(Number);
  return m ? `${MONTHS[m - 1]}/${y}` : '—';
}

function dateBR(iso) {
  if (!iso) return '—';
  const [y, m, d] = String(iso).slice(0, 10).split('-');
  return `${d}/${m}/${y}`;
}

function statusBadge(status) {
  return `<span class="${FINANCE_STATUS_BADGES[status] || 'badge-muted'}">${esc(FINANCE_STATUS_LABELS[status] || status)}</span>`;
}

// Per-page filters survive a save or a return to the page within this visit.
function financeFilters(page, defaults) {
  APP.financeFilters = APP.financeFilters || {};
  APP.financeFilters[page] = Object.assign({}, defaults, APP.financeFilters[page] || {});
  return APP.financeFilters[page];
}

// A save re-renders the list; keep the reader where they were.
function refreshKeepingScroll(render) {
  const y = typeof window !== 'undefined' ? window.scrollY : 0;
  return Promise.resolve(render()).then(() => { if (typeof window !== 'undefined') window.scrollTo(0, y); });
}

/* A GET that failed or timed out offers a manual retry instead of looping on
 * its own or automatically re-issuing the request (never done for a POST). */
function financeError(title, subtitle, error, retry) {
  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <div class="story-box mt-16">${icon('triangle-alert')} Não foi possível carregar: ${esc(error.message)}</div>
    <div class="btn-row"><button type="button" class="btn-primary btn-wide" id="finance-retry">Tentar novamente</button></div>`;
  document.getElementById('finance-retry').addEventListener('click', () => retry());
}

function financeLoading(title, subtitle, label) {
  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <div class="skeleton-block" aria-label="${label}"></div>`;
}

/* `token` comes from app.js's renderPage() (via beginPage()) for the normal
 * navigation path; a page re-rendering itself after a save (or a retry click)
 * has no token yet and mints a fresh one here — that correctly invalidates
 * any older fetch still in flight for this same route. */
function renderFinancePage(token) {
  token = token || beginPage();
  const renderers = {
    financeiro: renderFinanceiro, despesas: renderDespesas, 'contas-pagar': renderContasPagar,
    emprestimos: renderEmprestimos, 'fluxo-caixa': renderFluxoCaixa, conciliacao: renderConciliacao,
    recebiveis: renderRecebiveis,
  };
  return (renderers[APP.page] || renderFinanceiro)(token);
}

/* ---------------------------------------------------------------- Financeiro */

async function renderFinanceiro(token) {
  token = token || beginPage();
  const title = `${icon('gauge', {class: 'title-icon'})}Financeiro`;
  const subtitle = 'Resultado gerencial da competência: receita, custos, despesas e distribuições.';
  financeLoading(title, subtitle, 'Carregando resultado gerencial');
  let result;
  try {
    result = await api(`/api/companies/${APP.company}/finance/management-result?period=${APP.period}`);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;  // usuário já saiu desta rota
    return financeError(title, subtitle, e, () => renderFinanceiro());
  }
  if (!APP.pageState.isCurrent(token)) return;  // resposta obsoleta: descarta em silêncio
  const kpis = [
    kpi('Receita', money(result.revenue_cents)),
    kpi('CMV', money(result.cogs_cents)),
    kpi('Lucro bruto', money(result.gross_profit_cents)),
    kpi('Despesas operacionais', money(result.operating_expenses_cents)),
    kpi('Pró-labore', money(result.owner_compensation_cents)),
    kpi('Resultado operacional', money(result.operating_result_cents)),
    kpi('Despesas financeiras', money(result.financial_expenses_cents)),
    kpi('Resultado gerencial', money(result.managerial_result_cents), null, null,
      result.managerial_result_cents == null ? '' : result.managerial_result_cents < 0 ? 'kpi-negative' : 'kpi-positive'),
    kpi('Distribuição de lucros', money(result.profit_distribution_cents)),
  ].join('');
  const rows = result.accounts.map((line) => `
    <tr>
      <td>${esc(line.name)}</td>
      <td class="num">${money(line.actual_cents)}</td>
      <td class="num">${line.budget_cents == null ? '—' : money(line.budget_cents)}</td>
      <td class="num">${line.variance_cents == null ? '—' : money(line.variance_cents)}</td>
      <td>${esc(FINANCE_SOURCE_LABELS[line.source] || line.source)}</td>
    </tr>`).join('');
  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <span class="periodo-badge">${icon('calendar')} Competência: ${financePeriodLabel(APP.period)}</span>
    <hr class="divider">
    <div class="kpi-grid kpi-grid-4">${kpis}</div>
    <h2 class="section-header">Contas — Realizado vs. Orçado</h2>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Conta</th><th class="num">Realizado</th><th class="num">Orçado</th><th class="num">Variação</th><th>Origem</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5">Nenhum lançamento nesta competência.</td></tr>'}</tbody>
    </table></div>`;
}

/* ---------------------------------------------------------------- Custos e Despesas */

const DESPESAS_FILTERS = {
  active: {label: 'Ativos', match: (e) => !['cancelled', 'reversed'].includes(e.status)},
  forecast: {label: 'Previstos a confirmar', match: (e) => e.status === 'forecast'},
  open: {label: 'Em aberto', match: (e) => ['open', 'partially_paid', 'overdue'].includes(e.status)},
  paid: {label: 'Pagos', match: (e) => e.status === 'paid'},
  cancelled: {label: 'Cancelados e estornados', match: (e) => ['cancelled', 'reversed'].includes(e.status)},
  all: {label: 'Todos', match: () => true},
};

function expenseRowActions(entry) {
  const actions = [];
  if (entry.status === 'forecast') actions.push(['confirm', 'Confirmar']);
  if (!['cancelled', 'reversed'].includes(entry.status)) actions.push(['edit', 'Editar']);
  if (entry.source === 'manual' && ['open', 'forecast'].includes(entry.status)) actions.push(['cancel', 'Cancelar']);
  actions.push(['history', 'Histórico']);
  return actions.map(([action, label]) =>
    `<button type="button" class="btn-secondary" data-entry-action="${action}" data-entry-id="${esc(entry.id)}"
       aria-label="${label}: ${esc(entry.description)}">${label}</button>`).join('');
}

async function renderDespesas(token) {
  token = token || beginPage();
  const title = `${icon('dollar-sign', {class: 'title-icon'})}Custos e Despesas`;
  const subtitle = 'Lançamentos de despesas por competência.';
  financeLoading(title, subtitle, 'Carregando despesas');
  const base = `/api/companies/${APP.company}/finance`;
  let accounts, counterparties, entries;
  try {
    [accounts, counterparties, entries] = await Promise.all([
      api(`${base}/accounts`),
      api(`${base}/counterparties`),
      api(`${base}/entries?competence=${APP.period}`),
    ]);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;
    return financeError(title, subtitle, e, () => renderDespesas());
  }
  if (!APP.pageState.isCurrent(token)) return;

  const filters = financeFilters('despesas', {status: 'active'});
  const accountsById = Object.fromEntries(accounts.map((a) => [String(a.id), a]));
  const expenseEntries = entries.filter((e) => {
    const account = accountsById[String(e.account_id)];
    return !account || EXPENSE_NATURES.includes(account.nature);
  });
  const visible = expenseEntries.filter((DESPESAS_FILTERS[filters.status] || DESPESAS_FILTERS.active).match);
  const realized = expenseEntries.filter((e) => !['forecast', 'cancelled', 'reversed'].includes(e.status))
    .reduce((sum, e) => sum + e.amount_cents, 0);
  const forecast = expenseEntries.filter((e) => e.status === 'forecast').reduce((sum, e) => sum + e.amount_cents, 0);

  const rows = visible.map((e) => `
    <tr>
      <td>${esc(e.description)}<div class="muted">${esc((accountsById[String(e.account_id)] || {}).name || '—')}${e.installment_count ? ` · parcela ${e.installment_number}/${e.installment_count}` : ''}</div></td>
      <td class="num">${money(e.amount_cents)}</td>
      <td>${dateBR(e.due_date)}</td>
      <td>${statusBadge(e.status)}</td>
      <td><div class="row-actions">${expenseRowActions(e)}</div></td>
    </tr>`).join('');
  const filterOptions = Object.entries(DESPESAS_FILTERS)
    .map(([value, f]) => `<option value="${value}"${value === filters.status ? ' selected' : ''}>${f.label}</option>`).join('');
  const emptyMessage = expenseEntries.length
    ? '<div class="empty-state">Nenhum lançamento com este filtro.</div>'
    : `<div class="empty-state">Nenhuma despesa lançada em ${financePeriodLabel(APP.period)}.
        <div><button type="button" class="btn-primary" data-expense-new>Lançar primeira despesa</button></div></div>`;

  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <span class="periodo-badge">${icon('calendar')} Competência: ${financePeriodLabel(APP.period)}</span>
    <div class="kpi-grid kpi-grid-3 mt-16">
      ${kpi('Realizado na competência', money(realized))}
      ${kpi('Previsto a confirmar', money(forecast))}
      ${kpi('Lançamentos', expenseEntries.length)}
    </div>
    <div class="page-toolbar">
      <div class="filters">
        <div><label class="field-label" for="despesas-status">Mostrar</label>
          <select id="despesas-status" class="login-input">${filterOptions}</select></div>
      </div>
      <button type="button" class="btn-primary btn-wide" data-expense-new>Nova despesa</button>
    </div>
    ${visible.length ? `<div class="table-wrap"><table class="data-table">
      <thead><tr><th>Descrição</th><th class="num">Valor</th><th>Vencimento</th><th>Status</th><th>Ações</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>` : emptyMessage}`;

  const refresh = () => refreshKeepingScroll(() => renderDespesas());
  const lookups = {accounts, counterparties};
  const content = document.getElementById('content');
  document.getElementById('despesas-status').addEventListener('change', (ev) => {
    filters.status = ev.target.value;
    refresh();
  });
  content.querySelectorAll('[data-expense-new]').forEach((button) => button.addEventListener('click', () =>
    openExpenseForm(null, Object.assign({trigger: button, onSaved: refresh}, lookups))));
  const entriesById = Object.fromEntries(entries.map((e) => [String(e.id), e]));
  content.querySelectorAll('[data-entry-action]').forEach((button) => button.addEventListener('click', () => {
    const entry = entriesById[button.dataset.entryId];
    const ctx = Object.assign({trigger: button, onSaved: refresh}, lookups);
    const open = {
      edit: () => openExpenseForm(entry, ctx), cancel: () => openCancelEntryForm(entry, ctx),
      confirm: () => openConfirmForecastForm(entry, ctx), history: () => openEntryHistory(entry, ctx),
    }[button.dataset.entryAction];
    if (open) open();
  }));
}

/* ---------------------------------------------------------------- Contas a Pagar */

const OBLIGATION_STATUS_FILTERS = [
  ['', 'Todas em aberto'], ['overdue', 'Vencidas'], ['partially_paid', 'Parcialmente pagas'], ['open', 'Em dia'],
];

function obligationQuery(filters, cursor) {
  const params = new URLSearchParams({limit: '50'});
  ['kind', 'status', 'q', 'due_from', 'due_to'].forEach((key) => { if (filters[key]) params.set(key, filters[key]); });
  if (cursor) params.set('cursor', cursor);
  return `/api/companies/${APP.company}/finance/obligations?${params}`;
}

function obligationRow(item) {
  const actions = [];
  if (item.allowed_actions.includes('pay')) {
    actions.push(`<button type="button" class="btn-secondary" data-obligation-pay="${esc(item.key)}" aria-label="Pagar: ${esc(item.description)}">Pagar</button>`);
  }
  actions.push(`<button type="button" class="btn-secondary" data-obligation-details="${esc(item.key)}" aria-label="Detalhes: ${esc(item.description)}">Detalhes</button>`);
  return `
    <tr>
      <td>${esc(item.description)}${item.kind === 'loan_installment' ? '<div class="muted">Empréstimo</div>' : ''}</td>
      <td class="${item.status === 'overdue' ? 'cell-alert' : ''}">${dateBR(item.due_date)}</td>
      <td class="num">${money(item.total_cents)}</td>
      <td class="num">${money(item.paid_cents)}</td>
      <td class="num"><strong>${money(item.open_cents)}</strong></td>
      <td>${statusBadge(item.status)}</td>
      <td><div class="row-actions">${actions.join('')}</div></td>
    </tr>`;
}

async function renderContasPagar(token) {
  token = token || beginPage();
  const title = `${icon('calendar', {class: 'title-icon'})}Contas a Pagar`;
  const subtitle = 'Despesas e parcelas de empréstimo em aberto, vencidas ou parcialmente pagas, em qualquer competência.';
  const routeKind = APP.routeParams && APP.routeParams.get('tipo') === 'emprestimo' ? 'loan_installment' : '';
  const filters = financeFilters('contas-pagar', {kind: routeKind, status: '', q: '', due_from: '', due_to: ''});
  if (routeKind) filters.kind = routeKind;
  financeLoading(title, subtitle, 'Carregando contas a pagar');
  let page;
  try {
    page = await api(obligationQuery(filters));
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;
    return financeError(title, subtitle, e, () => renderContasPagar());
  }
  if (!APP.pageState.isCurrent(token)) return;

  const items = page.items.slice();
  const statusOptions = OBLIGATION_STATUS_FILTERS
    .map(([value, label]) => `<option value="${value}"${value === filters.status ? ' selected' : ''}>${label}</option>`).join('');
  const kindOptions = [['', 'Todas'], ['entry', 'Despesas'], ['loan_installment', 'Parcelas de empréstimo']]
    .map(([value, label]) => `<option value="${value}"${value === filters.kind ? ' selected' : ''}>${label}</option>`).join('');
  const hasFilter = filters.kind || filters.status || filters.q || filters.due_from || filters.due_to;

  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <div class="kpi-grid kpi-grid-3 mt-16">
      ${kpi('Saldo em aberto', money(page.open_cents), hasFilter ? 'no filtro atual' : null)}
      ${kpi('Obrigações', page.total)}
    </div>
    <form id="obligations-filter" class="page-toolbar">
      <div class="filters">
        <div><label class="field-label" for="obligations-kind">Tipo</label>
          <select id="obligations-kind" name="kind" class="login-input">${kindOptions}</select></div>
        <div><label class="field-label" for="obligations-status">Situação</label>
          <select id="obligations-status" name="status" class="login-input">${statusOptions}</select></div>
        <div><label class="field-label" for="obligations-due-from">Vence de</label>
          <input id="obligations-due-from" name="due_from" type="date" class="login-input" value="${esc(filters.due_from)}"></div>
        <div><label class="field-label" for="obligations-due-to">até</label>
          <input id="obligations-due-to" name="due_to" type="date" class="login-input" value="${esc(filters.due_to)}"></div>
        <div><label class="field-label" for="obligations-q">Buscar</label>
          <input id="obligations-q" name="q" type="search" class="login-input" maxlength="120" value="${esc(filters.q)}" placeholder="Descrição ou credor"></div>
      </div>
      <div class="btn-row">
        <button type="submit" class="btn-primary btn-wide">Filtrar</button>
        ${hasFilter ? '<button type="button" class="btn-secondary" data-obligations-clear>Limpar filtros</button>' : ''}
      </div>
    </form>
    <div id="obligations-list"></div>`;

  const refresh = () => refreshKeepingScroll(() => renderContasPagar());
  const list = document.getElementById('obligations-list');
  let nextCursor = page.next_cursor;
  const paint = () => {
    if (!items.length) {
      list.innerHTML = hasFilter
        ? '<div class="empty-state">Nenhuma obrigação com estes filtros.</div>'
        : '<div class="empty-state">Nenhuma conta em aberto. Novas despesas entram aqui a partir de Custos e Despesas.</div>';
      return;
    }
    list.innerHTML = `
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th>Descrição</th><th>Vencimento</th><th class="num">Total</th><th class="num">Pago</th><th class="num">Saldo</th><th>Status</th><th>Ações</th></tr></thead>
        <tbody>${items.map(obligationRow).join('')}</tbody>
      </table></div>
      <p class="muted">Exibindo ${items.length} de ${page.total}.</p>
      ${nextCursor ? '<div class="btn-row"><button type="button" class="btn-secondary" data-obligations-more>Carregar mais</button></div>' : ''}`;
    const byKey = Object.fromEntries(items.map((item) => [item.key, item]));
    list.querySelectorAll('[data-obligation-pay]').forEach((button) => button.addEventListener('click', () =>
      openPaymentForm(byKey[button.dataset.obligationPay], {trigger: button, onSaved: refresh})));
    list.querySelectorAll('[data-obligation-details]').forEach((button) => button.addEventListener('click', () => {
      const item = byKey[button.dataset.obligationDetails];
      openObligationDetails(item.kind, item.id, {trigger: button, onSaved: refresh});
    }));
    const more = list.querySelector('[data-obligations-more]');
    if (more) {
      more.addEventListener('click', async () => {
        more.disabled = true;
        try {
          const next = await api(obligationQuery(filters, nextCursor));
          items.push(...next.items);
          nextCursor = next.next_cursor;
          paint();
        } catch (e) {
          more.disabled = false;
          more.insertAdjacentHTML('afterend', `<span class="form-error" role="alert">${esc(e.message)}</span>`);
        }
      });
    }
  };
  paint();

  document.getElementById('obligations-filter').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const form = ev.currentTarget;
    ['kind', 'status', 'q', 'due_from', 'due_to'].forEach((key) => { filters[key] = form.elements.namedItem(key).value.trim(); });
    renderContasPagar();
  });
  const clear = document.querySelector('[data-obligations-clear]');
  if (clear) clear.addEventListener('click', () => {
    Object.assign(filters, {kind: '', status: '', q: '', due_from: '', due_to: ''});
    renderContasPagar();
  });
}

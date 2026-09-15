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

// Financeiro competence (navigation.js): any calendar month, not only synced sales months.
function financeCompetence() { return APP.financePeriod || APP.period; }

// One source for the managerial result: the Financeiro page and the Resumo card both read it.
function managementResultUrl(period) {
  return `/api/companies/${APP.company}/finance/management-result?period=${encodeURIComponent(period)}`;
}

function periodReviewUrl(period) {
  return `/api/companies/${APP.company}/finance/period-reviews/${encodeURIComponent(period)}`;
}

/* Review = explicit acknowledgement tied to the revision on screen; reopening
 * needs a reason. The server refuses a revision that changed meanwhile. */
function buildReviewRequest(review, values) {
  const reopening = review.status === 'reviewed';
  const reason = String(values.reason || '').trim();
  if (!reopening && !values.acknowledged) {
    return {errors: {acknowledged: 'Confirme que conferiu as despesas e lançamentos do mês.'}};
  }
  if (reopening && !reason) return {errors: {reason: 'Informe o motivo para reabrir a apuração.'}};
  return {
    method: 'POST', path: periodReviewUrl(financeCompetence()),
    body: {action: reopening ? 'reopen' : 'review', expected_revision: review.revision, reason: reopening ? reason : '', acknowledged: !reopening},
  };
}

function reviewSummary(review) {
  const checks = (review && review.checks) || [];
  return {total: checks.length, pending: checks.filter((check) => !check.ok).length};
}

/* Compact review: one line saying whether the automatic checks passed, the list
 * opened only when something needs attention, then the acknowledgement. */
function reviewPanelHtml(review) {
  const {total, pending} = reviewSummary(review);
  const checks = (review.checks || []).map((check) => `
    <li class="${check.ok ? 'check-ok' : 'check-pending'}">
      ${icon(check.ok ? 'circle-check' : 'triangle-alert')}
      <span><span class="visually-hidden">${check.ok ? 'OK: ' : 'Atenção: '}</span>${esc(check.label)}${check.count != null ? `: ${check.count}` : ''}
      ${check.detail ? `<span class="muted">${esc(check.detail)}</span>` : ''}</span>
    </li>`).join('');
  const summary = !total ? 'Sem conferências automáticas para este mês'
    : pending ? `${pending} ${pending === 1 ? 'ponto' : 'pontos'} para conferir`
      : `Todas as ${total} conferências automáticas estão OK`;
  const reviewed = review.status === 'reviewed';
  const action = reviewed ? `
      <div class="form-field">
        <label class="field-label" for="period-review-reason">Motivo para reabrir</label>
        <input id="period-review-reason" name="reason" class="login-input" maxlength="500">
      </div>
      <div class="form-error" data-form-error role="alert"></div>
      <div class="btn-row"><button type="submit" class="btn-secondary">Reabrir apuração</button></div>` : `
      <label class="checkbox-line"><input type="checkbox" name="acknowledged" id="period-review-ack">
        Conferi as despesas e lançamentos deste mês. A lista acima ajuda, mas não garante que nada ficou de fora.</label>
      <div class="form-error" data-form-error role="alert"></div>
      <div class="btn-row"><button type="submit" class="btn-primary btn-wide">Marcar como revisado</button></div>`;
  return `
    <section class="side-card review-card" aria-labelledby="period-review-title">
      <h2 id="period-review-title" class="side-card-title">Revisão do mês</h2>
      <p class="side-card-lead">Conferência gerencial, não fechamento contábil: qualquer alteração volta o mês para Em apuração.</p>
      ${review.changed_since_review ? '<div class="story-box">Os dados foram alterados depois da última revisão.</div>' : ''}
      ${reviewed && review.reviewed_at ? `<p class="muted">Revisado em ${esc(typeof dt === 'function' ? dt(review.reviewed_at) : review.reviewed_at)}.</p>` : ''}
      ${!reviewed && review.reason ? `<p class="muted">Reaberto: ${esc(review.reason)}</p>` : ''}
      <details class="review-details"${pending ? ' open' : ''}>
        <summary><span class="review-summary ${pending ? 'pending' : 'ok'}">${icon(pending ? 'triangle-alert' : 'circle-check')} ${summary}</span></summary>
        <ul class="review-checks">${checks}</ul>
      </details>
      <form id="period-review-form" novalidate>${action}</form>
    </section>`;
}

// "Em apuração" until the monthly review exists (C6); the detail names what is missing.
function managementStatus(result) {
  const status = (result && result.data_status) || {};
  const notes = [];
  if (status.reason) notes.push(status.reason);
  if (status.restricted && !/restrit/i.test(status.reason || '')) {
    notes.push('Parte das despesas é restrita ao seu perfil: totais não exibidos.');
  }
  return {
    label: status.expenses_reviewed ? 'Revisado' : 'Em apuração',
    detail: notes.join(' ') || (status.expenses_reviewed ? 'Despesas do mês revisadas pelos sócios.' : 'Despesas do mês ainda não revisadas.'),
  };
}

function pctBR(value) {
  return value == null ? '—' : `${Number(value).toFixed(1).replace('.', ',')}%`;
}

/* Break-even from the cost center: fixed costs ÷ contribution margin. When an
 * input is missing the reason is shown instead of a number. */
function breakEvenHtml(result) {
  const be = (result && result.break_even) || {};
  const available = be.break_even_cents != null;
  const gap = be.gap_pct;
  const gapText = gap == null ? '' : gap >= 0
    ? `Faturamento ${pctBR(gap)} acima do ponto de equilíbrio.`
    : `Faturamento ${pctBR(Math.abs(gap))} abaixo do ponto de equilíbrio.`;
  const reached = available ? Math.min(100, Math.round(((result && result.revenue_cents) || 0) * 100 / be.break_even_cents)) : 0;
  return `
    <section class="side-card break-even-card" aria-labelledby="break-even-title">
      <h2 id="break-even-title" class="side-card-title">Ponto de equilíbrio</h2>
      <p class="be-value ${available ? (gap >= 0 ? 'positive' : 'negative') : 'unavailable'}">${available ? money(be.break_even_cents) : 'Indisponível'}</p>
      ${available ? `<p class="be-gap">${gapText}</p>
        <div class="be-track" aria-hidden="true"><span class="be-fill${gap >= 0 ? ' over' : ''}" data-w="${reached}"></span></div>`
        : `<p class="be-reason">${esc(be.reason || 'Dados insuficientes para calcular.')}</p>`}
      <dl class="be-facts">
        <div><dt>Custos fixos</dt><dd>${money(be.fixed_costs_cents)}</dd></div>
        <div><dt>Custos variáveis</dt><dd>${money(be.variable_costs_cents)}</dd></div>
        <div><dt>Margem de contribuição</dt><dd>${pctBR(be.contribution_margin_pct)}${be.contribution_margin_cents == null ? '' : ` · ${money(be.contribution_margin_cents)}`}</dd></div>
      </dl>
      <details class="be-how"><summary>Como é calculado</summary>
        <p>Custos fixos ÷ margem de contribuição (faturamento − CMV − custos variáveis). Fixo ou variável é definido por categoria em Configurações → Categorias e favorecidos.</p></details>
    </section>`;
}

/* ---------------------------------------------------------------- Cascata do resultado */

// finance-forms.js already owns a global EXPENSE_NATURES; a second declaration stops this whole script loading.
const RESULT_EXPENSE_NATURES = ['operating_expense', 'tax_expense', 'financial_expense'];
const DRE_LINES = [
  ['revenue_cents', 'Receita', 'base'],
  ['cogs_cents', 'CMV', 'minus'],
  ['gross_profit_cents', 'Lucro bruto', 'subtotal'],
  ['operating_expenses_cents', 'Despesas operacionais', 'minus'],
  ['owner_compensation_cents', 'Pró-labore', 'minus'],
  ['operating_result_cents', 'Resultado operacional', 'subtotal'],
  ['financial_expenses_cents', 'Despesas financeiras', 'minus'],
  ['managerial_result_cents', 'Resultado gerencial', 'total'],
];

function shareOfRevenue(cents, revenue) {
  return cents == null || !revenue ? null : Math.round(cents * 1000 / revenue) / 10;
}

// A zero expense line is "sem lançamentos", never a confirmed zero.
function dreRows(result) {
  return DRE_LINES.map(([key, label, kind]) => ({
    key, label, kind, cents: result[key],
    pct: shareOfRevenue(result[key], result.revenue_cents),
    empty: kind === 'minus' && key !== 'cogs_cents' && result[key] === 0,
  }));
}

function resultHeadline(result) {
  const cents = result.managerial_result_cents;
  return {cents, tone: cents == null ? 'unavailable' : cents < 0 ? 'negative' : 'positive',
    marginPct: shareOfRevenue(cents, result.revenue_cents)};
}

// With sales in but no expense launched, the result is only the gross profit; say so.
function hasNoExpenses(result) {
  const status = result.data_status || {};
  if (status.restricted || result.revenue_cents == null) return false;
  return !(result.accounts || []).some((line) => RESULT_EXPENSE_NATURES.includes(line.nature) && line.actual_cents);
}

function resultHeroHtml(result, period, status) {
  const h = resultHeadline(result);
  const reviewed = status.label === 'Revisado';
  return `
    <section class="result-hero" aria-labelledby="result-hero-title">
      <div class="result-hero-main">
        <p class="result-eyebrow" id="result-hero-title">Resultado gerencial de ${financePeriodLabel(period)}</p>
        <p class="result-value ${h.tone}">${h.cents == null ? 'Indisponível' : money(h.cents)}</p>
        <p class="result-note">${h.marginPct == null ? 'Sem receita e CMV confirmados para calcular a margem.'
          : `${pctBR(h.marginPct)} da receita de ${money(result.revenue_cents)}`}</p>
      </div>
      <div class="result-hero-status">
        <span class="${reviewed ? 'badge-success' : 'badge-warning'}">${esc(status.label)}</span>
        <p>${esc(status.detail)}</p>
      </div>
    </section>`;
}

function dreHtml(result) {
  const rows = dreRows(result).map((r) => {
    const sign = r.kind === 'minus' ? '−' : r.kind === 'base' ? '' : '=';
    const tone = r.kind === 'total' && r.cents != null ? (r.cents < 0 ? ' negative' : ' positive') : '';
    const width = r.pct == null ? 0 : Math.max(0, Math.min(100, Math.abs(r.pct)));
    return `<li class="dre-row ${r.kind}${r.empty ? ' empty' : ''}${tone}">
      <span class="dre-sign" aria-hidden="true">${sign}</span>
      <span class="dre-label">${esc(r.label)}${r.empty ? ' <small>sem lançamentos</small>' : ''}</span>
      <span class="dre-value">${r.cents == null ? 'Indisponível' : money(r.cents)}</span>
      <span class="dre-pct">${pctBR(r.pct)}</span>
      <span class="dre-bar" aria-hidden="true"><i data-w="${width}"></i></span>
    </li>`;
  }).join('');
  const distribution = result.profit_distribution_cents;
  return `
    <section class="dre-card" aria-labelledby="dre-title">
      <div class="dre-head"><h2 id="dre-title">Do faturamento ao resultado</h2><span>% da receita</span></div>
      <ol class="dre-list">${rows}</ol>
      <p class="dre-after">Distribuição de lucros: <b>${distribution == null ? 'Indisponível' : money(distribution)}</b>
        · retirada dos sócios depois do resultado, não entra no cálculo.</p>
    </section>`;
}

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
  const title = `${icon('gauge', {class: 'title-icon'})}Resultado gerencial`;
  const subtitle = 'Quanto sobrou do faturamento depois de custos e despesas, no mês de competência.';
  financeLoading(title, subtitle, 'Carregando resultado gerencial');
  let result, review;
  try {
    [result, review] = await Promise.all([
      api(managementResultUrl(financeCompetence())),
      // Review status is secondary: without permission or on failure the result still shows.
      api(periodReviewUrl(financeCompetence())).catch(() => null),
    ]);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;  // usuário já saiu desta rota
    return financeError(title, subtitle, e, () => renderFinanceiro());
  }
  if (!APP.pageState.isCurrent(token)) return;  // resposta obsoleta: descarta em silêncio
  const period = financeCompetence();
  const status = managementStatus(result);
  const expensesHref = routeHash('despesas', period);
  const rows = result.accounts.map((line) => {
    // Spending above budget is bad news; earning above it is good news.
    const spending = RESULT_EXPENSE_NATURES.includes(line.nature) || line.nature === 'cogs';
    const variance = line.variance_cents;
    const tone = !variance ? '' : (variance > 0) === spending ? ' var-bad' : ' var-good';
    return `
    <tr>
      <td>${esc(line.name)}</td>
      <td class="num">${money(line.actual_cents)}</td>
      <td class="num">${line.budget_cents == null ? '—' : money(line.budget_cents)}</td>
      <td class="num${tone}">${variance == null ? '—' : money(variance)}</td>
      <td>${esc(FINANCE_SOURCE_LABELS[line.source] || line.source)}</td>
    </tr>`;
  }).join('');
  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    ${resultHeroHtml(result, period, status)}
    ${hasNoExpenses(result) ? `<div class="result-warning" role="note">${icon('triangle-alert')}
      <p><strong>Nenhuma despesa lançada em ${financePeriodLabel(period)}.</strong>
        Sem aluguel, salários e contas do mês, o resultado gerencial fica igual ao lucro bruto.
        <a href="${expensesHref}">Lançar despesas →</a></p></div>` : ''}
    <div class="result-layout">
      <div class="result-main">${dreHtml(result)}</div>
      <aside class="result-side" aria-label="Ponto de equilíbrio e revisão do mês">
        ${breakEvenHtml(result)}
        ${review ? reviewPanelHtml(review) : ''}
      </aside>
    </div>
    ${rows ? `<h2 class="section-header">Contas — Realizado vs. Orçado</h2>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Conta</th><th class="num">Realizado</th><th class="num">Orçado</th><th class="num">Variação</th><th>Origem</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>` : `<p class="accounts-empty">Nenhuma conta com lançamento ou orçamento em ${financePeriodLabel(period)}.
      <a href="${expensesHref}">Abrir Custos e despesas →</a></p>`}`;
  // Bar lengths through the CSSOM: the CSP forbids inline style attributes.
  document.querySelectorAll('#content [data-w]').forEach((el) => { el.style.width = `${el.dataset.w}%`; });
  const reviewForm = document.getElementById('period-review-form');
  if (reviewForm && typeof bindForm === 'function') {
    bindForm(reviewForm, (values) => buildReviewRequest(review, values), () => refreshKeepingScroll(() => renderFinanceiro()));
  }
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
  const title = `${icon('dollar-sign', {class: 'title-icon'})}Custos e despesas`;
  const subtitle = 'Lançamentos de despesas por competência.';
  financeLoading(title, subtitle, 'Carregando despesas');
  const base = `/api/companies/${APP.company}/finance`;
  let accounts, counterparties, entries;
  try {
    [accounts, counterparties, entries] = await Promise.all([
      api(`${base}/accounts`),
      api(`${base}/counterparties`),
      api(`${base}/entries?competence=${financeCompetence()}`),
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
    : `<div class="empty-state">Nenhuma despesa lançada em ${financePeriodLabel(financeCompetence())}.
        <div><button type="button" class="btn-primary" data-expense-new>Lançar primeira despesa</button></div></div>`;

  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <span class="periodo-badge">${icon('calendar')} Competência: ${financePeriodLabel(financeCompetence())}</span>
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
  const title = `${icon('calendar', {class: 'title-icon'})}Contas a pagar`;
  const subtitle = 'Despesas e parcelas de empréstimo em aberto, vencidas ou parcialmente pagas, em qualquer competência.';
  const routeKind = APP.routeParams && APP.routeParams.get('tipo') === 'emprestimo' ? 'loan_installment' : '';
  const filters = financeFilters('contas-pagar', {kind: routeKind, status: '', q: '', due_from: '', due_to: ''});
  if (routeKind) filters.kind = routeKind;
  financeLoading(title, subtitle, 'Carregando contas a pagar');
  let page, positions = null;
  try {
    // The loan filter also shows the contracts (ficha, renegotiation), which
    // used to live on their own Empréstimos page.
    [page, positions] = await Promise.all([
      api(obligationQuery(filters)),
      filters.kind === 'loan_installment' ? api(`/api/companies/${APP.company}/finance/loans`) : Promise.resolve(null),
    ]);
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
    <div class="btn-row">
      <button type="button" class="btn-primary btn-wide" data-expense-new>Nova despesa</button>
      <button type="button" class="btn-secondary" data-loan-new>Novo empréstimo</button>
    </div>
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
    <div id="obligations-list"></div>
    ${positions ? `<h2 class="section-header">Contratos de empréstimo</h2>${loanContractsHtml(positions)}` : ''}`;

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
  const content = document.getElementById('content');
  content.querySelectorAll('[data-expense-new]').forEach((button) =>
    button.addEventListener('click', () => openExpenseForm(null, {trigger: button, onSaved: refresh})));
  content.querySelectorAll('[data-loan-new]').forEach((button) =>
    button.addEventListener('click', () => openLoanForm(null, {trigger: button, onSaved: refresh})));
  if (positions) bindLoanCards(content, positions, refresh);

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

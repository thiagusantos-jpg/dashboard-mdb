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
  recurrence: 'Despesa recorrente',
  stone_receivable: 'Stone',
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
  ['financial_income_cents', 'Rendimentos de aplicações', 'plus'],
  ['managerial_result_cents', 'Resultado gerencial', 'total'],
];

function shareOfRevenue(cents, revenue) {
  return cents == null || !revenue ? null : Math.round(cents * 1000 / revenue) / 10;
}

// A zero expense line is "sem lançamentos", never a confirmed zero.
// Investment income only shows up in the months that have it.
function dreRows(result) {
  return DRE_LINES.filter(([key, , kind]) => kind !== 'plus' || result[key]).map(([key, label, kind]) => ({
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
    const sign = r.kind === 'minus' ? '−' : r.kind === 'plus' ? '+' : r.kind === 'base' ? '' : '=';
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

/* ---------------------------------------------------------------- Custo zero e despesas fixas */

// Revenue from items sold at a zero cost in the Mobne has no CMV behind it: the margin reads higher than it is.
function costQualityNote(result) {
  const quality = result && result.cost_quality;
  if (!quality || quality.zero_cost_share_pct == null || quality.zero_cost_share_pct < 1) return null;
  const items = quality.zero_cost_items;
  return `${pctBR(quality.zero_cost_share_pct)} do faturamento (${items} ${items === 1 ? 'item vendido' : 'itens vendidos'}) veio de produtos com custo zero no Mobne: o CMV está subestimado e a margem pode estar maior do que a real.`;
}

function pendingForecasts(review) {
  const check = ((review && review.checks) || []).find((item) => item.key === 'forecasts_pending');
  return check && check.count ? check.count : 0;
}

function fixedSetupUrl() {
  return `/api/companies/${APP.company}/finance/fixed-expenses`;
}

// Only the rows with a value are sent; each becomes a monthly recurrence confirmed for this month.
function buildFixedSetupRequest(period, rows) {
  const errors = {};
  const items = [];
  rows.forEach((row) => {
    if (!String(row.amount || '').trim()) return;
    const cents = parseMoneyToCents(row.amount);
    const day = parseInt(row.due_day, 10);
    if (cents == null || cents <= 0) errors[`amount-${row.system_key}`] = 'Valor inválido.';
    if (!(day >= 1 && day <= 31)) errors[`due-${row.system_key}`] = 'Dia de 1 a 31.';
    items.push({system_key: row.system_key, amount_cents: cents, due_day: day});
  });
  if (!items.length) errors.form = 'Preencha o valor de pelo menos uma despesa.';
  if (Object.keys(errors).length) return {errors};
  return {method: 'POST', path: fixedSetupUrl(), body: {period, items}};
}

function fixedSetupHtml(period) {
  return `
    <section class="fixed-setup" id="fixed-setup" aria-labelledby="fixed-setup-title">
      <div class="fixed-setup-head">${icon('triangle-alert')}
        <div><h2 id="fixed-setup-title">Nenhuma despesa lançada em ${financePeriodLabel(period)}</h2>
          <p>Sem aluguel, salários e contas do mês, o resultado gerencial fica igual ao lucro bruto. Preencha as despesas que a loja paga todo mês:
            elas ficam cadastradas e, nos próximos meses, aparecem como previstas para você só confirmar.</p></div>
      </div>
      <form id="fixed-setup-form" novalidate><div class="skeleton-block" aria-label="Carregando despesas típicas"></div></form>
    </section>`;
}

function fixedSetupRowsHtml(rows, period) {
  const cells = rows.map((row) => {
    const key = esc(row.system_key);
    if (row.stone_automatic && !row.configured) {
      return `<span class="fixed-setup-label">${esc(row.label)}</span>
        <span class="fixed-setup-done">${icon('circle-check')} Automática pelo relatório da Stone</span><span></span>`;
    }
    if (row.configured) {
      return `<span class="fixed-setup-label">${esc(row.label)}</span>
        <span class="fixed-setup-done">${icon('circle-check')} Já cadastrada</span><span></span>`;
    }
    return `<label class="fixed-setup-label" for="fixed-amount-${key}">${esc(row.label)}</label>
      <span><input id="fixed-amount-${key}" name="amount-${key}" class="login-input" inputmode="decimal" placeholder="0,00" autocomplete="off">
        <span class="field-error" data-error-for="amount-${key}"></span></span>
      <span><input id="fixed-due-${key}" name="due-${key}" class="login-input" type="number" min="1" max="31" value="10"
          aria-label="Dia de vencimento de ${esc(row.label)}">
        <span class="field-error" data-error-for="due-${key}"></span></span>`;
  }).join('');
  return `
    <div class="fixed-setup-grid" role="group" aria-label="Despesas fixas do mês">
      <span class="fixed-setup-col">Despesa</span><span class="fixed-setup-col">Valor por mês</span><span class="fixed-setup-col">Vence dia</span>
      ${cells}
    </div>
    <p class="form-error" data-form-error role="alert"></p>
    <div class="btn-row"><button type="submit" class="btn-primary btn-wide">Salvar despesas fixas</button>
      <a class="btn-link" href="${routeHash('despesas', period)}">Lançar outra despesa →</a></div>`;
}

async function loadFixedSetup(period) {
  const form = document.getElementById('fixed-setup-form');
  if (!form) return;
  let rows;
  try {
    rows = await api(fixedSetupUrl());
  } catch (e) {
    if (form.isConnected) {
      form.innerHTML = `<p class="muted">Não foi possível carregar as despesas típicas: ${esc(e.message)}.
        <a href="${routeHash('despesas', period)}">Abrir Despesas do mês →</a></p>`;
    }
    return;
  }
  if (!form.isConnected) return;
  form.innerHTML = fixedSetupRowsHtml(rows, period);
  if (typeof watchForm === 'function') watchForm(form);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    form.querySelectorAll('[data-error-for]').forEach((slot) => { slot.textContent = ''; });
    const formError = form.querySelector('[data-form-error]');
    formError.textContent = '';
    const values = rows.filter((row) => !row.configured && !row.stone_automatic).map((row) => ({
      system_key: row.system_key,
      amount: form.elements[`amount-${row.system_key}`].value,
      due_day: form.elements[`due-${row.system_key}`].value,
    }));
    const request = buildFixedSetupRequest(period, values);
    if (request.errors) {
      Object.entries(request.errors).forEach(([field, message]) => {
        const slot = form.querySelector(`[data-error-for="${field}"]`);
        if (slot) slot.textContent = message; else formError.textContent = message;
      });
      return;
    }
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    button.textContent = 'Salvando…';
    try {
      await api(request.path, {method: 'POST', body: JSON.stringify(request.body)});
      if (typeof clearDirty === 'function') clearDirty();
      refreshKeepingScroll(() => renderFinanceiro());
    } catch (e) {
      button.disabled = false;
      button.textContent = 'Salvar despesas fixas';
      formError.textContent = 'Não foi possível salvar: ' + e.message;
    }
  });
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
  const forecasts = pendingForecasts(review);
  const costNote = costQualityNote(result);
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
    ${hasNoExpenses(result) && !forecasts ? fixedSetupHtml(period) : ''}
    ${hasNoExpenses(result) && forecasts ? `<div class="result-warning" role="note">${icon('triangle-alert')}
      <p><strong>${forecasts} ${forecasts === 1 ? 'despesa prevista aguarda' : 'despesas previstas aguardam'} confirmação em ${financePeriodLabel(period)}.</strong>
        Enquanto não forem confirmadas, o resultado gerencial fica igual ao lucro bruto.
        <a href="${expensesHref}">Confirmar em Despesas do mês →</a></p></div>` : ''}
    ${costNote ? `<div class="result-warning" role="note">${icon('triangle-alert')}
      <p>${esc(costNote)} <a href="${routeHash('estoque', period, new URLSearchParams({filtro: 'custo-zero'}))}">Ver produtos com custo zero →</a></p></div>` : ''}
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
      <a href="${expensesHref}">Abrir Despesas do mês →</a></p>`}`;
  // Bar lengths through the CSSOM: the CSP forbids inline style attributes.
  document.querySelectorAll('#content [data-w]').forEach((el) => { el.style.width = `${el.dataset.w}%`; });
  if (hasNoExpenses(result) && !forecasts) loadFixedSetup(period);
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

/* Taxas e mensalidade vindas do relatório da Stone: já descontadas no depósito e
 * conferidas pela importação. Não há o que pagar nem o que revisar uma a uma. */
function isStoneAutomatic(entry) {
  return entry.source === 'stone_receivable';
}

function expenseRowActions(entry) {
  const actions = [];
  if (isStoneAutomatic(entry)) {
    return `<button type="button" class="btn-secondary" data-entry-action="history" data-entry-id="${esc(entry.id)}"
       aria-label="Histórico: ${esc(entry.description)}">Histórico</button>`;
  }
  if (entry.status === 'forecast') actions.push(['confirm', 'Confirmar']);
  if (!['cancelled', 'reversed'].includes(entry.status)) actions.push(['edit', 'Editar']);
  if (entry.source === 'manual' && ['open', 'forecast'].includes(entry.status)) actions.push(['cancel', 'Cancelar']);
  actions.push(['history', 'Histórico']);
  return actions.map(([action, label]) =>
    `<button type="button" class="btn-secondary" data-entry-action="${action}" data-entry-id="${esc(entry.id)}"
       aria-label="${label}: ${esc(entry.description)}">${label}</button>`).join('');
}

/* Uma despesa solta não responde nada; a categoria responde. Agrupa o que está visível e
 * ordena pelo maior gasto, que é a primeira pergunta do sócio: para onde foi o dinheiro. */
function expenseGroups(entries, accountsById) {
  const groups = new Map();
  entries.forEach((entry) => {
    const key = String(entry.account_id || 'sem-categoria');
    if (!groups.has(key)) {
      const account = accountsById[String(entry.account_id)] || {};
      groups.set(key, {key, name: account.name || 'Sem categoria', cents: 0, items: []});
    }
    const group = groups.get(key);
    group.items.push(entry);
    // Previsto e cancelado entram na lista, mas não no total: não são dinheiro gasto.
    if (!['cancelled', 'reversed', 'forecast'].includes(entry.status)) group.cents += entry.amount_cents;
  });
  const list = Array.from(groups.values());
  const total = list.reduce((sum, group) => sum + group.cents, 0);
  list.forEach((group) => { group.share = total ? Math.round(group.cents * 1000 / total) / 10 : 0; });
  return list.sort((a, b) => b.cents - a.cents || a.name.localeCompare(b.name, 'pt-BR'));
}

function expenseTotals(entries) {
  const spent = entries.filter((entry) => !['forecast', 'cancelled', 'reversed'].includes(entry.status));
  const forecast = entries.filter((entry) => entry.status === 'forecast');
  return {
    realized_cents: spent.reduce((sum, entry) => sum + entry.amount_cents, 0),
    forecast_cents: forecast.reduce((sum, entry) => sum + entry.amount_cents, 0),
    forecast_count: forecast.length,
    count: entries.length,
  };
}

/* R$ 8.000 é muito ou pouco? Sozinho o número não diz; o mês anterior diz. Em despesa,
 * subir é ruim — por isso a comparação carrega o tom, e não só o sinal. */
function expenseDelta(currentCents, previousCents) {
  if (previousCents == null) return null;
  if (!previousCents) return {tone: 'none', text: 'sem despesas no mês anterior'};
  const diff = currentCents - previousCents;
  if (!diff) return {tone: 'none', text: 'igual ao mês anterior'};
  const pct = Math.round(Math.abs(diff) * 1000 / previousCents) / 10;
  return {
    tone: diff > 0 ? 'bad' : 'good',
    text: `${diff > 0 ? '+' : '−'}${String(pct).replace('.', ',')}% vs mês anterior (${money(previousCents)})`,
  };
}

function expensesCardsHtml(totals, delta, groups) {
  const top = groups[0];
  const card = (label, value, sub, tone) => `<div class="action-stat${tone ? ` ${tone}` : ''}">
      <span class="action-stat-value">${value}</span><span class="action-stat-label">${label}</span>
      <span class="action-stat-sub">${sub}</span></div>`;
  return `<div class="actions-stats payables-cards">
    ${card('Total na competência', money(totals.realized_cents),
      delta ? delta.text : 'sem mês anterior para comparar', delta && delta.tone === 'bad' ? 'alert' : '')}
    ${card('Previsto a confirmar', money(totals.forecast_cents),
      totals.forecast_count ? `${totals.forecast_count} ${totals.forecast_count === 1 ? 'lançamento' : 'lançamentos'}` : 'nada pendente')}
    ${top && top.cents
      ? card('Maior categoria', esc(top.name), `${money(top.cents)} · ${String(top.share).replace('.', ',')}% do total`)
      : card('Maior categoria', '—', 'nenhuma despesa na competência')}
  </div>`;
}

/* O vencimento aparece para conferir o que foi digitado, sem tom de urgência: cobrar
 * atraso é trabalho de Contas a pagar, e duas telas alarmando a mesma conta confundem. */
function expenseRowHtml(entry, accountsById) {
  const parcel = entry.installment_count ? ` · parcela ${entry.installment_number}/${entry.installment_count}` : '';
  const auto = isStoneAutomatic(entry);
  return `
    <tr${auto ? ' data-stone-auto' : ''}>
      <td>${esc(entry.description)}${auto ? ' <span class="payables-tag">Automático · Stone</span>' : ''}<div class="muted">${esc((accountsById[String(entry.account_id)] || {}).name || '—')}${parcel}</div></td>
      <td class="num">${money(entry.amount_cents)}</td>
      <td>${dateBR(entry.due_date)}</td>
      <td>${auto ? '<span class="badge-success">Descontado pela Stone</span>' : statusBadge(entry.status)}</td>
      <td><div class="row-actions">${expenseRowActions(entry)}</div></td>
    </tr>`;
}

/* A ponte entre as duas telas, dita em voz alta em vez de adivinhada: aqui se vê quanto o
 * mês custou; o que ainda precisa sair do caixa se paga na outra. */
function unpaidNotice(entries) {
  const open = (entries || []).filter((entry) => !isStoneAutomatic(entry) && ['open', 'overdue', 'partially_paid'].includes(entry.status));
  if (!open.length) return null;
  return {count: open.length, cents: open.reduce((sum, entry) => sum + entry.amount_cents, 0)};
}

function unpaidNoticeHtml(notice) {
  if (!notice) return '';
  const lead = notice.count === 1
    ? 'Uma destas despesas ainda não foi paga'
    : `${notice.count} destas despesas ainda não foram pagas`;
  return `<p class="payables-coverage none" role="status">${icon('lightbulb')}
      <span>${lead} (${money(notice.cents)}). Vencimento e pagamento ficam em Contas a pagar.</span>
      <a href="${routeHash('contas-pagar')}">Abrir Contas a pagar →</a></p>`;
}

// A category made only of Stone automatic entries (one per day) folds into one line.
function expenseGroupRowsHtml(group, accountsById) {
  const rows = group.items.map((entry) => expenseRowHtml(entry, accountsById));
  if (group.items.length <= 3 || !group.items.every(isStoneAutomatic)) return rows.join('');
  return `<tr class="stone-auto-summary"><td colspan="5">
      <span class="payables-tag">Automático · Stone</span>
      ${group.items.length} lançamentos vindos do relatório de recebíveis, já conferidos na importação.
      <button type="button" class="btn-link" data-stone-auto-toggle="${esc(group.key)}" aria-expanded="false">Ver dia a dia</button>
    </td></tr>${rows.map((row) => row.replace('<tr data-stone-auto', `<tr data-stone-auto data-stone-group="${esc(group.key)}" hidden`)).join('')}`;
}

function expenseGroupsHtml(groups, accountsById) {
  return `<div class="table-wrap"><table class="data-table payables-table">
      <thead><tr><th>Descrição</th><th class="num">Valor</th><th>Vencimento</th><th>Status</th>
        <th><span class="visually-hidden">Ações</span></th></tr></thead>
      ${groups.map((group) => `<tbody class="payables-group">
        <tr class="payables-group-row"><th colspan="5" scope="rowgroup">${esc(group.name)}
          <span>${group.items.length} ${group.items.length === 1 ? 'lançamento' : 'lançamentos'} · ${money(group.cents)}${group.cents ? ` · ${String(group.share).replace('.', ',')}% do total` : ''}</span></th></tr>
        ${expenseGroupRowsHtml(group, accountsById)}</tbody>`).join('')}
    </table></div>`;
}

async function renderDespesas(token) {
  token = token || beginPage();
  const title = `${icon('dollar-sign', {class: 'title-icon'})}Despesas do mês`;
  const subtitle = `Para onde foi o dinheiro em ${financePeriodLabel(financeCompetence())}.`;
  financeLoading(title, subtitle, 'Carregando despesas');
  const base = `/api/companies/${APP.company}/finance`;
  let accounts, counterparties, entries, previousEntries = null;
  try {
    [accounts, counterparties, entries, previousEntries] = await Promise.all([
      api(`${base}/accounts`),
      api(`${base}/counterparties`),
      api(`${base}/entries?competence=${financeCompetence()}`),
      // Só comparação: se falhar, a página perde a linha "vs mês anterior", nunca a lista.
      api(`${base}/entries?competence=${monthShift(financeCompetence(), -1)}`).catch(() => null),
    ]);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;
    return financeError(title, subtitle, e, () => renderDespesas());
  }
  if (!APP.pageState.isCurrent(token)) return;

  const filters = financeFilters('despesas', {status: 'active', q: ''});
  const accountsById = Object.fromEntries(accounts.map((a) => [String(a.id), a]));
  const isExpense = (entry) => {
    const account = accountsById[String(entry.account_id)];
    return !account || EXPENSE_NATURES.includes(account.nature);
  };
  const expenseEntries = entries.filter(isExpense);
  const term = String(filters.q || '').trim().toLowerCase();
  const visible = expenseEntries
    .filter((DESPESAS_FILTERS[filters.status] || DESPESAS_FILTERS.active).match)
    .filter((entry) => !term
      || `${entry.description} ${(accountsById[String(entry.account_id)] || {}).name || ''}`.toLowerCase().includes(term));

  const totals = expenseTotals(expenseEntries);
  const previousTotal = previousEntries ? expenseTotals(previousEntries.filter(isExpense)).realized_cents : null;
  const delta = expenseDelta(totals.realized_cents, previousTotal);
  const filterOptions = Object.entries(DESPESAS_FILTERS)
    .map(([value, f]) => `<option value="${value}"${value === filters.status ? ' selected' : ''}>${f.label}</option>`).join('');
  const emptyMessage = expenseEntries.length
    ? '<div class="empty-state">Nenhum lançamento com este filtro.</div>'
    : `<div class="empty-state">Nenhuma despesa lançada em ${financePeriodLabel(financeCompetence())}.
        <div><button type="button" class="btn-primary" data-expense-new>Lançar primeira despesa</button></div></div>`;

  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    ${expensesCardsHtml(totals, delta, expenseGroups(expenseEntries, accountsById))}
    ${unpaidNoticeHtml(unpaidNotice(expenseEntries))}
    <div class="page-toolbar">
      <div class="filters">
        <div><label class="field-label" for="despesas-q">Buscar</label>
          <input id="despesas-q" class="login-input" type="search" autocomplete="off"
            placeholder="Descrição ou categoria" value="${esc(filters.q || '')}"></div>
        <div><label class="field-label" for="despesas-status">Mostrar</label>
          <select id="despesas-status" class="login-input">${filterOptions}</select></div>
      </div>
      <button type="button" class="btn-primary btn-wide" data-expense-new>Nova despesa</button>
    </div>
    ${visible.length ? expenseGroupsHtml(expenseGroups(visible, accountsById), accountsById) : emptyMessage}`;

  const refresh = () => refreshKeepingScroll(() => renderDespesas());
  const lookups = {accounts, counterparties};
  const content = document.getElementById('content');
  document.getElementById('despesas-status').addEventListener('change', (ev) => {
    filters.status = ev.target.value;
    refresh();
  });
  const search = document.getElementById('despesas-q');
  if (filters.focusSearch) {
    search.focus();
    search.setSelectionRange(search.value.length, search.value.length);
    filters.focusSearch = false;
  }
  let searchTimer;
  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      Object.assign(filters, {q: search.value.trim(), focusSearch: true});
      refresh();
    }, 400);
  });
  content.querySelectorAll('[data-expense-new]').forEach((button) => button.addEventListener('click', () =>
    openExpenseForm(null, Object.assign({trigger: button, onSaved: refresh}, lookups))));
  const entriesById = Object.fromEntries(entries.map((e) => [String(e.id), e]));
  content.querySelectorAll('[data-stone-auto-toggle]').forEach((button) => button.addEventListener('click', () => {
    const open = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(open));
    button.textContent = open ? 'Recolher' : 'Ver dia a dia';
    content.querySelectorAll(`[data-stone-group="${CSS.escape(button.dataset.stoneAutoToggle)}"]`).forEach((row) => { row.hidden = !open; });
  }));
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
const OBLIGATION_QUICK_FILTERS = [
  ['todas', 'Todas'], ['vencidas', 'Vencidas'], ['semana', 'Vencem em 7 dias'], ['mes', 'Próximos 30 dias'], ['emprestimos', 'Empréstimos'],
];
const OBLIGATION_BUCKETS = [
  ['overdue', 'Vencidas'], ['today', 'Vence hoje'], ['week', 'Próximos 7 dias'], ['month', 'Próximos 30 dias'], ['later', 'Depois'],
];

function isoAddDays(iso, days) {
  const d = new Date(`${String(iso).slice(0, 10)}T12:00:00`);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// Fallback when the summary (which carries the store's civil date) could not load.
function localTodayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// Calendar days from today: what the partner reads first ("venceu há 5 dias").
function dueInfo(dueDate, today) {
  const days = Math.round((Date.parse(`${String(dueDate).slice(0, 10)}T12:00:00`) - Date.parse(`${today}T12:00:00`)) / 86400000);
  const bucket = days < 0 ? 'overdue' : days === 0 ? 'today' : days <= 7 ? 'week' : days <= 30 ? 'month' : 'later';
  const text = days < -1 ? `venceu há ${-days} dias` : days === -1 ? 'venceu ontem' : days === 0 ? 'vence hoje'
    : days === 1 ? 'vence amanhã' : `vence em ${days} dias`;
  return {days, bucket, text};
}

function groupObligations(items, today) {
  return OBLIGATION_BUCKETS.map(([bucket, label]) => {
    const list = items.filter((item) => dueInfo(item.due_date, today).bucket === bucket);
    return {bucket, label, items: list, cents: list.reduce((sum, item) => sum + (item.open_cents || 0), 0)};
  }).filter((group) => group.items.length);
}

// Does the cash on hand pay what is overdue plus what falls due in the next 7 days?
function coverageMessage(summary) {
  if (!summary || summary.cash_balance_cents == null || !summary.coverage) {
    return {tone: 'none', text: 'Cadastre as contas de caixa em Fluxo de caixa para ver se o saldo cobre as contas da semana.'};
  }
  const balance = summary.cash_balance_cents;
  const {due_cents: due, shortfall_cents: shortfall} = summary.coverage;
  if (!due) return {tone: 'ok', text: `Saldo em caixa de ${money(balance)} e nada vencido ou vencendo nos próximos 7 dias.`};
  if (!shortfall) {
    return {tone: 'ok', text: `O saldo em caixa (${money(balance)}) cobre as contas vencidas e as dos próximos 7 dias (${money(due)}).`};
  }
  return {tone: 'short', text: `Faltam ${money(shortfall)} no caixa para pagar as contas vencidas e as dos próximos 7 dias (${money(due)}; saldo de ${money(balance)}).`};
}

function quickFilter(quick, today) {
  const filters = {quick, kind: '', status: '', due_from: '', due_to: ''};
  if (quick === 'vencidas') filters.status = 'overdue';
  if (quick === 'semana') Object.assign(filters, {due_from: today, due_to: isoAddDays(today, 7)});
  if (quick === 'mes') Object.assign(filters, {due_from: today, due_to: isoAddDays(today, 30)});
  if (quick === 'emprestimos') filters.kind = 'loan_installment';
  return filters;
}

function buildForecastConfirm(forecast) {
  return {
    method: 'POST', path: `/api/companies/${APP.company}/finance/entries/${forecast.id}/confirm`,
    body: {expected_version: forecast.version},
  };
}

function monthShift(monthISO, delta) {
  const [year, month] = String(monthISO).split('-').map(Number);
  const total = year * 12 + (month - 1) + delta;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, '0')}`;
}

// Sunday-to-Saturday weeks covering the whole month, so the calendar never cuts a week.
function calendarWeeks(monthISO, today) {
  const [year, month] = String(monthISO).split('-').map(Number);
  const first = new Date(year, month - 1, 1);
  const last = new Date(year, month, 0);
  const start = new Date(year, month - 1, 1 - first.getDay());
  const end = new Date(year, month - 1, last.getDate() + (6 - last.getDay()));
  const weeks = [];
  for (const day = new Date(start); day <= end; day.setDate(day.getDate() + 1)) {
    const iso = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`;
    if (!weeks.length || weeks[weeks.length - 1].length === 7) weeks.push([]);
    weeks[weeks.length - 1].push({iso, day: day.getDate(), inMonth: day.getMonth() === month - 1, isToday: iso === today});
  }
  return weeks;
}

function dayTotals(items) {
  const totals = {};
  (items || []).forEach((item) => {
    const iso = String(item.due_date).slice(0, 10);
    const slot = totals[iso] || (totals[iso] = {cents: 0, count: 0});
    slot.cents += item.open_cents || 0;
    slot.count += 1;
  });
  return totals;
}

/* A loan installment needs its principal/interest split, and a bill on direct debit
 * already leaves the account by itself — neither belongs in a batch payment. */
function selectableObligations(items) {
  return (items || []).filter((item) => item.kind === 'entry' && (item.allowed_actions || []).includes('pay')
    && item.payment_method !== 'debito_automatico');
}

function selectionSummary(items, selectedKeys) {
  const keys = new Set((selectedKeys || []).map(String));
  const chosen = (items || []).filter((item) => keys.has(String(item.key)));
  return {count: chosen.length, cents: chosen.reduce((sum, item) => sum + (item.open_cents || 0), 0)};
}

// One request per bill, each for its own open balance — the same body the single payment form sends.
function buildBatchPayments(items, selectedKeys, values) {
  const keys = new Set((selectedKeys || []).map(String));
  const chosen = selectableObligations(items).filter((item) => keys.has(String(item.key)));
  if (!chosen.length) return {errors: {form: 'Selecione ao menos uma conta para pagar.'}};
  const requests = [];
  const errors = {};
  chosen.forEach((item) => {
    const request = buildPaymentRequest(item, {
      amount: centsToMoneyInput(item.open_cents), paid_at: values.paid_at,
      cash_mode: 'generate', cash_account_id: values.cash_account_id,
    });
    if (request.errors) Object.assign(errors, request.errors);
    else requests.push({key: item.key, description: item.description, path: request.path, body: request.body});
  });
  if (Object.keys(errors).length) return {errors};
  return {requests};
}

function calendarHtml(monthISO, today, items) {
  const totals = dayTotals(items);
  const [year, month] = monthISO.split('-').map(Number);
  const weekdays = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'];
  const cells = calendarWeeks(monthISO, today).flat().map((cell) => {
    const total = totals[cell.iso];
    const late = total && cell.iso < today;
    return `<button type="button" class="calendar-day${cell.inMonth ? '' : ' out'}${cell.isToday ? ' today' : ''}${late ? ' late' : ''}"
      data-calendar-day="${cell.iso}"${total ? '' : ' disabled'}>
      <span class="calendar-number">${cell.day}</span>
      ${total ? `<span class="calendar-total">${money(total.cents)}</span><span class="calendar-count">${total.count} ${total.count === 1 ? 'conta' : 'contas'}</span>` : ''}
    </button>`;
  }).join('');
  return `
    <div class="calendar-card">
      <div class="calendar-head">
        <button type="button" class="btn-secondary btn-compact" data-calendar-month="-1" aria-label="Mês anterior">‹</button>
        <strong>${MONTHS[month - 1]}/${year}</strong>
        <button type="button" class="btn-secondary btn-compact" data-calendar-month="1" aria-label="Próximo mês">›</button>
      </div>
      <div class="calendar-grid" role="group" aria-label="Vencimentos de ${MONTHS[month - 1]}/${year}">
        ${weekdays.map((day) => `<span class="calendar-weekday">${day}</span>`).join('')}
        ${cells}
      </div>
      <p class="field-help">Clique num dia para ver as contas que vencem nele.</p>
    </div>`;
}

function obligationQuery(filters, cursor, limit) {
  const params = new URLSearchParams({limit: String(limit || 50)});
  ['kind', 'status', 'q', 'due_from', 'due_to'].forEach((key) => { if (filters[key]) params.set(key, filters[key]); });
  if (cursor) params.set('cursor', cursor);
  return `/api/companies/${APP.company}/finance/obligations?${params}`;
}

function obligationRow(item, today) {
  const due = dueInfo(item.due_date, today);
  const urgent = due.bucket === 'overdue' || due.bucket === 'today';
  const actions = [];
  if (item.allowed_actions.includes('pay')) {
    actions.push(`<button type="button" class="${urgent ? 'btn-primary' : 'btn-secondary'} btn-compact" data-obligation-pay="${esc(item.key)}" aria-label="Pagar: ${esc(item.description)}">Pagar</button>`);
  }
  actions.push(`<button type="button" class="btn-secondary btn-compact" data-obligation-details="${esc(item.key)}" aria-label="Detalhes: ${esc(item.description)}">Detalhes</button>`);
  const tag = item.kind === 'loan_installment' ? '<span class="payables-tag">Empréstimo</span>'
    : item.payment_method === 'debito_automatico' ? '<span class="payables-tag">Débito automático</span>'
      : item.count > 1 ? `<span class="payables-tag">Parcela ${esc(item.number)}/${esc(item.count)}</span>` : '';
  const partial = item.paid_cents > 0 ? `<div class="muted">Pago ${money(item.paid_cents)} de ${money(item.total_cents)}</div>` : '';
  // Same rule as the batch itself, so a bill that cannot be batch-paid shows no checkbox.
  const selectable = selectableObligations([item]).length > 0;
  return `
    <tr>
      <td class="payables-check-cell">${selectable
        ? `<input type="checkbox" class="payables-check" data-payables-select="${esc(item.key)}" aria-label="Selecionar ${esc(item.description)}">`
        : '<span class="visually-hidden">Esta conta é paga uma a uma</span>'}</td>
      <td><div class="payables-desc">${esc(item.description)}${tag}</div>${partial}</td>
      <td><div class="${due.bucket === 'overdue' ? 'due-late' : due.bucket === 'today' ? 'due-today' : ''}">${due.text}</div>
        <div class="muted">${dateBR(item.due_date)}</div></td>
      <td class="num"><strong>${money(item.open_cents)}</strong></td>
      <td><div class="row-actions">${actions.join('')}</div></td>
    </tr>`;
}

function payablesCardsHtml(summary, filters, page) {
  if (!summary) return `<div class="kpi-grid kpi-grid-3 mt-16">${kpi('Saldo em aberto', money(page.open_cents))}${kpi('Contas', page.total)}</div>`;
  const b = summary.buckets;
  const plural = (n) => `${n} ${n === 1 ? 'conta' : 'contas'}`;
  const card = (quick, label, cents, sub, alert) => `<button type="button" class="action-stat${alert ? ' alert' : ''}" data-quick-filter="${quick}" aria-pressed="${filters.quick === quick}">
      <span class="action-stat-value">${money(cents)}</span><span class="action-stat-label">${label}</span><span class="action-stat-sub">${sub}</span></button>`;
  const weekCount = b.today.count + b.week.count;
  return `<div class="actions-stats payables-cards">
    ${card('vencidas', 'Vencidas', b.overdue.cents, b.overdue.count ? plural(b.overdue.count) : 'nenhuma', b.overdue.count > 0)}
    ${card('semana', 'Vencem em 7 dias', b.today.cents + b.week.cents, weekCount ? `${plural(weekCount)}${b.today.count ? ` · ${b.today.count} hoje` : ''}` : 'nenhuma', false)}
    ${card('mes', 'Próximos 30 dias', b.today.cents + b.week.cents + b.month.cents, plural(b.today.count + b.week.count + b.month.count), false)}
    ${card('todas', 'Total em aberto', summary.open_cents, plural(summary.count), false)}
  </div>`;
}

/* Os próximos 30 dias resumidos: o que entra (inclusive o que a Stone ainda vai
 * depositar), o que sai, e o pior dia. O pior dia é a pergunta real do sócio — em que dia
 * o dinheiro acaba — e não quanto sobra no fim do mês, que esconde um vale no meio. */
function cashOutlook(projection) {
  const days = (projection && projection.days) || [];
  if (!days.length) return null;
  let incoming = 0;
  let outgoing = 0;
  days.forEach((day) => day.items.forEach((item) => {
    if (item.amount_cents >= 0) incoming += item.amount_cents;
    else outgoing += item.amount_cents;
  }));
  const stone = days.some((day) => day.items.some((item) => item.source === 'stone_receivables'));
  const lowest = projection.lowest || null;
  // O horizonte pedido, não a quantidade de linhas: a projeção devolve um objeto por dia,
  // extremos incluídos, então contar linhas diria "31 dias" para uma janela de 30.
  const horizon = projection.start && projection.end
    ? Math.round((Date.parse(projection.end) - Date.parse(projection.start)) / 86400000)
    : days.length;
  return {
    horizon_days: horizon,
    incoming_cents: incoming,
    outgoing_cents: outgoing,
    final_cents: days[days.length - 1].balance_cents,
    lowest,
    negative: !!(lowest && lowest.balance_cents < 0),
    hasStone: stone,
  };
}

function outlookHtml(outlook) {
  if (!outlook) return '';
  // Math.abs, não -x: JS tem zero negativo, e money(-0) imprime "-R$ 0,00" num período sem saídas.
  const outflowCents = Math.abs(outlook.outgoing_cents);
  const figure = (label, value, extra) =>
    `<div class="outlook-figure${extra ? ` ${extra}` : ''}"><span>${label}</span><strong>${value}</strong></div>`;
  const warning = outlook.negative
    ? `<p class="outlook-alert">${icon('triangle-alert')} <span>O caixa fica negativo em ${dateBR(outlook.lowest.date)}:
        ${money(outlook.lowest.balance_cents)}. Antecipe um recebimento ou negocie um vencimento antes dessa data.</span></p>`
    : `<p class="outlook-foot">Menor saldo no período: ${outlook.lowest ? `${money(outlook.lowest.balance_cents)} em ${dateBR(outlook.lowest.date)}` : '—'}.</p>`;
  return `
    <section class="outlook-card" aria-labelledby="outlook-title">
      <div class="forecast-head"><h2 id="outlook-title">Próximos ${outlook.horizon_days} ${outlook.horizon_days === 1 ? 'dia' : 'dias'}</h2>
        <span>${outlook.hasStone ? 'Inclui o que a Stone ainda vai depositar' : 'Sem recebíveis de cartão no período'}</span></div>
      <div class="outlook-grid">
        ${figure('Entra', money(outlook.incoming_cents))}
        ${figure('Sai', money(outflowCents))}
        ${figure('Saldo no fim', money(outlook.final_cents), outlook.final_cents < 0 ? 'bad' : '')}
      </div>
      ${warning}
    </section>`;
}

/* Contas que já parecem ter saído da conta: um débito do extrato bate com o saldo em
 * aberto e com a data. Só aparece o par sem ambiguidade, e quem confirma é o sócio. */
function suggestionsHtml(suggestions) {
  const items = suggestions || [];
  if (!items.length) return '';
  return `
    <section class="suggestion-card" aria-labelledby="suggestion-title">
      <div class="forecast-head"><h2 id="suggestion-title">Parece que já foram pagas</h2>
        <span>${items.length} ${items.length === 1 ? 'conta' : 'contas'}</span></div>
      <p class="forecast-lead">Achamos no caixa uma saída do mesmo valor, perto do vencimento. Confira e dê a baixa — nada é quitado sem você confirmar.</p>
      <ul class="forecast-list">${items.map((item) => `
        <li><div><strong>${esc(item.obligation.description)}</strong>
            <span>Vencia em ${dateBR(item.obligation.due_date)} · saída de ${money(-item.cash_event.amount_cents)} em ${dateBR(item.cash_event.occurred_at)}${item.cash_event.description ? ` · ${esc(item.cash_event.description)}` : ''}</span></div>
          <span class="num">${money(item.obligation.open_cents)}</span>
          <button type="button" class="btn-secondary btn-compact" data-suggestion-pay="${esc(item.obligation.id)}"
            aria-label="Dar baixa: ${esc(item.obligation.description)}">Dar baixa</button></li>`).join('')}
      </ul>
    </section>`;
}

function forecastsHtml(summary, today) {
  const forecasts = (summary && summary.forecasts) || [];
  if (!forecasts.length) return '';
  return `
    <section class="forecast-card" aria-labelledby="forecast-title">
      <div class="forecast-head"><h2 id="forecast-title">Previstas para confirmar</h2>
        <span>${forecasts.length} ${forecasts.length === 1 ? 'despesa' : 'despesas'} · ${money(summary.forecast_cents)}</span></div>
      <p class="forecast-lead">Despesas recorrentes até 30 dias. Confirme quando a conta chegar: só então ela entra no resultado e na lista para pagar.</p>
      <ul class="forecast-list">${forecasts.map((forecast) => `
        <li><div><strong>${esc(forecast.description)}</strong><span>${dueInfo(forecast.due_date, today).text} · ${dateBR(forecast.due_date)}</span></div>
          <span class="num">${money(forecast.amount_cents)}</span>
          <button type="button" class="btn-secondary btn-compact" data-forecast-confirm="${esc(forecast.id)}" aria-label="Confirmar: ${esc(forecast.description)}">Confirmar</button></li>`).join('')}
      </ul>
      <p class="form-error" data-forecast-error role="alert"></p>
      <p class="forecast-foot">Valor diferente este mês? <a href="${routeHash('despesas', today.slice(0, 7))}">Ajuste em Despesas do mês →</a></p>
    </section>`;
}

async function renderContasPagar(token) {
  token = token || beginPage();
  const title = `${icon('calendar', {class: 'title-icon'})}Contas a pagar`;
  const subtitle = 'O que precisa ser pago, do mais urgente ao mais distante — de qualquer competência.';
  const routeKind = APP.routeParams && APP.routeParams.get('tipo') === 'emprestimo' ? 'loan_installment' : '';
  const filters = financeFilters('contas-pagar', {quick: 'todas', kind: routeKind, status: '', q: '', due_from: '', due_to: '',
    view: 'lista', month: localTodayISO().slice(0, 7)});
  if (routeKind) Object.assign(filters, {quick: 'emprestimos', kind: routeKind});
  financeLoading(title, subtitle, 'Carregando contas a pagar');
  let page, positions = null, summary = null, projection = null, suggestions = null;
  try {
    // The loan filter also shows the contracts (ficha, renegotiation), which
    // used to live on their own Empréstimos page.
    const horizonStart = localTodayISO();
    [page, positions, summary, projection, suggestions] = await Promise.all([
      api(obligationQuery(filters)),
      filters.kind === 'loan_installment' ? api(`/api/companies/${APP.company}/finance/loans`) : Promise.resolve(null),
      // The urgency summary is a guide on top of the list: without it the list still works.
      api(`/api/companies/${APP.company}/finance/obligations/summary`).catch(() => null),
      // Same rule for the next two: they add context and a shortcut on top of the list,
      // so a failure in either leaves the page whole instead of taking it down.
      api(`/api/companies/${APP.company}/finance/forecast?start=${horizonStart}&end=${isoAddDays(horizonStart, 30)}&scenario=base`).catch(() => null),
      api(`/api/companies/${APP.company}/finance/obligations/payment-suggestions`).catch(() => null),
    ]);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;
    return financeError(title, subtitle, e, () => renderContasPagar());
  }
  if (!APP.pageState.isCurrent(token)) return;

  const today = (summary && summary.today) || localTodayISO();
  const items = page.items.slice();
  const hasFilter = filters.quick !== 'todas' || filters.q;
  const coverage = summary ? coverageMessage(summary) : null;
  const chips = OBLIGATION_QUICK_FILTERS.map(([value, label]) =>
    `<button type="button" class="payables-chip" data-quick-filter="${value}" aria-pressed="${filters.quick === value}">${label}</button>`).join('');
  const statusOptions = OBLIGATION_STATUS_FILTERS
    .map(([value, label]) => `<option value="${value}"${value === filters.status ? ' selected' : ''}>${label}</option>`).join('');

  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <div class="btn-row">
      <button type="button" class="btn-primary btn-wide" data-expense-new>Nova despesa</button>
      <button type="button" class="btn-secondary" data-loan-new>Novo empréstimo</button>
    </div>
    ${payablesCardsHtml(summary, filters, page)}
    ${coverage ? `<p class="payables-coverage ${coverage.tone}" role="status">${icon(coverage.tone === 'short' ? 'triangle-alert' : coverage.tone === 'ok' ? 'circle-check' : 'lightbulb')}
      <span>${esc(coverage.text)}</span>${coverage.tone === 'ok' ? '' : ` <a href="${routeHash('fluxo-caixa')}">Abrir Fluxo de caixa →</a>`}</p>` : ''}
    ${suggestionsHtml(suggestions)}
    ${outlookHtml(cashOutlook(projection))}
    ${forecastsHtml(summary, today)}
    <div class="payables-toolbar">
      <div class="payables-views" role="group" aria-label="Como ver as contas">
        <button type="button" class="payables-chip" data-payables-view="lista" aria-pressed="${filters.view !== 'calendario'}">Lista</button>
        <button type="button" class="payables-chip" data-payables-view="calendario" aria-pressed="${filters.view === 'calendario'}">Calendário</button>
      </div>
      <div class="payables-chips" role="group" aria-label="Filtro rápido">${chips}</div>
      <label class="visually-hidden" for="obligations-q">Buscar</label>
      <input id="obligations-q" type="search" class="login-input payables-search" maxlength="120" value="${esc(filters.q)}" placeholder="Buscar por descrição ou credor">
      ${hasFilter ? '<button type="button" class="btn-link" data-obligations-clear>Limpar filtros</button>' : ''}
      <details class="payables-more"${filters.quick === 'custom' ? ' open' : ''}>
        <summary>Mais filtros</summary>
        <form id="obligations-filter" class="payables-more-form">
          <div><label class="field-label" for="obligations-status">Situação</label>
            <select id="obligations-status" name="status" class="login-input">${statusOptions}</select></div>
          <div><label class="field-label" for="obligations-due-from">Vence de</label>
            <input id="obligations-due-from" name="due_from" type="date" class="login-input" value="${esc(filters.due_from)}"></div>
          <div><label class="field-label" for="obligations-due-to">até</label>
            <input id="obligations-due-to" name="due_to" type="date" class="login-input" value="${esc(filters.due_to)}"></div>
          <button type="submit" class="btn-secondary">Aplicar</button>
        </form>
      </details>
    </div>
    <p class="actions-status" id="payables-status" role="status" aria-live="polite"></p>
    <div class="payables-batch" id="payables-batch" hidden></div>
    <div id="obligations-list"></div>
    ${positions ? `<h2 class="section-header">Contratos de empréstimo</h2>${loanContractsHtml(positions)}` : ''}`;

  const refresh = () => refreshKeepingScroll(() => renderContasPagar());
  const content = document.getElementById('content');
  const list = document.getElementById('obligations-list');
  let nextCursor = page.next_cursor;
  const selected = new Set();
  const paint = () => {
    if (filters.view === 'calendario') return paintCalendar(filters, today, list);
    if (!items.length) {
      list.innerHTML = hasFilter
        ? '<div class="empty-state">Nenhuma conta com estes filtros.</div>'
        : '<div class="empty-state">Nenhuma conta em aberto. Novas despesas entram aqui quando você as lança em Despesas do mês.</div>';
      return;
    }
    list.innerHTML = `
      <div class="table-wrap"><table class="data-table payables-table">
        <thead><tr><th><span class="visually-hidden">Selecionar</span></th><th>Conta</th><th>Vencimento</th>
          <th class="num">Saldo a pagar</th><th><span class="visually-hidden">Ações</span></th></tr></thead>
        ${groupObligations(items, today).map((group) => `<tbody class="payables-group ${group.bucket}">
          <tr class="payables-group-row"><th colspan="5" scope="rowgroup">${group.label}
            <span>${group.items.length} ${group.items.length === 1 ? 'conta' : 'contas'} · ${money(group.cents)}</span></th></tr>
          ${group.items.map((item) => obligationRow(item, today)).join('')}</tbody>`).join('')}
      </table></div>
      <p class="muted">Exibindo ${items.length} de ${page.total}.</p>
      ${nextCursor ? '<div class="btn-row"><button type="button" class="btn-secondary" data-obligations-more>Carregar mais</button></div>' : ''}`;
    const byKey = Object.fromEntries(items.map((item) => [item.key, item]));
    list.querySelectorAll('[data-payables-select]').forEach((box) => {
      box.checked = selected.has(box.dataset.payablesSelect);
      box.addEventListener('change', () => {
        if (box.checked) selected.add(box.dataset.payablesSelect); else selected.delete(box.dataset.payablesSelect);
        paintBatchBar(items, selected, refresh);
      });
    });
    paintBatchBar(items, selected, refresh);
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

  content.querySelectorAll('[data-expense-new]').forEach((button) =>
    button.addEventListener('click', () => openExpenseForm(null, {trigger: button, onSaved: refresh})));
  content.querySelectorAll('[data-loan-new]').forEach((button) =>
    button.addEventListener('click', () => openLoanForm(null, {trigger: button, onSaved: refresh})));
  if (positions) bindLoanCards(content, positions, refresh);

  // One click filters: the cards and the chips share the same quick filters.
  content.querySelectorAll('[data-quick-filter]').forEach((button) => button.addEventListener('click', () => {
    Object.assign(filters, quickFilter(button.dataset.quickFilter, today));
    renderContasPagar();
  }));
  content.querySelectorAll('[data-payables-view]').forEach((button) => button.addEventListener('click', () => {
    filters.view = button.dataset.payablesView;
    renderContasPagar();
  }));
  const search = document.getElementById('obligations-q');
  if (filters.focusSearch) {
    search.focus();
    search.setSelectionRange(search.value.length, search.value.length);
    filters.focusSearch = false;
  }
  let searchTimer;
  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      Object.assign(filters, {q: search.value.trim(), focusSearch: true});
      renderContasPagar();
    }, 400);
  });
  document.getElementById('obligations-filter').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const form = ev.currentTarget;
    Object.assign(filters, {quick: 'custom', kind: ''});
    ['status', 'due_from', 'due_to'].forEach((key) => { filters[key] = form.elements.namedItem(key).value.trim(); });
    renderContasPagar();
  });
  const clear = content.querySelector('[data-obligations-clear]');
  if (clear) clear.addEventListener('click', () => {
    Object.assign(filters, quickFilter('todas', today), {q: ''});
    renderContasPagar();
  });
  // A baixa sugerida não quita nada sozinha: abre a gaveta de sempre, já vinculada ao
  // movimento, para o sócio conferir e confirmar.
  content.querySelectorAll('[data-suggestion-pay]').forEach((button) => button.addEventListener('click', () => {
    const item = (suggestions || []).find((one) => String(one.obligation.id) === button.dataset.suggestionPay);
    if (!item) return;
    openPaymentForm(item.obligation, {trigger: button, linkCashEvent: item.cash_event, onSaved: refresh});
  }));
  content.querySelectorAll('[data-forecast-confirm]').forEach((button) => button.addEventListener('click', async () => {
    const forecast = summary.forecasts.find((item) => String(item.id) === button.dataset.forecastConfirm);
    const request = buildForecastConfirm(forecast);
    button.disabled = true;
    button.textContent = 'Confirmando…';
    try {
      await api(request.path, {method: request.method, body: JSON.stringify(request.body)});
      refresh();
    } catch (e) {
      button.disabled = false;
      button.textContent = 'Confirmar';
      content.querySelector('[data-forecast-error]').textContent = 'Não foi possível confirmar: ' + e.message;
    }
  }));
}

/* The calendar reads the whole month at once (never the filtered page), so a day
 * total is the day total — the list filters stay untouched underneath. */
async function paintCalendar(filters, today, list) {
  const month = filters.month;
  const monthQuery = {kind: filters.kind, status: '', q: '', due_from: `${month}-01`, due_to: `${monthShift(month, 1)}-01`};
  list.innerHTML = '<div class="skeleton-block" aria-label="Carregando calendário"></div>';
  let page;
  try {
    page = await api(obligationQuery(monthQuery, null, 200));
  } catch (e) {
    list.innerHTML = `<div class="story-box">Não foi possível carregar o mês: ${esc(e.message)}</div>`;
    return;
  }
  const items = page.items.filter((item) => String(item.due_date).slice(0, 7) === month);
  list.innerHTML = calendarHtml(month, today, items);
  list.querySelectorAll('[data-calendar-month]').forEach((button) => button.addEventListener('click', () => {
    filters.month = monthShift(month, Number(button.dataset.calendarMonth));
    renderContasPagar();
  }));
  list.querySelectorAll('[data-calendar-day]').forEach((button) => button.addEventListener('click', () => {
    const day = button.dataset.calendarDay;
    Object.assign(filters, {view: 'lista', quick: 'custom', status: '', due_from: day, due_to: day});
    renderContasPagar();
  }));
}

function paintBatchBar(items, selected, onPaid) {
  const bar = document.getElementById('payables-batch');
  if (!bar) return;
  const summary = selectionSummary(items, Array.from(selected));
  bar.hidden = !summary.count;
  if (!summary.count) return;
  bar.innerHTML = `<span><strong>${summary.count} ${summary.count === 1 ? 'conta' : 'contas'}</strong> · ${money(summary.cents)}</span>
    <button type="button" class="btn-primary btn-compact" data-batch-pay>Pagar selecionadas</button>
    <button type="button" class="btn-link" data-batch-clear>Limpar seleção</button>`;
  bar.querySelector('[data-batch-clear]').addEventListener('click', () => {
    selected.clear();
    document.querySelectorAll('[data-payables-select]').forEach((box) => { box.checked = false; });
    paintBatchBar(items, selected, onPaid);
  });
  bar.querySelector('[data-batch-pay]').addEventListener('click', (event) =>
    openBatchPaymentDrawer(items, Array.from(selected), {trigger: event.currentTarget, onPaid}));
}

/* Each bill keeps its own payment (its own value, version and Idempotency-Key):
 * one failing bill never blocks the others, and the result says what happened. */
async function openBatchPaymentDrawer(items, keys, ctx) {
  const chosen = selectableObligations(items).filter((item) => keys.includes(String(item.key)));
  const drawer = openDrawer({title: 'Pagar contas selecionadas', trigger: ctx.trigger,
    body: '<div class="skeleton-block" aria-label="Carregando contas de caixa"></div>'});
  let lookups;
  try {
    lookups = await financeLookups(['cashAccounts']);
  } catch (e) {
    drawer.setBody(`<p class="form-error" role="alert">Não foi possível carregar as contas de caixa: ${esc(e.message)}</p>`);
    return;
  }
  const accounts = lookups.cashAccounts.filter((account) => !account.archived);
  const total = chosen.reduce((sum, item) => sum + item.open_cents, 0);
  drawer.setBody(`
    <form class="drawer-form action-form" novalidate>
      <p class="action-form-lead">${chosen.length} ${chosen.length === 1 ? 'conta' : 'contas'} · ${money(total)}</p>
      <ul class="batch-list">${chosen.map((item) => `<li><span>${esc(item.description)}</span><span class="num">${money(item.open_cents)}</span></li>`).join('')}</ul>
      <label for="batch-paid-at">Data do pagamento</label>
      <input id="batch-paid-at" name="paid_at" type="date" class="login-input" value="${esc(localTodayISO())}">
      <label for="batch-account">Conta de onde sai o dinheiro</label>
      <select id="batch-account" name="cash_account_id" class="login-input">
        <option value="">Escolha…</option>
        ${accounts.map((account) => `<option value="${esc(account.id)}">${esc(account.name)}</option>`).join('')}
      </select>
      <p class="field-help">Uma saída é lançada no fluxo de caixa para cada conta paga.</p>
      <p class="form-error" role="alert" data-form-error></p>
      <div class="drawer-actions"><button type="button" class="btn-secondary" data-drawer-close>Cancelar</button>
        <button type="submit" class="btn-primary">Pagar ${chosen.length} ${chosen.length === 1 ? 'conta' : 'contas'}</button></div>
    </form>`);
  const form = drawer.dialog.querySelector('form');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const error = form.querySelector('[data-form-error]');
    const button = form.querySelector('[type="submit"]');
    const batch = buildBatchPayments(items, keys, {
      paid_at: form.elements.paid_at.value, cash_account_id: form.elements.cash_account_id.value,
    });
    if (batch.errors) {
      error.textContent = Object.values(batch.errors)[0];
      return;
    }
    button.disabled = true;
    const done = [];
    const failed = [];
    for (const request of batch.requests) {
      button.textContent = `Pagando ${done.length + failed.length + 1} de ${batch.requests.length}…`;
      try {
        await api(request.path, {method: 'POST', body: JSON.stringify(request.body),
          headers: {'Idempotency-Key': newIdempotencyKey()}});
        done.push(request);
      } catch (e) {
        failed.push(`${request.description}: ${e.message}`);
      }
    }
    drawer.close();
    // The refresh replaces the page (and this status line), so it has to run first.
    if (ctx.onPaid) await ctx.onPaid();
    const status = document.getElementById('payables-status');
    if (status) {
      status.textContent = failed.length
        ? `${done.length} de ${batch.requests.length} contas pagas. Não deu certo em: ${failed.join(' · ')}`
        : `${done.length} ${done.length === 1 ? 'conta paga' : 'contas pagas'}.`;
      status.classList.toggle('error', failed.length > 0);
    }
  });
}

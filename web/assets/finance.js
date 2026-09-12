/* Financeiro, Custos e Despesas e Contas a Pagar. Loaded before app.js and
 * uses its shared api(), esc(), money(), APP and MONTHS globals only when a
 * route opens (same convention as settings.js). */
'use strict';

const FINANCE_SOURCE_LABELS = {
  manual: 'Lançamento manual',
  mobne: 'Mobne',
  multiple: 'Múltiplas origens',
  budget: 'Somente orçado',
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

function financePeriodLabel(period) {
  const [y, m] = (period || '').split('-').map(Number);
  return m ? `${MONTHS[m - 1]}/${y}` : '—';
}

function dateBR(iso) {
  if (!iso) return '—';
  const [y, m, d] = iso.split('-');
  return `${d}/${m}/${y}`;
}

function financeError(title, subtitle, error) {
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <div class="story-box mt-16">Não foi possível carregar: ${esc(error.message)}</div>`;
}

function renderFinancePage() {
  const renderers = {
    financeiro: renderFinanceiro, despesas: renderDespesas, 'contas-pagar': renderContasPagar,
    emprestimos: renderEmprestimos, 'fluxo-caixa': renderFluxoCaixa, conciliacao: renderConciliacao,
  };
  return (renderers[APP.page] || renderFinanceiro)();
}

/* ---------------------------------------------------------------- Financeiro */

async function renderFinanceiro() {
  const title = '💵 Financeiro';
  const subtitle = 'Resultado gerencial da competência: receita, custos, despesas e distribuições.';
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <div class="skeleton-block" aria-label="Carregando resultado gerencial"></div>`;
  let result;
  try {
    result = await api(`/api/companies/${APP.company}/finance/management-result?period=${APP.period}`);
  } catch (e) {
    return financeError(title, subtitle, e);
  }
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
      <td>${money(line.actual_cents)}</td>
      <td>${line.budget_cents == null ? '—' : money(line.budget_cents)}</td>
      <td>${line.variance_cents == null ? '—' : money(line.variance_cents)}</td>
      <td>${esc(FINANCE_SOURCE_LABELS[line.source] || line.source)}</td>
    </tr>`).join('');
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <span class="periodo-badge">📅 Competência: ${financePeriodLabel(APP.period)}</span>
    <hr class="divider">
    <div class="kpi-grid kpi-grid-4">${kpis}</div>
    <div class="section-header">Contas — Realizado vs. Orçado</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Conta</th><th>Realizado</th><th>Orçado</th><th>Variação</th><th>Origem</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5">Nenhum lançamento nesta competência.</td></tr>'}</tbody>
    </table></div>`;
}

/* ---------------------------------------------------------------- Custos e Despesas */

async function renderDespesas() {
  const title = '🧾 Custos e Despesas';
  const subtitle = 'Lançamentos de despesas por competência.';
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <div class="skeleton-block" aria-label="Carregando despesas"></div>`;
  let accounts, entries;
  try {
    [accounts, entries] = await Promise.all([
      api(`/api/companies/${APP.company}/finance/accounts`),
      api(`/api/companies/${APP.company}/finance/entries?competence=${APP.period}`),
    ]);
  } catch (e) {
    return financeError(title, subtitle, e);
  }
  const expenseAccounts = accounts.filter((a) =>
    ['operating_expense', 'financial_expense', 'tax_expense'].includes(a.nature));
  const options = expenseAccounts.map((a) => `<option value="${esc(a.id)}">${esc(a.code)} — ${esc(a.name)}</option>`).join('');
  const accountsById = Object.fromEntries(accounts.map((a) => [a.id, a]));
  const rows = entries.map((e) => `
    <tr>
      <td>${esc((accountsById[e.account_id] || {}).name || '—')}</td>
      <td>${esc(e.description)}</td>
      <td>${money(e.amount_cents)}</td>
      <td>${dateBR(e.due_date)}</td>
      <td>${esc(FINANCE_STATUS_LABELS[e.status] || e.status)}</td>
    </tr>`).join('');
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <span class="periodo-badge">📅 Competência: ${financePeriodLabel(APP.period)}</span>
    <hr class="divider">
    <div class="settings-block">
      <h2>Nova despesa</h2>
      <form id="expense-create-form" class="settings-form">
        <div class="form-grid">
          <div>
            <label for="expense-account" class="field-label">Conta</label>
            <select id="expense-account" class="login-input" required>${options}</select>
          </div>
          <div>
            <label for="expense-amount" class="field-label">Valor (R$)</label>
            <input id="expense-amount" class="login-input" type="number" min="0.01" step="0.01" required>
          </div>
          <div>
            <label for="expense-due" class="field-label">Vencimento</label>
            <input id="expense-due" class="login-input" type="date" required>
          </div>
          <div class="form-grid-wide">
            <label for="expense-description" class="field-label">Descrição</label>
            <input id="expense-description" class="login-input" maxlength="240" required>
          </div>
        </div>
        <div class="settings-actions">
          <button class="btn-primary" type="submit">Lançar despesa</button>
          <span id="expense-create-status" class="sim-status" role="status" aria-live="polite"></span>
        </div>
      </form>
    </div>
    <div class="section-header">Lançamentos de ${financePeriodLabel(APP.period)}</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Conta</th><th>Descrição</th><th>Valor</th><th>Vencimento</th><th>Status</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5">Nenhuma despesa lançada nesta competência.</td></tr>'}</tbody>
    </table></div>`;
  document.getElementById('expense-create-form').addEventListener('submit', onCreateExpense);
}

async function onCreateExpense(event) {
  event.preventDefault();
  const status = document.getElementById('expense-create-status');
  status.textContent = 'Salvando…';
  const amount = parseFloat(document.getElementById('expense-amount').value.replace(',', '.'));
  try {
    await api(`/api/companies/${APP.company}/finance/entries`, {
      method: 'POST',
      body: JSON.stringify({
        account_id: document.getElementById('expense-account').value,
        amount_cents: Math.round(amount * 100),
        competence: APP.period,
        due_date: document.getElementById('expense-due').value,
        description: document.getElementById('expense-description').value.trim(),
      }),
    });
    renderDespesas();
  } catch (e) {
    status.textContent = 'Erro: ' + e.message;
  }
}

/* ---------------------------------------------------------------- Contas a Pagar */

async function renderContasPagar() {
  const title = '📄 Contas a Pagar';
  const subtitle = 'Despesas em aberto, vencidas ou parcialmente pagas, em qualquer competência.';
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <div class="skeleton-block" aria-label="Carregando contas a pagar"></div>`;
  let accounts, entries;
  try {
    [accounts, entries] = await Promise.all([
      api(`/api/companies/${APP.company}/finance/accounts`),
      api(`/api/companies/${APP.company}/finance/entries`),
    ]);
  } catch (e) {
    return financeError(title, subtitle, e);
  }
  const accountsById = Object.fromEntries(accounts.map((a) => [a.id, a]));
  const today = new Date().toISOString().slice(0, 10);
  const pending = entries.filter((e) => ['open', 'partially_paid', 'overdue'].includes(e.status));
  const rows = pending.map((e) => {
    const late = e.due_date < today;
    return `
    <tr class="${late ? 'row-alert' : ''}">
      <td>${esc((accountsById[e.account_id] || {}).name || '—')}</td>
      <td>${esc(e.description)}</td>
      <td>${money(e.amount_cents)}</td>
      <td>${dateBR(e.due_date)}${late ? ' ⚠️' : ''}</td>
      <td>${esc(FINANCE_STATUS_LABELS[e.status] || e.status)}</td>
      <td><button type="button" class="btn-secondary" data-settle-id="${esc(e.id)}" data-settle-amount="${e.amount_cents}">Pagar</button></td>
    </tr>`;
  }).join('');
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <hr class="divider">
    <div class="table-wrap"><table>
      <thead><tr><th>Conta</th><th>Descrição</th><th>Valor</th><th>Vencimento</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6">Nenhuma conta em aberto.</td></tr>'}</tbody>
    </table></div>`;
  document.querySelectorAll('[data-settle-id]').forEach((btn) => btn.addEventListener('click', onSettleEntry));
}

async function onSettleEntry(event) {
  const btn = event.currentTarget;
  const entryId = btn.dataset.settleId;
  const amountCents = parseInt(btn.dataset.settleAmount, 10);
  btn.disabled = true;
  try {
    await api(`/api/companies/${APP.company}/finance/entries/${entryId}/settlements`, {
      method: 'POST',
      body: JSON.stringify({amount_cents: amountCents, paid_at: new Date().toISOString().slice(0, 10)}),
    });
    renderContasPagar();
  } catch (e) {
    alert('Erro ao registrar pagamento: ' + e.message);
    btn.disabled = false;
  }
}

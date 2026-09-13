/* Fluxo de Caixa: contas de caixa, saldo consolidado e projeção realizada/prevista.
 * Loaded before app.js and uses its shared api(), esc(), money(), APP globals
 * plus finance.js's dateBR()/financeError() and loans.js's addMonthsISO(). */
'use strict';

const CASHFLOW_KIND_LABELS = {bank: 'Conta bancária', payment: 'Maquininha/adquirente', cash: 'Dinheiro em caixa'};
const CASHFLOW_CONFIDENCE_LABELS = {realized: 'Realizado', forecast: 'Previsto', simulated: 'Simulado'};

const CASHFLOW_HORIZONS = [30, 60, 90];

function cashflowHorizon() {
  const filters = financeFilters('fluxo-caixa', {horizon: 90});
  return CASHFLOW_HORIZONS.includes(Number(filters.horizon)) ? Number(filters.horizon) : 90;
}

function forecastWindow(days) {
  const start = todayISO();
  const end = new Date(`${start}T00:00:00Z`);
  end.setUTCDate(end.getUTCDate() + days);
  return {start, end: end.toISOString().slice(0, 10)};
}

async function renderFluxoCaixa(token) {
  token = token || beginPage();
  const title = `${icon('trending-up', {class: 'title-icon'})}Fluxo de caixa`;
  const horizon = cashflowHorizon();
  const subtitle = `Contas, saldo consolidado e projeção dos movimentos cadastrados para os próximos ${horizon} dias.`;
  financeLoading(title, subtitle, 'Carregando fluxo de caixa');
  const {start, end} = forecastWindow(horizon);
  let accounts, balance, forecastData;
  try {
    [accounts, balance, forecastData] = await Promise.all([
      api(`/api/companies/${APP.company}/finance/cash-accounts`),
      api(`/api/companies/${APP.company}/finance/cash-balance`),
      api(`/api/companies/${APP.company}/finance/forecast?start=${start}&end=${end}&scenario=base`),
    ]);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;
    return financeError(title, subtitle, e, () => renderFluxoCaixa());
  }
  if (!APP.pageState.isCurrent(token)) return;

  const accountRows = accounts.map((a) => `
    <tr>
      <td>${esc(a.name)}</td>
      <td>${esc(CASHFLOW_KIND_LABELS[a.kind] || a.kind)}</td>
      <td class="num">${money(a.balance_cents)}</td>
    </tr>`).join('');

  const movementDays = forecastData.days.filter((d) => d.items.length);
  const movementRows = movementDays.flatMap((d) => d.items.map((item) => `
    <tr>
      <td>${dateBR(d.date)}</td>
      <td>${esc(item.description)}</td>
      <td class="num">${money(item.amount_cents)}</td>
      <td>${esc(CASHFLOW_CONFIDENCE_LABELS[item.confidence] || item.confidence)}</td>
    </tr>`)).join('');

  const alertBanner = forecastData.alerts.length
    ? `<div class="story-box mt-16">${icon('triangle-alert')} Projeção de caixa negativo em ${dateBR(forecastData.alerts[0].date)}:
        ${money(forecastData.alerts[0].balance_cents)}.</div>`
    : '';

  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <div class="page-toolbar">
      <div class="filters">
        <div><label class="field-label" for="cashflow-horizon">Horizonte da projeção</label>
          <select id="cashflow-horizon" class="login-input">${CASHFLOW_HORIZONS.map((d) => `<option value="${d}"${d === horizon ? ' selected' : ''}>${d} dias</option>`).join('')}</select></div>
      </div>
    </div>
    <div class="kpi-grid kpi-grid-3">
      ${kpi('Saldo consolidado', money(balance.balance_cents))}
      ${kpi(`Ponto mais baixo projetado (${horizon} dias)`,
        forecastData.lowest ? money(forecastData.lowest.balance_cents) : '—',
        forecastData.lowest ? `em ${dateBR(forecastData.lowest.date)}` : null,
        forecastData.lowest && forecastData.lowest.balance_cents < 0 ? 'kpi-negative' : null)}
      ${kpi('Contas de caixa', accounts.length)}
    </div>
    ${alertBanner}
    <div class="settings-block">
      <h2>Contas de caixa</h2>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th>Nome</th><th>Tipo</th><th>Saldo</th></tr></thead>
        <tbody>${accountRows || '<tr><td colspan="3">Nenhuma conta cadastrada.</td></tr>'}</tbody>
      </table></div>
      <form id="cash-account-form" class="inline-form">
        <div><label class="field-label" for="cash-account-name">Nova conta</label>
          <input id="cash-account-name" class="login-input" maxlength="180" required></div>
        <div><label class="field-label" for="cash-account-kind">Tipo</label>
          <select id="cash-account-kind" class="login-input">
            <option value="bank">Conta bancária</option>
            <option value="payment">Maquininha/adquirente</option>
            <option value="cash">Dinheiro em caixa</option>
          </select></div>
        <button class="btn-secondary" type="submit">Adicionar conta</button>
        <span id="cash-account-status" class="sim-status" role="status" aria-live="polite"></span>
      </form>
    </div>
    <div class="settings-block">
      <h2>Movimentar caixa</h2>
      <form id="cash-event-form" class="settings-form">
        <div class="form-grid">
          <div><label class="field-label" for="cash-event-account">Conta</label>
            <select id="cash-event-account" class="login-input" required>
              ${accounts.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('')}
            </select></div>
          <div><label class="field-label" for="cash-event-amount">Valor (R$, negativo para saída)</label>
            <input id="cash-event-amount" class="login-input" type="number" step="0.01" required></div>
          <div><label class="field-label" for="cash-event-date">Data</label>
            <input id="cash-event-date" class="login-input" type="date" required></div>
          <div class="form-grid-wide"><label class="field-label" for="cash-event-description">Descrição</label>
            <input id="cash-event-description" class="login-input" maxlength="240" required></div>
        </div>
        <div class="settings-actions">
          <button class="btn-primary" type="submit">Lançar movimento</button>
          <span id="cash-event-status" class="sim-status" role="status" aria-live="polite"></span>
        </div>
      </form>
    </div>
    <h2 class="section-header">Próximos ${horizon} dias — realizado e previsto</h2>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Data</th><th>Descrição</th><th>Valor</th><th>Origem</th></tr></thead>
      <tbody>${movementRows || '<tr><td colspan="4">Nenhuma movimentação prevista.</td></tr>'}</tbody>
    </table></div>`;

  document.getElementById('cashflow-horizon').addEventListener('change', (ev) => {
    financeFilters('fluxo-caixa', {horizon: 90}).horizon = Number(ev.target.value);
    renderFluxoCaixa();
  });
  watchForm(document.getElementById('cash-account-form')).addEventListener('submit', onCreateCashAccount);
  watchForm(document.getElementById('cash-event-form')).addEventListener('submit', onCreateCashEvent);
}

async function onCreateCashAccount(event) {
  event.preventDefault();
  const status = document.getElementById('cash-account-status');
  status.textContent = 'Salvando…';
  try {
    await api(`/api/companies/${APP.company}/finance/cash-accounts`, {
      method: 'POST',
      body: JSON.stringify({
        name: document.getElementById('cash-account-name').value.trim(),
        kind: document.getElementById('cash-account-kind').value,
      }),
    });
    clearDirty();
    renderFluxoCaixa();
  } catch (e) {
    status.textContent = 'Erro: ' + e.message;
  }
}

async function onCreateCashEvent(event) {
  event.preventDefault();
  const status = document.getElementById('cash-event-status');
  status.textContent = 'Salvando…';
  const amount = parseFloat(document.getElementById('cash-event-amount').value.replace(',', '.'));
  try {
    await api(`/api/companies/${APP.company}/finance/cash-events`, {
      method: 'POST',
      body: JSON.stringify({
        cash_account_id: document.getElementById('cash-event-account').value,
        amount_cents: Math.round(amount * 100),
        occurred_at: document.getElementById('cash-event-date').value,
        description: document.getElementById('cash-event-description').value.trim(),
      }),
    });
    clearDirty();
    renderFluxoCaixa();
  } catch (e) {
    status.textContent = 'Erro: ' + e.message;
  }
}

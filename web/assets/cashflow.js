/* Fluxo de Caixa: contas de caixa, saldo consolidado e projeção realizada/prevista.
 * Loaded before app.js and uses its shared api(), esc(), money(), APP globals
 * plus finance.js's dateBR()/financeError() and loans.js's addMonthsISO(). */
'use strict';

const CASHFLOW_KIND_LABELS = {bank: 'Conta bancária', payment: 'Maquininha/adquirente', cash: 'Dinheiro em caixa'};
const CASHFLOW_CONFIDENCE_LABELS = {realized: 'Realizado', forecast: 'Previsto', simulated: 'Simulado'};

function forecastWindow() {
  const start = new Date().toISOString().slice(0, 10);
  return {start, end: addMonthsISO(start, 3)};
}

async function renderFluxoCaixa() {
  const title = '📈 Fluxo de Caixa';
  const subtitle = 'Contas, saldo consolidado e projeção de caixa realizada e prevista (90 dias).';
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <div class="skeleton-block" aria-label="Carregando fluxo de caixa"></div>`;
  const {start, end} = forecastWindow();
  let accounts, balance, forecastData;
  try {
    [accounts, balance, forecastData] = await Promise.all([
      api(`/api/companies/${APP.company}/finance/cash-accounts`),
      api(`/api/companies/${APP.company}/finance/cash-balance`),
      api(`/api/companies/${APP.company}/finance/forecast?start=${start}&end=${end}&scenario=base`),
    ]);
  } catch (e) {
    return financeError(title, subtitle, e);
  }

  const accountRows = accounts.map((a) => `
    <tr>
      <td>${esc(a.name)}</td>
      <td>${esc(CASHFLOW_KIND_LABELS[a.kind] || a.kind)}</td>
      <td>${money(a.balance_cents)}</td>
    </tr>`).join('');

  const movementDays = forecastData.days.filter((d) => d.items.length);
  const movementRows = movementDays.flatMap((d) => d.items.map((item) => `
    <tr>
      <td>${dateBR(d.date)}</td>
      <td>${esc(item.description)}</td>
      <td>${money(item.amount_cents)}</td>
      <td>${esc(CASHFLOW_CONFIDENCE_LABELS[item.confidence] || item.confidence)}</td>
    </tr>`)).join('');

  const alertBanner = forecastData.alerts.length
    ? `<div class="story-box mt-16">⚠️ Projeção de caixa negativo em ${dateBR(forecastData.alerts[0].date)}:
        ${money(forecastData.alerts[0].balance_cents)}.</div>`
    : '';

  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <hr class="divider">
    <div class="kpi-grid kpi-grid-3">
      ${kpi('Saldo consolidado', money(balance.balance_cents))}
      ${kpi('Ponto mais baixo projetado (90 dias)',
        forecastData.lowest ? money(forecastData.lowest.balance_cents) : '—',
        forecastData.lowest ? `em ${dateBR(forecastData.lowest.date)}` : null,
        forecastData.lowest && forecastData.lowest.balance_cents < 0 ? 'kpi-negative' : null)}
      ${kpi('Contas de caixa', accounts.length)}
    </div>
    ${alertBanner}
    <div class="settings-block">
      <h2>Contas de caixa</h2>
      <div class="table-wrap"><table>
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
    <div class="section-header">Próximos 90 dias — realizado e previsto</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Data</th><th>Descrição</th><th>Valor</th><th>Origem</th></tr></thead>
      <tbody>${movementRows || '<tr><td colspan="4">Nenhuma movimentação prevista.</td></tr>'}</tbody>
    </table></div>`;

  document.getElementById('cash-account-form').addEventListener('submit', onCreateCashAccount);
  document.getElementById('cash-event-form').addEventListener('submit', onCreateCashEvent);
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
    renderFluxoCaixa();
  } catch (e) {
    status.textContent = 'Erro: ' + e.message;
  }
}

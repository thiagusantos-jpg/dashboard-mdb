/* Fluxo de Caixa: contas de caixa, saldo consolidado, movimentos e projeção.
 * Loaded before app.js and uses its shared api(), esc(), money(), APP globals
 * plus finance.js's dateBR()/financeError()/financeFilters() and
 * finance-forms.js's money parsing, controllers and drawers. Movements are an
 * explicit Entrada or Saída typed as a positive pt-BR amount; the sign is
 * applied here, never typed by the user. */
'use strict';

const CASHFLOW_KIND_LABELS = {bank: 'Conta bancária', payment: 'Maquininha/adquirente', cash: 'Dinheiro em caixa'};
const CASHFLOW_CONFIDENCE_LABELS = {realized: 'Realizado', forecast: 'Previsto', simulated: 'Simulado'};

const CASHFLOW_HORIZONS = [30, 60, 90];

function cashBlank(value) { return value == null || String(value).trim() === ''; }

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

/* ---------------------------------------------------------------- request builders */

function buildCashEventRequest(values) {
  const errors = {};
  const amount = parseMoneyToCents(values.amount);
  if (cashBlank(values.cash_account_id)) errors.cash_account_id = 'Escolha a conta.';
  if (!['in', 'out'].includes(values.direction)) errors.direction = 'Escolha Entrada ou Saída.';
  if (amount == null || amount <= 0) errors.amount = 'Informe um valor maior que zero.';
  if (cashBlank(values.occurred_at)) errors.occurred_at = 'Informe a data.';
  if (cashBlank(values.description)) errors.description = 'Informe a descrição.';
  if (Object.keys(errors).length) return {errors};
  return {
    method: 'POST', path: `${financeBasePath()}/cash-events`,
    body: {
      cash_account_id: String(values.cash_account_id),
      amount_cents: values.direction === 'out' ? -amount : amount,
      occurred_at: values.occurred_at,
      description: String(values.description).trim(),
    },
  };
}

function buildTransferRequest(values) {
  const errors = {};
  const amount = parseMoneyToCents(values.amount);
  if (cashBlank(values.from_account_id)) errors.from_account_id = 'Escolha a conta de origem.';
  if (cashBlank(values.to_account_id)) errors.to_account_id = 'Escolha a conta de destino.';
  else if (String(values.to_account_id) === String(values.from_account_id)) errors.to_account_id = 'Escolha contas diferentes.';
  if (amount == null || amount <= 0) errors.amount = 'Informe um valor maior que zero.';
  if (cashBlank(values.occurred_at)) errors.occurred_at = 'Informe a data.';
  if (Object.keys(errors).length) return {errors};
  return {
    method: 'POST', path: `${financeBasePath()}/cash-transfers`,
    body: {
      from_account_id: String(values.from_account_id),
      to_account_id: String(values.to_account_id),
      amount_cents: amount,
      occurred_at: values.occurred_at,
      description: cashBlank(values.description) ? 'Transferência entre contas' : String(values.description).trim(),
    },
  };
}

// The opening balance is posted as its own dated movement right after the account exists.
function buildCashAccountRequest(values) {
  const errors = {};
  const name = String(values.name || '').trim();
  if (!name) errors.name = 'Informe o nome da conta.';
  if (!['bank', 'payment', 'cash'].includes(values.kind)) errors.kind = 'Escolha o tipo de conta.';
  let opening = null;
  if (!cashBlank(values.opening_amount)) {
    const amount = parseMoneyToCents(values.opening_amount);
    if (amount == null || amount <= 0) errors.opening_amount = 'Informe um saldo inicial maior que zero ou deixe em branco.';
    if (cashBlank(values.opening_date)) errors.opening_date = 'Informe a data do saldo inicial.';
    if (amount != null && amount > 0 && !cashBlank(values.opening_date)) opening = {amount_cents: amount, occurred_at: values.opening_date};
  }
  if (Object.keys(errors).length) return {errors};
  return {method: 'POST', path: `${financeBasePath()}/cash-accounts`, body: {name, kind: values.kind}, opening};
}

function openingBalanceRequest(account, opening) {
  return {
    method: 'POST', path: `${financeBasePath()}/cash-events`,
    body: {cash_account_id: String(account.id), amount_cents: opening.amount_cents, occurred_at: opening.occurred_at, description: 'Saldo inicial'},
  };
}

/* ---------------------------------------------------------------- page */

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

  const accountOptions = accounts.map((a) => ({value: a.id, label: a.name}));
  const accountRows = accounts.map((a) => `
    <tr>
      <td>${esc(a.name)}</td>
      <td>${esc(CASHFLOW_KIND_LABELS[a.kind] || a.kind)}</td>
      <td class="num">${money(a.balance_cents)}</td>
      <td><div class="row-actions">
        <button type="button" class="btn-secondary" data-cash-rename="${esc(a.id)}" aria-label="Renomear ${esc(a.name)}">Renomear</button>
        <button type="button" class="btn-secondary" data-cash-archive="${esc(a.id)}" aria-label="Arquivar ${esc(a.name)}">Arquivar</button>
      </div></td>
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

  const movementForms = accounts.length ? `
    <div class="settings-block">
      <h2>Movimentar caixa</h2>
      <form id="cash-event-form" class="settings-form" novalidate>
        <fieldset class="segmented">
          <legend class="field-label">Tipo de movimento</legend>
          <label><input type="radio" name="direction" value="in"> Entrada</label>
          <label><input type="radio" name="direction" value="out" checked> Saída</label>
        </fieldset>
        <div class="form-grid">
          ${formField('cash_account_id', 'Conta', selectControl(accountOptions, accountOptions.length === 1 ? accountOptions[0].value : '', 'Escolha…'))}
          ${formField('amount', 'Valor (R$)', moneyInput(''), 'Sempre positivo: o tipo acima define se entra ou sai.')}
          ${formField('occurred_at', 'Data', dateInput(todayISO()))}
          ${formField('description', 'Descrição', textInput('', 240))}
        </div>
        <div class="form-error" data-form-error role="alert"></div>
        <div class="settings-actions"><button class="btn-primary" type="submit">Lançar movimento</button></div>
      </form>
    </div>
    ${accounts.length > 1 ? `
    <div class="settings-block">
      <h2>Transferir entre contas</h2>
      <form id="cash-transfer-form" class="settings-form" novalidate>
        <div class="form-grid">
          ${formField('from_account_id', 'De', selectControl(accountOptions, '', 'Escolha…'))}
          ${formField('to_account_id', 'Para', selectControl(accountOptions, '', 'Escolha…'))}
          ${formField('amount', 'Valor (R$)', moneyInput(''))}
          ${formField('occurred_at', 'Data', dateInput(todayISO()))}
          ${formField('description', 'Descrição (opcional)', textInput('', 240))}
        </div>
        <div class="form-error" data-form-error role="alert"></div>
        <div class="settings-actions"><button class="btn-primary" type="submit">Transferir</button></div>
      </form>
    </div>` : ''}` : '<div class="empty-state mt-16">Cadastre uma conta de caixa para lançar movimentos.</div>';

  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <div class="page-toolbar">
      <div class="filters">
        <div><label class="field-label" for="cashflow-horizon">Horizonte da projeção</label>
          <select id="cashflow-horizon" class="login-input">${CASHFLOW_HORIZONS.map((d) => `<option value="${d}"${d === horizon ? ' selected' : ''}>${d} dias</option>`).join('')}</select></div>
      </div>
    </div>
    <div id="cashflow-message" class="form-error" role="alert"></div>
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
        <thead><tr><th>Nome</th><th>Tipo</th><th class="num">Saldo</th><th>Ações</th></tr></thead>
        <tbody>${accountRows || '<tr><td colspan="4">Nenhuma conta cadastrada.</td></tr>'}</tbody>
      </table></div>
      <p class="field-help">Arquivar tira a conta das listas; o saldo e os movimentos continuam no histórico.</p>
      <form id="cash-account-form" class="settings-form" novalidate>
        <h3 class="drawer-subtitle">Nova conta</h3>
        <div class="form-grid">
          ${formField('name', 'Nome da conta', textInput('', 180))}
          ${formField('kind', 'Tipo', selectControl(Object.entries(CASHFLOW_KIND_LABELS).map(([value, label]) => ({value, label})), 'bank'))}
          ${formField('opening_amount', 'Saldo inicial (R$, opcional)', moneyInput(''), 'Lançado como uma entrada "Saldo inicial" na data informada.')}
          ${formField('opening_date', 'Data do saldo inicial', dateInput(''))}
        </div>
        <div class="form-error" data-form-error role="alert"></div>
        <div class="settings-actions"><button class="btn-secondary" type="submit">Adicionar conta</button></div>
      </form>
    </div>
    ${movementForms}
    <h2 class="section-header">Próximos ${horizon} dias — realizado e previsto</h2>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Data</th><th>Descrição</th><th class="num">Valor</th><th>Origem</th></tr></thead>
      <tbody>${movementRows || '<tr><td colspan="4">Nenhuma movimentação prevista.</td></tr>'}</tbody>
    </table></div>`;

  const refresh = () => refreshKeepingScroll(() => renderFluxoCaixa());
  const content = document.getElementById('content');
  document.getElementById('cashflow-horizon').addEventListener('change', (ev) => {
    financeFilters('fluxo-caixa', {horizon: 90}).horizon = Number(ev.target.value);
    renderFluxoCaixa();
  });

  bindForm(document.getElementById('cash-account-form'), buildCashAccountRequest, async (account, intent, form) => {
    const {opening} = buildCashAccountRequest(readFormValues(form));
    let message = '';
    if (opening) {
      const request = openingBalanceRequest(account, opening);
      try {
        await api(request.path, {method: request.method, body: JSON.stringify(request.body)});
      } catch (e) {
        message = `Conta criada, mas o saldo inicial não foi lançado (${e.message}). Lance-o em Movimentar caixa como Entrada.`;
      }
    }
    await refresh();
    if (message) document.getElementById('cashflow-message').textContent = message;
  });
  const eventForm = document.getElementById('cash-event-form');
  if (eventForm) bindForm(eventForm, buildCashEventRequest, () => refresh());
  const transferForm = document.getElementById('cash-transfer-form');
  if (transferForm) bindForm(transferForm, buildTransferRequest, () => refresh());

  const byId = Object.fromEntries(accounts.map((a) => [String(a.id), a]));
  content.querySelectorAll('[data-cash-rename]').forEach((button) => button.addEventListener('click', () =>
    openRenameForm('cashAccount', byId[button.dataset.cashRename], {trigger: button, onSaved: refresh})));
  content.querySelectorAll('[data-cash-archive]').forEach((button) => button.addEventListener('click', () => {
    const account = byId[button.dataset.cashArchive];
    openArchiveForm('cashAccount', account, true, {
      trigger: button, onSaved: refresh,
      effect: `A conta sai das listas de pagamento e movimento. O saldo de ${money(account.balance_cents)} e os movimentos continuam no histórico e no saldo consolidado.`,
    });
  }));
}

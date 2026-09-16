/* Fluxo de Caixa: contas de caixa, saldo consolidado, curva de saldo e projeção.
 * Loaded before app.js and uses its shared api(), esc(), money(), APP globals;
 * finance.js's dateBR()/financeError()/financeFilters()/cashOutlook()/outlookHtml();
 * finance-forms.js's money parsing, drawers and controllers; insights.js's brlShort();
 * echarts-charts.js's mountEchartCombo()/brandTokens()/date-label helpers. Movements
 * are an explicit Entrada or Saída typed as a positive pt-BR amount; the sign is
 * applied here, never typed by the user.
 *
 * UX pass (2026-09-16): cadastro de conta, lançamento e transferência viram gavetas
 * (o sócio abre a tela para ver saldo e projeção, não para cadastrar — o mesmo
 * princípio já aplicado em Despesas/Contas a pagar); a tabela de movimentos futuros
 * agrupa por dia com o saldo do dia e marca "hoje"; um gráfico mostra o saldo
 * realizado (sólido) emendando na projeção (tracejado); e o outlook/alerta já usado
 * em Contas a pagar é reaproveitado aqui em vez de duplicar a mesma pergunta com um
 * texto diferente. */
'use strict';

const CASHFLOW_KIND_LABELS = {bank: 'Conta bancária', payment: 'Maquininha/adquirente', cash: 'Dinheiro em caixa'};
const CASHFLOW_CONFIDENCE_LABELS = {realized: 'Realizado', forecast: 'Previsto', simulated: 'Simulado'};
const CASHFLOW_CONFIDENCE_BADGES = {realized: 'badge-success', forecast: 'badge-info', simulated: 'badge-warning'};
// Só os tipos que merecem um selo no extrato: um lançamento comum não precisa dizer que é comum.
const CASHFLOW_EVENT_KIND_TAGS = {transfer: 'Transferência', reversal: 'Estorno'};

const CASHFLOW_HORIZONS = [30, 60, 90];
// Fixo, independente do horizonte escolhido: é só o trecho "sólido" do gráfico, para
// mostrar de onde o saldo veio antes de emendar na projeção.
const CASHFLOW_HISTORY_DAYS = 30;

const CASHFLOW_TIPO_FILTERS = {
  todas: {label: 'Todas', match: () => true},
  entradas: {label: 'Só entradas', match: (item) => item.amount_cents >= 0},
  saidas: {label: 'Só saídas', match: (item) => item.amount_cents < 0},
};

function cashBlank(value) { return value == null || String(value).trim() === ''; }

function cashflowHorizon() {
  const filters = financeFilters('fluxo-caixa', {horizon: 90, tipo: 'todas'});
  return CASHFLOW_HORIZONS.includes(Number(filters.horizon)) ? Number(filters.horizon) : 90;
}

function cashflowTipo() {
  const filters = financeFilters('fluxo-caixa', {horizon: 90, tipo: 'todas'});
  return CASHFLOW_TIPO_FILTERS[filters.tipo] ? filters.tipo : 'todas';
}

function forecastWindow(days) {
  const start = todayISO();
  const end = new Date(`${start}T00:00:00Z`);
  end.setUTCDate(end.getUTCDate() + days);
  return {start, end: end.toISOString().slice(0, 10)};
}

// O trecho sólido do gráfico: os CASHFLOW_HISTORY_DAYS dias antes de hoje.
function historyWindow(days) {
  const end = todayISO();
  const start = new Date(`${end}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - days);
  return {start: start.toISOString().slice(0, 10), end};
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

/* ---------------------------------------------------------------- pure helpers */

// Só o que já aconteceu de fato — um item "hoje" pode ser uma conta vencida arrastada
// para a projeção (confidence: forecast), não dinheiro que já entrou ou saiu.
function cashflowRealizedTotals(days) {
  let incoming = 0;
  let outgoing = 0;
  (days || []).forEach((day) => (day.items || []).forEach((item) => {
    if (item.confidence !== 'realized') return;
    if (item.amount_cents >= 0) incoming += item.amount_cents; else outgoing += item.amount_cents;
  }));
  return {incoming_cents: incoming, outgoing_cents: outgoing};
}

/* Uma curva só, emendada em "hoje": sólida (realizado) antes, tracejada (previsto) depois.
 * Os dois pontos de "hoje" (o último do histórico e o primeiro da projeção) valem o mesmo
 * saldo, então as duas séries se tocam nesse ponto em vez de deixar um buraco no gráfico. */
function cashflowBalanceSeries(historyDays, forecastDays, today) {
  const past = (historyDays || []).filter((d) => d.date < today);
  const future = forecastDays || [];
  const labels = [...past.map((d) => d.date), ...future.map((d) => d.date)];
  const boundary = past.length; // índice de "hoje" (primeiro dia da projeção) no array combinado
  const realized = labels.map((_, i) => {
    if (i > boundary) return null;
    const day = i < boundary ? past[i] : future[0];
    return day ? day.balance_cents : null;
  });
  const forecast = labels.map((_, i) => {
    if (i < boundary) return null;
    const day = future[i - boundary];
    return day ? day.balance_cents : null;
  });
  return {labels, realized, forecast};
}

function confidenceBadge(confidence) {
  return `<span class="${CASHFLOW_CONFIDENCE_BADGES[confidence] || 'badge-muted'}">${esc(CASHFLOW_CONFIDENCE_LABELS[confidence] || confidence)}</span>`;
}

/* O extrato vem do mais novo para o mais antigo, e o saldo da conta é o total de tudo que
 * ela já movimentou — então o saldo depois do lançamento mais novo é o saldo atual, e cada
 * linha anterior desconta o próprio valor. A conta fecha mesmo quando a conta tem mais
 * movimentos do que as 200 linhas que a API devolve: andando para trás, nada de mais antigo
 * entra nessa soma. */
function statementRows(events, balanceCents) {
  let running = balanceCents;
  return (events || []).map((event) => {
    const row = Object.assign({}, event, {balance_cents: running});
    running -= event.amount_cents;
    return row;
  });
}

function statementHtml(rows) {
  if (!rows.length) return '<p class="muted">Nenhum movimento nesta conta ainda.</p>';
  return `<div class="table-wrap"><table class="data-table">
      <thead><tr><th>Data</th><th>Descrição</th><th class="num">Valor</th><th class="num">Saldo</th></tr></thead>
      <tbody>${rows.map((row) => `<tr>
        <td>${dateBR(row.occurred_at)}</td>
        <td>${esc(row.description)}${CASHFLOW_EVENT_KIND_TAGS[row.kind] ? ` <span class="payables-tag">${CASHFLOW_EVENT_KIND_TAGS[row.kind]}</span>` : ''}</td>
        <td class="num">${money(row.amount_cents)}</td>
        <td class="num">${money(row.balance_cents)}</td>
      </tr>`).join('')}</tbody>
    </table></div>`;
}

// Agrupa os dias com movimento pelo tipo escolhido (entradas/saídas/todas) e descarta
// o dia inteiro se o filtro esvaziar seus itens — um dia sem nada não vira um grupo vazio.
function movementGroups(days, tipo) {
  const matcher = (CASHFLOW_TIPO_FILTERS[tipo] || CASHFLOW_TIPO_FILTERS.todas).match;
  return (days || [])
    .map((day) => Object.assign({}, day, {items: (day.items || []).filter(matcher)}))
    .filter((day) => day.items.length);
}

function movementGroupsHtml(groups, today) {
  return `<div class="table-wrap"><table class="data-table payables-table">
      <thead><tr><th>Descrição</th><th class="num">Valor</th><th>Origem</th></tr></thead>
      ${groups.map((day) => {
        const negative = day.balance_cents < 0;
        const cls = [day.date === today ? 'today' : '', negative ? 'negative' : ''].filter(Boolean).join(' ');
        return `<tbody class="payables-group${cls ? ` ${cls}` : ''}">
          <tr class="payables-group-row"><th colspan="3" scope="rowgroup">${dateBR(day.date)}${day.date === today ? ' · Hoje' : ''}
            <span>Saldo: ${money(day.balance_cents)}${negative ? ' · negativo' : ''}</span></th></tr>
          ${day.items.map((item) => `<tr>
            <td>${esc(item.description)}</td>
            <td class="num">${money(item.amount_cents)}</td>
            <td>${confidenceBadge(item.confidence)}</td>
          </tr>`).join('')}
        </tbody>`;
      }).join('')}
    </table></div>`;
}

/* ---------------------------------------------------------------- drawers */

function openCashAccountForm(ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({title: 'Nova conta de caixa', trigger: ctx.trigger, body: `
    <form class="drawer-form" novalidate>
      <div class="form-grid">
        ${formField('name', 'Nome da conta', textInput('', 180))}
        ${formField('kind', 'Tipo', selectControl(Object.entries(CASHFLOW_KIND_LABELS).map(([value, label]) => ({value, label})), 'bank'))}
        ${formField('opening_amount', 'Saldo inicial (R$, opcional)', moneyInput(''), 'Lançado como uma entrada "Saldo inicial" na data informada.')}
        ${formField('opening_date', 'Data do saldo inicial', dateInput(''))}
      </div>
      ${formActions('Adicionar conta')}
    </form>`});
  bindDrawerForm(drawer, buildCashAccountRequest, async (account, intent, form) => {
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
    drawer.close();
    if (ctx.onSaved) await ctx.onSaved();
    if (message) {
      const banner = document.getElementById('cashflow-message');
      if (banner) banner.textContent = message;
    }
  });
}

function openCashEventForm(accountOptions, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({title: 'Movimentar caixa', trigger: ctx.trigger, body: `
    <form class="drawer-form" novalidate>
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
      ${formActions('Lançar movimento')}
    </form>`});
  bindDrawerForm(drawer, buildCashEventRequest, () => { drawer.close(); if (ctx.onSaved) ctx.onSaved(); });
}

function openCashTransferForm(accountOptions, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({title: 'Transferir entre contas', trigger: ctx.trigger, body: `
    <form class="drawer-form" novalidate>
      <div class="form-grid">
        ${formField('from_account_id', 'De', selectControl(accountOptions, '', 'Escolha…'))}
        ${formField('to_account_id', 'Para', selectControl(accountOptions, '', 'Escolha…'))}
        ${formField('amount', 'Valor (R$)', moneyInput(''))}
        ${formField('occurred_at', 'Data', dateInput(todayISO()))}
        ${formField('description', 'Descrição (opcional)', textInput('', 240))}
      </div>
      ${formActions('Transferir')}
    </form>`});
  bindDrawerForm(drawer, buildTransferRequest, () => { drawer.close(); if (ctx.onSaved) ctx.onSaved(); });
}

/* "Quanto entrou e saiu na maquininha?" — a projeção não responde isso: ela é consolidada e
 * as contas previstas ainda não escolheram de qual conta vão sair. Quem responde é o extrato
 * da conta, que é só o realizado, onde cada movimento sabe a que conta pertence. */
async function openCashStatement(account, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({
    title: `Extrato — ${account.name}`, trigger: ctx.trigger,
    body: '<div class="skeleton-block" aria-label="Carregando extrato"></div>',
  });
  let events;
  try {
    events = await api(`${financeBasePath()}/cash-events?cash_account_id=${encodeURIComponent(account.id)}`);
  } catch (e) {
    return drawerLoadError(drawer, e, () => { drawer.close(); openCashStatement(account, ctx); });
  }
  drawer.setBody(`
    <p class="muted">${esc(CASHFLOW_KIND_LABELS[account.kind] || account.kind)} · saldo atual de <strong>${money(account.balance_cents)}</strong></p>
    ${statementHtml(statementRows(events, account.balance_cents))}
    <p class="field-help">Só o que já aconteceu nesta conta. O que ainda vai entrar ou sair está na projeção, que é consolidada de todas as contas.</p>
    <div class="btn-row"><button type="button" class="btn-secondary" data-drawer-close>Fechar</button></div>`);
}

/* ---------------------------------------------------------------- page */

async function renderFluxoCaixa(token) {
  token = token || beginPage();
  const title = `${icon('trending-up', {class: 'title-icon'})}Fluxo de caixa`;
  const horizon = cashflowHorizon();
  const tipo = cashflowTipo();
  const subtitle = `Saldo de caixa e o que está previsto para os próximos ${horizon} dias.`;
  financeLoading(title, subtitle, 'Carregando fluxo de caixa');
  const {start, end} = forecastWindow(horizon);
  const historyRange = historyWindow(CASHFLOW_HISTORY_DAYS);
  let accounts, balance, forecastData, historyData;
  try {
    [accounts, balance, forecastData, historyData] = await Promise.all([
      api(`/api/companies/${APP.company}/finance/cash-accounts`),
      api(`/api/companies/${APP.company}/finance/cash-balance`),
      api(`/api/companies/${APP.company}/finance/forecast?start=${start}&end=${end}&scenario=base`),
      // Só o trecho sólido do gráfico: se falhar, o gráfico perde o histórico e mostra
      // só a projeção tracejada, nunca derruba a página inteira por isso.
      api(`/api/companies/${APP.company}/finance/forecast?start=${historyRange.start}&end=${historyRange.end}&scenario=base`).catch(() => null),
    ]);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;
    return financeError(title, subtitle, e, () => renderFluxoCaixa());
  }
  if (!APP.pageState.isCurrent(token)) return;

  const today = todayISO();
  const accountOptions = accounts.map((a) => ({value: a.id, label: a.name}));
  const accountRows = accounts.map((a) => `
    <tr>
      <td>${esc(a.name)}</td>
      <td>${esc(CASHFLOW_KIND_LABELS[a.kind] || a.kind)}</td>
      <td class="num">${money(a.balance_cents)}</td>
      <td><div class="row-actions">
        <button type="button" class="btn-secondary" data-cash-statement="${esc(a.id)}" aria-label="Extrato de ${esc(a.name)}">Extrato</button>
        <button type="button" class="btn-secondary" data-cash-rename="${esc(a.id)}" aria-label="Renomear ${esc(a.name)}">Renomear</button>
        <button type="button" class="btn-secondary" data-cash-archive="${esc(a.id)}" aria-label="Arquivar ${esc(a.name)}">Arquivar</button>
      </div></td>
    </tr>`).join('');

  const realized = cashflowRealizedTotals((historyData && historyData.days) || []);
  const groups = movementGroups(forecastData.days, tipo);
  const emptyMovements = `<div class="empty-state">Nenhuma movimentação${tipo !== 'todas' ? ' deste tipo' : ''} prevista.</div>`;

  const tipoOptions = Object.entries(CASHFLOW_TIPO_FILTERS)
    .map(([value, f]) => `<option value="${value}"${value === tipo ? ' selected' : ''}>${f.label}</option>`).join('');

  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <div class="page-toolbar">
      <div class="filters">
        <div><label class="field-label" for="cashflow-horizon">Horizonte da projeção</label>
          <select id="cashflow-horizon" class="login-input">${CASHFLOW_HORIZONS.map((d) => `<option value="${d}"${d === horizon ? ' selected' : ''}>${d} dias</option>`).join('')}</select></div>
        <div><label class="field-label" for="cashflow-tipo">Mostrar</label>
          <select id="cashflow-tipo" class="login-input">${tipoOptions}</select></div>
      </div>
      <div class="btn-row">
        ${accounts.length ? '<button type="button" class="btn-primary" data-cash-event-new>Movimentar caixa</button>' : ''}
        ${accounts.length > 1 ? '<button type="button" class="btn-secondary" data-cash-transfer-new>Transferir entre contas</button>' : ''}
        <button type="button" class="btn-secondary" data-cash-account-new>Nova conta</button>
      </div>
    </div>
    <div id="cashflow-message" class="form-error" role="alert"></div>
    <div class="kpi-grid kpi-grid-4">
      ${kpi('Saldo consolidado', money(balance.balance_cents))}
      ${kpi(`Entradas realizadas (${CASHFLOW_HISTORY_DAYS} dias)`, money(realized.incoming_cents))}
      ${kpi(`Saídas realizadas (${CASHFLOW_HISTORY_DAYS} dias)`, money(-realized.outgoing_cents))}
      ${kpi('Contas de caixa', accounts.length)}
    </div>
    <h2 class="section-header">Saldo de caixa</h2>
    <div class="chart-container chart-h-330" id="chart-cashflow-balance"></div>
    ${outlookHtml(cashOutlook(forecastData))}
    <h2 class="section-header">Próximos ${horizon} dias — realizado e previsto</h2>
    <p class="section-note">Projeção consolidada de todas as contas: uma conta a pagar só escolhe de qual conta sai
      na hora do pagamento. Para ver conta por conta, abra o extrato dela em Contas de caixa.</p>
    ${groups.length ? movementGroupsHtml(groups, today) : emptyMovements}
    <div class="settings-block mt-16">
      <h2>Contas de caixa</h2>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th>Nome</th><th>Tipo</th><th class="num">Saldo</th><th>Ações</th></tr></thead>
        <tbody>${accountRows || '<tr><td colspan="4">Nenhuma conta cadastrada.</td></tr>'}</tbody>
      </table></div>
      <p class="field-help">Arquivar tira a conta das listas; o saldo e os movimentos continuam no histórico.</p>
    </div>`;

  const refresh = () => refreshKeepingScroll(() => renderFluxoCaixa());
  const content = document.getElementById('content');
  document.getElementById('cashflow-horizon').addEventListener('change', (ev) => {
    financeFilters('fluxo-caixa', {horizon: 90, tipo: 'todas'}).horizon = Number(ev.target.value);
    refresh();
  });
  document.getElementById('cashflow-tipo').addEventListener('change', (ev) => {
    financeFilters('fluxo-caixa', {horizon: 90, tipo: 'todas'}).tipo = ev.target.value;
    refresh();
  });

  const eventButton = content.querySelector('[data-cash-event-new]');
  if (eventButton) eventButton.addEventListener('click', () => openCashEventForm(accountOptions, {trigger: eventButton, onSaved: refresh}));
  const transferButton = content.querySelector('[data-cash-transfer-new]');
  if (transferButton) transferButton.addEventListener('click', () => openCashTransferForm(accountOptions, {trigger: transferButton, onSaved: refresh}));
  content.querySelector('[data-cash-account-new]').addEventListener('click', (ev) =>
    openCashAccountForm({trigger: ev.currentTarget, onSaved: refresh}));

  const byId = Object.fromEntries(accounts.map((a) => [String(a.id), a]));
  content.querySelectorAll('[data-cash-statement]').forEach((button) => button.addEventListener('click', () =>
    openCashStatement(byId[button.dataset.cashStatement], {trigger: button})));
  content.querySelectorAll('[data-cash-rename]').forEach((button) => button.addEventListener('click', () =>
    openRenameForm('cashAccount', byId[button.dataset.cashRename], {trigger: button, onSaved: refresh})));
  content.querySelectorAll('[data-cash-archive]').forEach((button) => button.addEventListener('click', () => {
    const account = byId[button.dataset.cashArchive];
    openArchiveForm('cashAccount', account, true, {
      trigger: button, onSaved: refresh,
      effect: `A conta sai das listas de pagamento e movimento. O saldo de ${money(account.balance_cents)} e os movimentos continuam no histórico e no saldo consolidado.`,
    });
  }));

  disposeEcharts(); // refreshKeepingScroll re-renders in place, outside the router's own dispose
  const t = brandTokens();
  const series = cashflowBalanceSeries(historyData ? historyData.days : [], forecastData.days, today);
  mountEchartCombo(document.getElementById('chart-cashflow-balance'), {
    labels: series.labels.map(dayMonthLabel), tipLabels: series.labels.map(fullDateLabel), rotate: true,
    series: [
      {name: 'Realizado', type: 'line', values: series.realized, color: t.amber, width: 2.5, marker: 'circle', fill: true, fmt: money},
      {name: 'Previsto', type: 'line', values: series.forecast, color: t.amber, width: 2.5, dash: '4 4', marker: 'none', fmt: money},
    ],
    hlines: [{value: 0, color: t.bad, dash: '2 4', label: 'Saldo zero'}],
    yFmt: brlShort, yTitle: 'Saldo (R$)',
    empty: 'Sem dados suficientes para o gráfico.',
  });
}

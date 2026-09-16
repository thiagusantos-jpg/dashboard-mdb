/* Recebíveis Stone: agenda de recebimentos (passados e futuros) e taxa efetiva
 * vs. contratada. A importação das vendas Stone (CSV/XML) mora em Conciliação → Fontes de
 * dados (openStoneImportForm, reconciliation.js); aqui fica só um atalho.
 * Loaded after reconciliation.js and uses its shared api(), esc(), money(), APP
 * globals plus finance.js's dateBR()/financeError(), loans.js's addMonthsISO()
 * and cashflow.js's openCashAccountForm(). */
'use strict';

const RECEIVABLES_PAST_DAYS = 30;

function receivablesWindow(today) {
  const start = new Date(`${today}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - RECEIVABLES_PAST_DAYS);
  return {start: start.toISOString().slice(0, 10), end: addMonthsISO(today, 2)};
}

function receivablesRate(value, suffix) {
  return value.toFixed(2).replace('.', ',') + (suffix || '%');
}

async function renderRecebiveis(token) {
  token = token || beginPage();
  const title = `${icon('tag', {class: 'title-icon'})}Recebíveis`;
  const subtitle = 'Quanto a Stone vai depositar (e já depositou) e quanto ela cobra de taxa.';
  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <div class="skeleton-block" aria-label="Carregando recebíveis"></div>`;
  const today = todayISO();
  const {start, end} = receivablesWindow(today);
  let cashAccounts, settlements, feeReport;
  try {
    [cashAccounts, settlements, feeReport] = await Promise.all([
      api(`/api/companies/${APP.company}/finance/cash-accounts`),
      api(`/api/companies/${APP.company}/finance/receivables/expected-settlements?start=${start}&end=${end}`),
      api(`/api/companies/${APP.company}/finance/receivables/effective-fee-report?start=${start}&end=${end}`),
    ]);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;
    return financeError(title, subtitle, e, () => renderRecebiveis());
  }
  if (!APP.pageState.isCurrent(token)) return;

  const settlementRows = settlements.map((s) => {
    const past = s.settlement_date < today;
    const isToday = s.settlement_date === today;
    const badge = isToday ? '<span class="badge-info">Hoje</span>'
      : past ? '<span class="badge-muted">Já deveria ter caído</span>' : '<span class="badge-warning">A receber</span>';
    return `
    <tr>
      <td>${dateBR(s.settlement_date)}</td>
      <td>${badge}</td>
      <td class="num">${s.count}</td>
      <td class="num">${money(s.gross_cents)}</td>
      <td class="num">${money(s.fee_cents)}</td>
      <td class="num"><strong>${money(s.net_cents)}</strong></td>
    </tr>`;
  }).join('');
  const toReceive = settlements.filter((s) => s.settlement_date >= today).reduce((sum, s) => sum + Number(s.net_cents || 0), 0);

  const contracted = feeReport.contracted_rate_pct;
  const variance = feeReport.variance_pct;
  const contractedValue = contracted == null ? 'Não informada' : receivablesRate(contracted);
  const rateButton = `<button type="button" class="btn-link" id="receivables-rate-edit">${contracted == null ? 'Informar taxa contratada' : 'Alterar'}</button>`;

  const noAccount = !cashAccounts.length;
  const sourceNote = noAccount
    ? `<div class="empty-state">
        <p><strong>Nenhuma conta de caixa cadastrada.</strong> Crie a conta onde a Stone deposita para poder importar as vendas.</p>
        <button type="button" class="btn-primary" id="receivables-create-account">Criar conta de caixa</button>
      </div>`
    : `<div class="page-toolbar">
        <p class="page-subtitle">As vendas Stone (relatório de recebíveis) são importadas em <a href="#/conciliacao">Conciliação</a>, junto com o extrato bancário.</p>
        <button type="button" class="btn-secondary" id="receivables-import-open">Importar vendas Stone</button>
      </div>`;

  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <hr class="divider">
    <div class="kpi-grid kpi-grid-3">
      ${kpi('A receber (próximos 60 dias)', money(toReceive), `De hoje até ${dateBR(end)}`)}
      ${kpi('Taxa efetiva', feeReport.effective_rate_pct == null ? '—' : receivablesRate(feeReport.effective_rate_pct),
        `Contratada: ${contractedValue} · vendas de ${dateBR(start)} a ${dateBR(end)} ${rateButton}`)}
      ${kpi('Variação', variance == null ? '—' : (variance >= 0 ? '+' : '') + receivablesRate(variance, ' p.p.'),
        variance == null ? (contracted == null ? 'Informe a taxa contratada para comparar.' : 'Sem vendas no período.')
          : variance > 0 ? 'Stone está cobrando acima do contratado.' : 'Dentro do contratado.',
        variance != null && variance > 0 ? 'kpi-negative' : null)}
    </div>
    ${sourceNote}
    <h2 class="section-header">Agenda de recebimentos <span class="section-hint">${dateBR(start)} a ${dateBR(end)}</span></h2>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Data</th><th>Situação</th><th class="num">Vendas</th><th class="num">Bruto</th><th class="num">Taxa</th><th class="num">Líquido</th></tr></thead>
      <tbody>${settlementRows || `<tr><td colspan="6">Nenhum recebível entre ${dateBR(start)} e ${dateBR(end)}. Importe o relatório de recebíveis da Stone para ver a agenda.</td></tr>`}</tbody>
    </table></div>`;

  const refresh = () => renderRecebiveis();
  const createAccount = document.getElementById('receivables-create-account');
  if (createAccount) createAccount.addEventListener('click', (ev) => openCashAccountForm({trigger: ev.currentTarget, onSaved: refresh}));
  const importOpen = document.getElementById('receivables-import-open');
  if (importOpen) importOpen.addEventListener('click', (ev) => openStoneImportForm(cashAccounts, {trigger: ev.currentTarget, onSaved: refresh}));
  document.getElementById('receivables-rate-edit').addEventListener('click', (ev) =>
    openContractedRateForm(contracted, {trigger: ev.currentTarget, onSaved: refresh}));
}

function buildContractedRateRequest(values) {
  const raw = String(values.rate_pct == null ? '' : values.rate_pct).trim().replace('%', '').replace(',', '.');
  const rate = Number(raw);
  const errors = {};
  if (!raw || !Number.isFinite(rate) || rate < 0 || rate > 20) errors.rate_pct = 'Informe um percentual entre 0 e 20 (ex.: 1,49).';
  if (!values.effective_from) errors.effective_from = 'Informe a partir de quando vale a taxa.';
  if (Object.keys(errors).length) return {errors};
  return {
    method: 'PUT', path: `${financeBasePath()}/receivables/contracted-rate`,
    body: {rate_pct: rate, effective_from: values.effective_from},
  };
}

function openContractedRateForm(current, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({title: 'Taxa contratada com a Stone', trigger: ctx.trigger, body: `
    <form class="drawer-form" novalidate>
      <div class="form-grid">
        ${formField('rate_pct', 'Taxa média contratada (%)', moneyInput(current == null ? '' : String(current).replace('.', ',')),
          'A taxa (MDR) do seu contrato. O painel compara com o que a Stone realmente descontou.')}
        ${formField('effective_from', 'Vale a partir de', dateInput(todayISO()))}
      </div>
      ${formActions('Salvar taxa')}
    </form>`});
  bindDrawerForm(drawer, buildContractedRateRequest, () => { drawer.close(); if (ctx.onSaved) ctx.onSaved(); });
}

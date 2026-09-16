/* Conciliação: ponto único de entrada dos dados externos (vendas Stone em XML e
 * extrato bancário em OFX/CSV) e o casamento entre lançamentos de caixa e
 * lançamentos financeiros (um crédito pode fechar várias vendas e taxas).
 * Loaded before app.js and uses its shared api(), esc(), money(), APP globals
 * plus finance.js's dateBR()/financeError(), finance-forms.js's drawer helpers
 * and cashflow.js's openCashAccountForm(). */
'use strict';

const RECONCILIATION_STATE = {groups: [], stoneTab: null};

const RECONCILIATION_STATUS_LABELS = {
  unmatched: 'Sem correspondência', suggested: 'Sugestão pendente', auto_matched: 'Conciliado automaticamente',
  manual_matched: 'Conciliado manualmente', partial: 'Parcial', divergent: 'Divergente', ignored: 'Ignorado',
};

async function renderConciliacao(token) {
  token = token || beginPage();
  const title = `${icon('circle-check', {class: 'title-icon'})}Conciliação bancária`;
  const subtitle = 'Importe as vendas Stone e o extrato do banco e confira se cada movimento bate com o que foi lançado.';
  financeLoading(title, subtitle, 'Carregando conciliação');
  // Interval and account filters apply to the Stone check, the cash movements and the groups anchored on them.
  const filters = financeFilters('conciliacao', {account: '', from: '', to: ''});
  const stoneParams = new URLSearchParams();
  if (filters.from) stoneParams.set('start', filters.from);
  if (filters.to) stoneParams.set('end', filters.to);
  if (filters.account) stoneParams.set('cash_account_id', filters.account);
  let events, groups, cashAccounts, sources, stoneCheck;
  try {
    [events, groups, cashAccounts, sources, stoneCheck] = await Promise.all([
      api(`/api/companies/${APP.company}/finance/cash-events`),
      api(`/api/companies/${APP.company}/finance/reconciliation`),
      api(`/api/companies/${APP.company}/finance/cash-accounts`),
      api(`/api/companies/${APP.company}/finance/reconciliation/sources`),
      api(`/api/companies/${APP.company}/finance/reconciliation/stone-daily?${stoneParams}`),
    ]);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;
    return financeError(title, subtitle, e, () => renderConciliacao());
  }
  if (!APP.pageState.isCurrent(token)) return;

  const linkedEventIds = new Set(
    groups.flatMap((g) => g.links.filter((l) => l.item_type === 'cash_event').map((l) => String(l.item_id)))
  );
  const matches = (e) => (!filters.account || String(e.cash_account_id) === filters.account)
    && (!filters.from || e.occurred_at >= filters.from) && (!filters.to || e.occurred_at <= filters.to);
  const eventsById = Object.fromEntries(events.map((e) => [String(e.id), e]));
  const pending = events.filter((e) => !linkedEventIds.has(String(e.id))).filter(matches);
  const visibleGroups = groups.filter((g) => {
    const anchor = g.links.find((l) => l.item_type === 'cash_event');
    const event = anchor && eventsById[String(anchor.item_id)];
    return !event || matches(event);
  });
  const pendingOptions = pending.map((e) =>
    `<option value="${esc(e.id)}">${dateBR(e.occurred_at)} — ${esc(e.description)} — ${money(e.amount_cents)}</option>`).join('');

  const groupRows = visibleGroups.map((g) => {
    const anchor = g.links.find((l) => l.item_type === 'cash_event');
    const entryLinks = g.links.filter((l) => l.item_type === 'financial_entry');
    const needsReview = g.status === 'suggested';
    return `
    <tr>
      <td class="num">${anchor ? money(anchor.amount_cents) : '—'}</td>
      <td>${entryLinks.length} lançamento(s)</td>
      <td>${money(g.difference_cents)}</td>
      <td>${esc(RECONCILIATION_STATUS_LABELS[g.status] || g.status)}</td>
      <td>${needsReview ? `<form class="inline-form" data-confirm-form="${esc(g.id)}">
            <label class="field-label" for="reconciliation-candidate-${esc(g.id)}">Opção a confirmar</label>
            <select id="reconciliation-candidate-${esc(g.id)}" class="login-input">
              ${g.candidates.map((c, i) => `<option value="${i}">Opção ${i + 1}: ${money(c.total_cents)} em ${c.entry_ids.length} lançamento(s)${anchor ? ` — diferença ${money(anchor.amount_cents - c.total_cents)}` : ''}</option>`).join('')}
            </select>
            <button type="submit" class="btn-secondary">Revisar e confirmar</button>
          </form>` : (g.status !== 'ignored' ? `<form class="inline-form" data-undo-form="${esc(g.id)}">
            <label class="field-label" for="reconciliation-undo-${esc(g.id)}">Motivo para desfazer</label>
            <input id="reconciliation-undo-${esc(g.id)}" type="text" class="login-input" maxlength="500" required>
            <button type="submit" class="btn-secondary">Desfazer</button>
          </form>` : '')}
      </td>
    </tr>`;
  }).join('');

  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    ${reconciliationSourcesSection(sources)}
    <form id="reconciliation-filter" class="page-toolbar recon-filter" aria-label="Período e conta conferidos">
      <div class="filters">
        <div><label class="field-label" for="reconciliation-account">Conta</label>
          <select id="reconciliation-account" name="account" class="login-input">
            <option value="">Todas as contas</option>
            ${cashAccounts.map((a) => `<option value="${esc(a.id)}"${String(a.id) === filters.account ? ' selected' : ''}>${esc(a.name)}</option>`).join('')}
          </select></div>
        <div><label class="field-label" for="reconciliation-from">De</label>
          <input id="reconciliation-from" name="from" type="date" class="login-input" value="${esc(filters.from)}"></div>
        <div><label class="field-label" for="reconciliation-to">Até</label>
          <input id="reconciliation-to" name="to" type="date" class="login-input" value="${esc(filters.to)}"></div>
      </div>
      <button type="submit" class="btn-secondary">Aplicar</button>
    </form>
    ${reconciliationStoneSection(stoneCheck, sources)}
    <h2 class="section-header">Outros movimentos do banco</h2>
    <p class="page-subtitle">Pagamentos, tarifas e créditos que não são repasse da Stone: case cada um com o lançamento correspondente.</p>
    <div class="settings-block">
      <h3>Sugerir conciliação</h3>
      <form id="reconciliation-suggest-form" class="inline-form">
        <div><label class="field-label" for="reconciliation-search">Buscar movimento</label>
          <input id="reconciliation-search" type="search" class="login-input" autocomplete="off" placeholder="Descrição, data ou valor"></div>
        <div><label class="field-label" for="reconciliation-event">Movimento de caixa</label>
          <select id="reconciliation-event" class="login-input" required>${pendingOptions || '<option value="">Nenhum movimento pendente — importe um extrato em Fontes de dados</option>'}</select></div>
        <button type="submit" class="btn-primary">Sugerir</button>
        <span id="reconciliation-suggest-status" class="sim-status" role="status" aria-live="polite"></span>
      </form>
    </div>
    <div id="reconciliation-action-status" class="form-error" role="alert"></div>
    <h2 class="section-header">Conciliações feitas</h2>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Lançamento de caixa</th><th>Lançamentos financeiros</th><th>Diferença</th><th>Status</th><th></th></tr></thead>
      <tbody>${groupRows || '<tr><td colspan="5">Nenhuma conciliação registrada.</td></tr>'}</tbody>
    </table></div>`;

  RECONCILIATION_STATE.groups = groups;
  bindReconciliationSources(cashAccounts, eventsById);
  bindReconciliationStoneTabs();
  document.getElementById('reconciliation-filter').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const form = ev.currentTarget;
    ['account', 'from', 'to'].forEach((key) => { filters[key] = form.elements.namedItem(key).value; });
    renderConciliacao();
  });
  const search = document.getElementById('reconciliation-search');
  const eventSelect = document.getElementById('reconciliation-event');
  search.addEventListener('input', () => {
    const term = search.value.trim().toLowerCase();
    Array.from(eventSelect.options).forEach((option) => {
      option.hidden = !!option.value && !!term && !option.textContent.toLowerCase().includes(term);
    });
  });
  const suggestForm = document.getElementById('reconciliation-suggest-form');
  if (suggestForm) watchForm(suggestForm).addEventListener('submit', onSuggestReconciliation);
  document.querySelectorAll('[data-confirm-form]').forEach((form) => form.addEventListener('submit', onConfirmReconciliation));
  document.querySelectorAll('[data-undo-form]').forEach((form) => form.addEventListener('submit', onUndoReconciliation));
}

async function onSuggestReconciliation(event) {
  event.preventDefault();
  const status = document.getElementById('reconciliation-suggest-status');
  const cashEventId = document.getElementById('reconciliation-event').value;
  if (!cashEventId) return;
  status.textContent = 'Analisando…';
  try {
    await api(`/api/companies/${APP.company}/finance/reconciliation/suggest`, {
      method: 'POST',
      body: JSON.stringify({cash_event_id: cashEventId}),
    });
    clearDirty();
    renderConciliacao();
  } catch (e) {
    status.textContent = 'Erro: ' + e.message;
  }
}

/* Confirming needs the details first: a native confirm dialog names the
 * amount, the number of entries and the difference before anything is sent. */
async function onConfirmReconciliation(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const status = document.getElementById('reconciliation-action-status');
  const button = form.querySelector('button');
  const groupId = form.dataset.confirmForm;
  const group = RECONCILIATION_STATE.groups.find((g) => String(g.id) === String(groupId));
  const optionIndex = parseInt(form.querySelector('select').value, 10);
  const candidate = group && group.candidates[optionIndex];
  if (!candidate) return;
  const anchor = group.links.find((l) => l.item_type === 'cash_event');
  const detail = `Conciliar ${anchor ? money(anchor.amount_cents) : 'o movimento'} com ${candidate.entry_ids.length} lançamento(s) somando ${money(candidate.total_cents)}`
    + (anchor ? ` (diferença ${money(anchor.amount_cents - candidate.total_cents)})` : '') + '?';
  if (!window.confirm(detail)) return;
  status.textContent = '';
  button.disabled = true;
  try {
    await api(`/api/companies/${APP.company}/finance/reconciliation/${groupId}/confirm`, {
      method: 'POST',
      body: JSON.stringify({entry_ids: candidate.entry_ids}),
    });
    renderConciliacao();
  } catch (e) {
    status.textContent = 'Não foi possível confirmar: ' + e.message;
    button.disabled = false;
  }
}

async function onUndoReconciliation(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const groupId = form.dataset.undoForm;
  const reason = form.querySelector('input').value.trim();
  const status = document.getElementById('reconciliation-action-status');
  const button = form.querySelector('button');
  status.textContent = '';
  button.disabled = true;
  try {
    await api(`/api/companies/${APP.company}/finance/reconciliation/${groupId}/undo`, {
      method: 'POST',
      body: JSON.stringify({reason}),
    });
    renderConciliacao();
  } catch (e) {
    status.textContent = 'Não foi possível desfazer: ' + e.message;
    button.disabled = false;
  }
}

/* ---------------------------------------------------------------- repasses da Stone */

const STONE_STATUS = {
  ok: {label: 'Conferido', badge: 'badge-success', tab: 'ok'},
  divergent: {label: 'Valor diferente', badge: 'badge-error', tab: 'pending'},
  missing: {label: 'Não caiu no banco', badge: 'badge-error', tab: 'pending'},
  no_statement: {label: 'Falta o extrato', badge: 'badge-warning', tab: 'pending'},
  upcoming: {label: 'A receber', badge: 'badge-muted', tab: 'upcoming'},
};

const STONE_TABS = [['pending', 'Pendências'], ['upcoming', 'A receber'], ['ok', 'Conferidos'], ['all', 'Todos']];

/* A day "missing" from an account whose statement was never imported is not
 * Stone's fault: say what is actually missing. */
function stoneDayStatus(day, bankImported) {
  if (day.status === 'missing' && !bankImported[String(day.cash_account_id)]) return 'no_statement';
  return day.status;
}

function stoneSignedMoney(cents) {
  if (!cents) return money(0);
  return `${cents > 0 ? '+' : '−'}${money(Math.abs(cents))}`;
}

function stoneDayRow(day, status, showAccount) {
  const meta = STONE_STATUS[status];
  const credit = day.credit;
  const received = credit
    ? `${money(credit.amount_cents)}<div class="cell-note">${dateBR(credit.occurred_at)} · ${esc(credit.description)}</div>`
    : '—';
  const hint = status === 'missing' ? `<div class="cell-note">Nenhum crédito até ${dateBR(stoneAddDays(day.settlement_date, 3))}. Confira no portal Stone.</div>`
    : status === 'no_statement' ? '<div class="cell-note">Importe o extrato desta conta para conferir.</div>'
    : status === 'divergent' ? '<div class="cell-note">Compare as vendas do dia em Recebíveis.</div>' : '';
  const diff = day.difference_cents == null || status === 'no_statement' ? '—' : stoneSignedMoney(day.difference_cents);
  return `<tr data-stone-tab="${meta.tab}">
    <td>${dateBR(day.settlement_date)}</td>
    ${showAccount ? `<td>${esc(day.account_name)}</td>` : ''}
    <td class="num">${money(day.expected_cents)}<div class="cell-note">${day.sales} venda(s)</div></td>
    <td class="num">${received}</td>
    <td class="num${day.difference_cents && status !== 'ok' && status !== 'no_statement' ? ' value-negative' : ''}">${diff}</td>
    <td><span class="${meta.badge}">${meta.label}</span>${hint}</td>
  </tr>`;
}

function stoneAddDays(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function reconciliationStoneSection(check, sources) {
  const bankImported = Object.fromEntries(sources.map((s) => [String(s.cash_account_id), s.bank.count > 0]));
  const days = check.days.map((day) => ({day, status: stoneDayStatus(day, bankImported)}));
  const counts = {pending: 0, upcoming: 0, ok: 0, all: days.length};
  days.forEach(({status}) => { counts[STONE_STATUS[status].tab] += 1; });
  const tab = RECONCILIATION_STATE.stoneTab && counts[RECONCILIATION_STATE.stoneTab] != null
    ? RECONCILIATION_STATE.stoneTab : (counts.pending ? 'pending' : 'all');
  RECONCILIATION_STATE.stoneTab = tab;
  const sum = check.summary;
  const noStatement = days.filter((d) => d.status === 'no_statement').length;
  const showAccount = new Set(check.days.map((d) => String(d.cash_account_id))).size > 1;
  const period = `${dateBR(check.start)} a ${dateBR(check.end)} · tolerância ${money(check.tolerance_cents)}`;
  const header = `<h2 class="section-header">Repasses da Stone <span class="section-hint">${period}</span></h2>`;
  if (!days.length) {
    return `<section class="recon-stone">${header}
      <div class="empty-state"><p>Nenhuma venda Stone com repasse neste período. Importe o XML da Stone em Fontes de dados ou ajuste o período.</p></div>
    </section>`;
  }
  const diffCls = sum.difference_cents < 0 ? 'kpi-negative' : '';
  const verdict = !sum.due_days ? 'Nenhum repasse venceu ainda.'
    : sum.ok_days === sum.due_days ? 'Tudo o que a Stone devia caiu no banco.'
    : noStatement ? `${noStatement} dia(s) sem extrato importado.` : `${sum.due_days - sum.ok_days} dia(s) para verificar.`;
  const rows = days.map(({day, status}) => stoneDayRow(day, status, showAccount)).join('');
  return `<section class="recon-stone" aria-labelledby="recon-stone-title">
    ${header.replace('<h2 ', '<h2 id="recon-stone-title" ')}
    <div class="kpi-grid kpi-grid-4">
      ${kpi('Stone previu', money(sum.expected_cents), `Repasses vencidos no período · mais ${money(sum.upcoming_cents)} a receber`)}
      ${kpi('Caiu no banco', money(sum.received_cents), 'Créditos casados com os repasses')}
      ${kpi('Diferença', stoneSignedMoney(sum.difference_cents), sum.difference_cents < 0 ? 'Faltou dinheiro' : 'Sem falta', null, diffCls)}
      ${kpi('Dias conferidos', sum.ok_pct == null ? '—' : `${sum.ok_pct}%`, `${sum.ok_days} de ${sum.due_days} · ${verdict}`)}
    </div>
    <div class="recon-tabs" role="group" aria-label="Filtrar dias">
      ${STONE_TABS.map(([key, label]) => `<button type="button" class="btn-secondary" data-stone-tab-button="${key}" aria-pressed="${key === tab}">${label} <span class="tab-count">${counts[key]}</span></button>`).join('')}
    </div>
    <div class="table-wrap"><table class="data-table" id="recon-stone-table">
      <thead><tr><th>Dia do repasse</th>${showAccount ? '<th>Conta</th>' : ''}<th class="num">Stone previu</th><th class="num">Banco recebeu</th><th class="num">Diferença</th><th>Situação</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <p class="recon-tab-empty" id="recon-stone-empty" hidden>Nada nesta aba.</p>
  </section>`;
}

function applyStoneTab(tab) {
  RECONCILIATION_STATE.stoneTab = tab;
  let visible = 0;
  document.querySelectorAll('#recon-stone-table tbody tr').forEach((row) => {
    const show = tab === 'all' || row.dataset.stoneTab === tab;
    row.hidden = !show;
    if (show) visible += 1;
  });
  document.querySelectorAll('[data-stone-tab-button]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.stoneTabButton === tab));
  });
  const empty = document.getElementById('recon-stone-empty');
  if (empty) empty.hidden = visible > 0;
}

function bindReconciliationStoneTabs() {
  const buttons = document.querySelectorAll('[data-stone-tab-button]');
  if (!buttons.length) return;
  buttons.forEach((button) => button.addEventListener('click', () => applyStoneTab(button.dataset.stoneTabButton)));
  applyStoneTab(RECONCILIATION_STATE.stoneTab);
}

/* ---------------------------------------------------------------- fontes de dados */

function reconciliationSourceStatus(done, text) {
  return `<span class="${done ? 'badge-success' : 'badge-warning'}">${done ? 'Em dia' : 'Falta'}</span> <span class="source-detail">${text}</span>`;
}

function reconciliationSourcesSection(sources) {
  if (!sources.length) {
    return `<section class="recon-sources" aria-labelledby="recon-sources-title">
      <h2 id="recon-sources-title" class="section-header">Fontes de dados</h2>
      <div class="empty-state">
        <p><strong>Comece cadastrando a conta de caixa.</strong> É nela que entram as vendas Stone e o extrato do banco.</p>
        <button type="button" class="btn-primary" data-source-action="create-account">Criar conta de caixa</button>
      </div>
    </section>`;
  }
  const rows = sources.map((src) => {
    const stone = src.stone.count
      ? reconciliationSourceStatus(true, `Vendas até ${dateBR(src.stone.last_settlement_date)} · importado em ${dateBR(src.stone.last_import_at)}`)
      : reconciliationSourceStatus(false, 'Nunca importado');
    const bank = src.bank.count
      ? reconciliationSourceStatus(true, `${src.bank.count} movimento(s) · importado em ${dateBR(src.bank.last_import_at)}`)
      : reconciliationSourceStatus(false, 'Nunca importado');
    const id = esc(src.cash_account_id);
    return `<tr>
      <td><strong>${esc(src.name)}</strong></td>
      <td>${stone}<br><button type="button" class="btn-link" data-source-action="stone" data-account="${id}">Importar XML da Stone</button></td>
      <td>${bank}<br><button type="button" class="btn-link" data-source-action="bank" data-account="${id}">Importar extrato (OFX/CSV)</button></td>
    </tr>`;
  }).join('');
  return `<section class="recon-sources" aria-labelledby="recon-sources-title">
    <h2 id="recon-sources-title" class="section-header">Fontes de dados</h2>
    <p class="page-subtitle">Mantenha as duas fontes em dia: as vendas Stone dizem quanto deveria cair; o extrato diz quanto caiu.</p>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Conta</th><th>Vendas Stone (XML)</th><th>Extrato bancário</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <button type="button" class="btn-link" data-source-action="create-account">+ Nova conta de caixa</button>
  </section>`;
}

function bindReconciliationSources(cashAccounts, eventsById) {
  const refresh = () => renderConciliacao();
  document.querySelectorAll('[data-source-action]').forEach((button) => button.addEventListener('click', (ev) => {
    const trigger = ev.currentTarget;
    const action = trigger.dataset.sourceAction;
    const ctx = {trigger, onSaved: refresh, accountId: trigger.dataset.account, eventsById};
    if (action === 'create-account') openCashAccountForm(ctx);
    else if (action === 'stone') openStoneImportForm(cashAccounts, ctx);
    else if (action === 'bank') openBankImportForm(cashAccounts, ctx);
  }));
}

/* Multipart upload: api() always sends JSON, so files go through fetch directly
 * with the same credentials and CSRF header. */
async function reconciliationUpload(path, formData) {
  const res = await fetch(path, {
    method: 'POST', credentials: 'same-origin',
    headers: {'x-csrf-token': APP.csrf || ''},
    body: formData,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = body && body.detail;
    const message = typeof detail === 'string' ? detail : (detail && detail.message) || 'Não foi possível enviar o arquivo.';
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return body;
}

function reconciliationAccountOptions(cashAccounts) {
  return cashAccounts.map((a) => ({value: a.id, label: a.name}));
}

function importFormBody(cashAccounts, ctx, accept, fileLabel, help, submitLabel) {
  return `<form class="drawer-form" novalidate>
      <div class="form-grid">
        ${formField('cash_account_id', 'Conta de caixa', selectControl(reconciliationAccountOptions(cashAccounts),
          ctx.accountId || (cashAccounts.length === 1 ? cashAccounts[0].id : ''), 'Escolha…'))}
        ${formField('file', fileLabel, `<input type="file" class="login-input" accept="${accept}">`, help)}
      </div>
      ${formActions(submitLabel)}
    </form>`;
}

/* Reads the account + file of an import drawer; returns null after showing the error. */
function readImportForm(form, ui) {
  const accountId = form.elements.namedItem('cash_account_id').value;
  const fileInput = form.elements.namedItem('file');
  const file = fileInput.files && fileInput.files[0];
  if (!accountId) { ui.showError('Escolha a conta de caixa.', ['cash_account_id']); return null; }
  if (!file) { ui.showError('Escolha o arquivo.', ['file']); return null; }
  return {accountId, file};
}

function openStoneImportForm(cashAccounts, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({title: 'Importar vendas Stone', trigger: ctx.trigger, body: importFormBody(
    cashAccounts, ctx, '.xml', 'Arquivo XML', 'No portal Stone: Conciliação → baixar arquivo, layout 2.4 (XML). Reimportar o mesmo arquivo não duplica nada.',
    'Importar vendas')});
  const form = drawer.dialog.querySelector('form');
  const ui = formUiFor(form);
  watchForm(form);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    ui.clearError();
    const picked = readImportForm(form, ui);
    if (!picked) return;
    const data = new FormData();
    data.append('file', picked.file);
    ui.setBusy(true);
    try {
      const result = await reconciliationUpload(`${financeBasePath()}/cash-accounts/${picked.accountId}/receivables-import`, data);
      clearDirty();
      drawer.setBody(`<p role="status"><strong>Importação concluída.</strong></p>
        <p>${result.imported} venda(s) nova(s) · ${result.duplicates} já existiam.</p>
        <div class="btn-row drawer-actions"><button type="button" class="btn-primary btn-wide" data-drawer-close>Fechar</button></div>`);
      if (ctx.onSaved) ctx.onSaved(result);
    } catch (e) {
      ui.showError(e.message, ['file']);
    } finally {
      if (form.isConnected) ui.setBusy(false);
    }
  });
}

function reconciliationCandidateLabel(eventId, eventsById) {
  const event = eventsById && eventsById[String(eventId)];
  return event ? `Já lançado: ${dateBR(event.occurred_at)} ${event.description}` : `Já lançado (movimento ${eventId})`;
}

function bankPreviewBody(preview, eventsById) {
  const items = preview.items;
  const linkable = items.filter((i) => i.candidate_cash_event_ids.length).length;
  const rows = items.map((item, index) => {
    const options = [{value: 'new', label: 'Novo movimento'}].concat(
      item.candidate_cash_event_ids.map((id) => ({value: `link:${id}`, label: reconciliationCandidateLabel(id, eventsById)})));
    const control = options.length === 1 ? '<span class="badge-muted">Novo movimento</span>'
      : `<label class="visually-hidden" for="bank-decision-${index}">O que é esta linha</label>
        <select id="bank-decision-${index}" class="login-input" data-external-id="${esc(item.external_id)}">
          ${options.map((o) => `<option value="${esc(o.value)}"${o.value === item.decision ? ' selected' : ''}>${esc(o.label)}</option>`).join('')}
        </select>`;
    return `<tr>
      <td>${dateBR(item.date)}</td>
      <td>${esc(item.description || '—')}</td>
      <td class="num">${money(item.amount_cents)}</td>
      <td>${control}</td>
    </tr>`;
  }).join('');
  return `<form class="drawer-form" novalidate>
      <p class="recon-preview-summary"><strong>${preview.count} linha(s)</strong> de ${dateBR(preview.start)} a ${dateBR(preview.end)} · saldo do período ${money(preview.total_cents)}</p>
      <p class="field-help">${linkable
        ? `${linkable} linha(s) parecem ser movimentos que já estão no painel (ex.: um pagamento registrado). Confira a coluna "O que é" para não lançar em dobro.`
        : 'Nenhuma linha coincide com movimentos já lançados: todas entram como novas.'}</p>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th>Data</th><th>Descrição</th><th class="num">Valor</th><th>O que é</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="4">O arquivo não tem movimentos.</td></tr>'}</tbody>
      </table></div>
      ${formActions(`Importar ${preview.count} linha(s)`)}
    </form>`;
}

/* Two steps in one drawer: pick account + file → review each line (new vs.
 * already recorded) → commit with the preview hash, so a different file can
 * never be committed against this review. */
function openBankImportForm(cashAccounts, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({title: 'Importar extrato bancário', trigger: ctx.trigger, body: importFormBody(
    cashAccounts, ctx, '.ofx,.csv', 'Arquivo OFX ou CSV', 'Baixe o extrato no internet banking. Antes de gravar, você revisa cada linha.',
    'Revisar linhas')});
  const form = drawer.dialog.querySelector('form');
  const ui = formUiFor(form);
  watchForm(form);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    ui.clearError();
    const picked = readImportForm(form, ui);
    if (!picked) return;
    const data = new FormData();
    data.append('file', picked.file);
    ui.setBusy(true);
    let preview;
    try {
      preview = await reconciliationUpload(`${financeBasePath()}/cash-accounts/${picked.accountId}/bank-imports/preview`, data);
    } catch (e) {
      ui.showError(e.message, ['file']);
      ui.setBusy(false);
      return;
    }
    drawer.setBody(bankPreviewBody(preview, ctx.eventsById));
    bindBankCommit(drawer, picked, preview, ctx);
  });
}

function bindBankCommit(drawer, picked, preview, ctx) {
  const form = drawer.dialog.querySelector('form');
  const ui = formUiFor(form);
  const submit = form.querySelector('button[type="submit"]');
  if (submit) submit.focus();
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    ui.clearError();
    const decisions = {};
    preview.items.forEach((item) => { decisions[item.external_id] = item.decision; });
    form.querySelectorAll('select[data-external-id]').forEach((select) => { decisions[select.dataset.externalId] = select.value; });
    const data = new FormData();
    data.append('file', picked.file);
    data.append('preview_hash', preview.preview_hash);
    data.append('decisions', JSON.stringify(decisions));
    ui.setBusy(true);
    try {
      const result = await reconciliationUpload(`${financeBasePath()}/cash-accounts/${picked.accountId}/bank-imports`, data);
      clearDirty();
      drawer.setBody(`<p role="status"><strong>Extrato importado.</strong></p>
        <p>${result.imported} movimento(s) novo(s) · ${result.linked} ligado(s) a lançamentos existentes · ${result.duplicates} já importado(s) antes.</p>
        <div class="btn-row drawer-actions"><button type="button" class="btn-primary btn-wide" data-drawer-close>Conferir movimentos</button></div>`);
      if (ctx.onSaved) ctx.onSaved(result);
    } catch (e) {
      ui.showError(e.status === 409 ? `${e.message} Feche e importe o arquivo de novo.` : e.message);
      ui.setBusy(false);
    }
  });
}

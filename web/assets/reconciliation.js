/* Conciliação: sugere e confirma o casamento entre lançamentos de caixa e
 * lançamentos financeiros (um crédito pode fechar várias vendas e taxas).
 * Loaded before app.js and uses its shared api(), esc(), money(), APP globals
 * plus finance.js's dateBR()/financeError(). */
'use strict';

const RECONCILIATION_STATE = {groups: []};

const RECONCILIATION_STATUS_LABELS = {
  unmatched: 'Sem correspondência', suggested: 'Sugestão pendente', auto_matched: 'Conciliado automaticamente',
  manual_matched: 'Conciliado manualmente', partial: 'Parcial', divergent: 'Divergente', ignored: 'Ignorado',
};

async function renderConciliacao(token) {
  token = token || beginPage();
  const title = `${icon('circle-check', {class: 'title-icon'})}Conciliação`;
  const subtitle = 'Casa lançamentos de caixa com lançamentos financeiros — um crédito pode fechar várias vendas.';
  financeLoading(title, subtitle, 'Carregando conciliação');
  let events, groups;
  try {
    [events, groups] = await Promise.all([
      api(`/api/companies/${APP.company}/finance/cash-events`),
      api(`/api/companies/${APP.company}/finance/reconciliation`),
    ]);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;
    return financeError(title, subtitle, e, () => renderConciliacao());
  }
  if (!APP.pageState.isCurrent(token)) return;

  const linkedEventIds = new Set(
    groups.flatMap((g) => g.links.filter((l) => l.item_type === 'cash_event').map((l) => String(l.item_id)))
  );
  const pending = events.filter((e) => !linkedEventIds.has(String(e.id)));
  const pendingOptions = pending.map((e) =>
    `<option value="${esc(e.id)}">${dateBR(e.occurred_at)} — ${esc(e.description)} — ${money(e.amount_cents)}</option>`).join('');

  const groupRows = groups.map((g) => {
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
    <hr class="divider">
    <div class="settings-block">
      <h2>Sugerir conciliação</h2>
      <form id="reconciliation-suggest-form" class="inline-form">
        <div><label class="field-label" for="reconciliation-search">Buscar movimento</label>
          <input id="reconciliation-search" type="search" class="login-input" autocomplete="off" placeholder="Descrição, data ou valor"></div>
        <div><label class="field-label" for="reconciliation-event">Movimento de caixa</label>
          <select id="reconciliation-event" class="login-input" required>${pendingOptions || '<option value="">Nenhum lançamento pendente</option>'}</select></div>
        <button type="submit" class="btn-primary">Sugerir</button>
        <span id="reconciliation-suggest-status" class="sim-status" role="status" aria-live="polite"></span>
      </form>
    </div>
    <div id="reconciliation-action-status" class="form-error" role="alert"></div>
    <h2 class="section-header">Grupos de conciliação</h2>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Lançamento de caixa</th><th>Lançamentos financeiros</th><th>Diferença</th><th>Status</th><th></th></tr></thead>
      <tbody>${groupRows || '<tr><td colspan="5">Nenhuma conciliação registrada.</td></tr>'}</tbody>
    </table></div>`;

  RECONCILIATION_STATE.groups = groups;
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

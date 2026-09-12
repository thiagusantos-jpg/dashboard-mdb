/* Conciliação: sugere e confirma o casamento entre lançamentos de caixa e
 * lançamentos financeiros (um crédito pode fechar várias vendas e taxas).
 * Loaded before app.js and uses its shared api(), esc(), money(), APP globals
 * plus finance.js's dateBR()/financeError(). */
'use strict';

const RECONCILIATION_STATUS_LABELS = {
  unmatched: 'Sem correspondência', suggested: 'Sugestão pendente', auto_matched: 'Conciliado automaticamente',
  manual_matched: 'Conciliado manualmente', partial: 'Parcial', divergent: 'Divergente', ignored: 'Ignorado',
};

async function renderConciliacao(token) {
  token = token || beginPage();
  const title = '✅ Conciliação';
  const subtitle = 'Casa lançamentos de caixa com lançamentos financeiros — um crédito pode fechar várias vendas.';
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <div class="skeleton-block" aria-label="Carregando conciliação"></div>`;
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
      <td>${anchor ? money(anchor.amount_cents) : '—'}</td>
      <td>${entryLinks.length} lançamento(s)</td>
      <td>${money(g.difference_cents)}</td>
      <td>${esc(RECONCILIATION_STATUS_LABELS[g.status] || g.status)}</td>
      <td>${needsReview ? `<form class="inline-form" data-confirm-form="${esc(g.id)}">
            <select class="login-input">
              ${g.candidates.map((c, i) => `<option value="${i}">Opção ${i + 1}: ${money(c.total_cents)} (${c.entry_ids.length} lançamentos)</option>`).join('')}
            </select>
            <button type="submit" class="btn-secondary">Confirmar</button>
          </form>` : (g.status !== 'ignored' ? `<form class="inline-form" data-undo-form="${esc(g.id)}">
            <input type="text" class="login-input" placeholder="Motivo do desfazimento" maxlength="500" required>
            <button type="submit" class="btn-secondary">Desfazer</button>
          </form>` : '')}
      </td>
    </tr>`;
  }).join('');

  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <hr class="divider">
    <div class="settings-block">
      <h2>Sugerir conciliação</h2>
      <form id="reconciliation-suggest-form" class="inline-form">
        <select id="reconciliation-event" class="login-input" required>${pendingOptions || '<option value="">Nenhum lançamento pendente</option>'}</select>
        <button type="submit" class="btn-primary">Sugerir</button>
        <span id="reconciliation-suggest-status" class="sim-status" role="status" aria-live="polite"></span>
      </form>
    </div>
    <div class="section-header">Grupos de conciliação</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Lançamento de caixa</th><th>Lançamentos financeiros</th><th>Diferença</th><th>Status</th><th></th></tr></thead>
      <tbody>${groupRows || '<tr><td colspan="5">Nenhuma conciliação registrada.</td></tr>'}</tbody>
    </table></div>`;

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

async function onConfirmReconciliation(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const groupId = form.dataset.confirmForm;
  const group = (await api(`/api/companies/${APP.company}/finance/reconciliation`))
    .find((g) => String(g.id) === String(groupId));
  const optionIndex = parseInt(form.querySelector('select').value, 10);
  const entryIds = group.candidates[optionIndex].entry_ids;
  try {
    await api(`/api/companies/${APP.company}/finance/reconciliation/${groupId}/confirm`, {
      method: 'POST',
      body: JSON.stringify({entry_ids: entryIds}),
    });
    renderConciliacao();
  } catch (e) {
    alert('Erro ao confirmar: ' + e.message);
  }
}

async function onUndoReconciliation(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const groupId = form.dataset.undoForm;
  const reason = form.querySelector('input').value.trim();
  try {
    await api(`/api/companies/${APP.company}/finance/reconciliation/${groupId}/undo`, {
      method: 'POST',
      body: JSON.stringify({reason}),
    });
    renderConciliacao();
  } catch (e) {
    alert('Erro ao desfazer: ' + e.message);
  }
}

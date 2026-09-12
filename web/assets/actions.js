/* Central de Ações: acompanha alertas transformados em ações rastreáveis
 * (Task 19). Loaded before app.js and uses its shared api(), esc(), dt(), APP globals. */
'use strict';

const ACTION_STATUS_LABELS = {
  open: 'Em aberto', in_progress: 'Em andamento', resolved: 'Resolvida', dismissed: 'Descartada',
};

const ACTION_TRANSITIONS = {
  open: [['in_progress', 'Assumir'], ['resolved', 'Resolver'], ['dismissed', 'Descartar']],
  in_progress: [['resolved', 'Resolver'], ['dismissed', 'Descartar'], ['open', 'Reabrir']],
  resolved: [],
  dismissed: [],
};

async function renderAcoes(data, token) {
  token = token || beginPage();
  const title = '📣 Central de Ações';
  const subtitle = 'Alertas viram ações rastreáveis — quem assumiu, o que foi feito, e o resultado observado.';
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <div class="skeleton-block" aria-label="Carregando ações"></div>`;
  let list;
  try {
    list = await api(`/api/companies/${APP.company}/actions`);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;  // usuário já saiu desta rota
    document.getElementById('content').innerHTML = `
      <div class="page-title">${title}</div>
      <div class="story-box mt-16">Não foi possível carregar: ${esc(e.message)}</div>`;
    return;
  }
  if (!APP.pageState.isCurrent(token)) return;  // resposta obsoleta: descarta em silêncio
  const rows = list.map((a) => {
    const transitions = ACTION_TRANSITIONS[a.status] || [];
    const buttons = transitions.map(([to, label]) =>
      `<button type="button" class="btn-secondary" data-action-transition="${esc(a.id)}" data-to-status="${to}">${esc(label)}</button>`).join(' ');
    return `<tr>
      <td>${esc(a.title)}</td>
      <td>${esc(a.priority)}</td>
      <td>${esc(ACTION_STATUS_LABELS[a.status] || a.status)}</td>
      <td>${dt(a.created_at)}</td>
      <td>${buttons}</td>
    </tr>`;
  }).join('');
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <hr class="divider">
    <div class="table-wrap"><table>
      <thead><tr><th>Ação</th><th>Prioridade</th><th>Status</th><th>Criada em</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5">Nenhuma ação criada ainda — use "Criar ação" nos alertas do Resumo Executivo.</td></tr>'}</tbody>
    </table></div>`;
  document.querySelectorAll('[data-action-transition]').forEach((btn) => btn.addEventListener('click', onTransitionAction));
}

async function onTransitionAction(event) {
  const btn = event.currentTarget;
  const actionId = btn.dataset.actionTransition;
  const toStatus = btn.dataset.toStatus;
  btn.disabled = true;
  try {
    await api(`/api/companies/${APP.company}/actions/${actionId}/transition`, {
      method: 'POST',
      body: JSON.stringify({to_status: toStatus}),
    });
    renderAcoes();
  } catch (e) {
    alert('Erro ao atualizar ação: ' + e.message);
    btn.disabled = false;
  }
}

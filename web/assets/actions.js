/* Central de Ações: acompanha alertas transformados em ações rastreáveis
 * (Task 19). Loaded before app.js and uses its shared api(), esc(), dt(), APP globals. */
'use strict';

/* ---------------------------------------------------------------- Regras puras
 * No DOM here: everything below is exercised by tests/test_actions_center_frontend.cjs.
 * Globals from app.js/insights.js (esc, num, money, pct, icon, routeHash, ALERT_FILTERS,
 * sentenceCase) are only read inside functions — this file loads before app.js. */

const ACTION_ORIGINS = {
  estoque: {label: 'Estoque', icon: 'package'},
  preco: {label: 'Preço', icon: 'tag'},
  custo: {label: 'Custo', icon: 'dollar-sign'},
  integracao: {label: 'Integração', icon: 'link'},
  mapa: {label: 'Mapa de produtos', icon: 'map'},
  manual: {label: 'Manual', icon: 'clipboard-list'},
  fechamento: {label: 'Fechamento do mês', icon: 'calendar'},
  vencimento: {label: 'Conta a pagar', icon: 'calendar'},
};
const ACTION_PRIORITY_LABELS = {high: 'Alta', medium: 'Média', low: 'Baixa'};
const ACTION_PRIORITY_RANK = {high: 0, medium: 1, low: 2};
const ACTION_TABS = [['pendentes', 'Pendentes'], ['concluidas', 'Concluídas'], ['descartadas', 'Descartadas'], ['todas', 'Todas']];
const ACTION_TAB_STATUSES = {
  pendentes: ['open', 'in_progress'],
  concluidas: ['resolved'],
  descartadas: ['dismissed'],
  todas: ['open', 'in_progress', 'resolved', 'dismissed'],
};
// Resumo alerts are stored with their whole message; the card shows this instead.
const ACTION_SHORT_TITLES = {
  estoque: (n) => `${n} ${n === 1 ? 'produto' : 'produtos'} da curva A sem estoque`,
  preco: (n) => `${n} ${n === 1 ? 'produto vendendo' : 'produtos vendendo'} abaixo do custo`,
  custo: (n) => `${n} ${n === 1 ? 'item vendido' : 'itens vendidos'} sem custo conhecido`,
};

const isPendingAction = (a) => a.status === 'open' || a.status === 'in_progress';

function isoDay(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function shiftIsoDay(iso, days) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return isoDay(d);
}

function daysBetween(a, b) {
  return Math.round((Date.parse(`${b}T12:00:00`) - Date.parse(`${a}T12:00:00`)) / 86400000);
}

function shortDay(iso) {
  return `${String(iso).slice(8, 10)}/${String(iso).slice(5, 7)}`;
}

function actionAge(timestamp, today) {
  const d = daysBetween(isoDay(new Date(timestamp)), today);
  return d <= 0 ? 'hoje' : d === 1 ? 'ontem' : `há ${d} dias`;
}

function actionOrigin(alertKey) {
  const base = String(alertKey || '').split(':')[0];
  return ACTION_ORIGINS[base] ? base : 'manual';
}

// Count at creation; actions older than baseline_count still start with it ("170 produto(s)…").
function actionBaseline(a) {
  if (a.baseline_count != null) return Number(a.baseline_count);
  const m = String(a.title || '').match(/^(\d+)\s/);
  return m ? Number(m[1]) : null;
}

function actionUnit(a) {
  return a.alert_key === 'custo' ? ['item', 'itens'] : ['produto', 'produtos'];
}

function actionDisplay(a) {
  const n = actionBaseline(a);
  const short = ACTION_SHORT_TITLES[a.alert_key];
  const text = String(a.title || '');
  const at = text.indexOf(': ');
  const names = short && at >= 0
    ? text.slice(at + 2).replace(/[….]+$/, '').split(', ').map((s) => sentenceCase(s.trim())).filter(Boolean)
    : [];
  const products = names.slice(0, 3);
  return {
    title: short && n != null ? short(n) : text,
    products,
    more: n != null && names.length ? Math.max(0, n - products.length) : 0,
  };
}

// How the problem behind an action looks in the newest synced month (null = nothing to compare).
function actionLiveState(a, data) {
  if (!data) return null;
  const key = String(a.alert_key || '');
  if (ACTION_SHORT_TITLES[key]) {
    const alert = (data.alerts || []).find((x) => x.type === key);
    const now = !alert ? 0 : alert.count != null ? alert.count : actionBaseline({title: alert.message});
    return {kind: 'count', now, was: actionBaseline(a), gone: !alert};
  }
  if (key === 'integracao') {
    return (data.alerts || []).some((x) => x.type === 'integracao') ? null : {kind: 'count', now: 0, was: null, gone: true};
  }
  if (key === 'mapa:gerador' || key === 'mapa:baixo-giro') {
    const group = key === 'mapa:gerador' ? 'Gerador de caixa' : 'Baixo giro';
    const products = (data.product_map ? data.product_map.products : data.products) || [];
    // Same exclusion as the Mapa de produtos: a zero cost fakes a 100% margin.
    const now = products.filter((p) => !(p.cost === 0 && p.revenue > 0) && p.classification === group).length;
    return {kind: 'count', now, was: actionBaseline(a), gone: now === 0};
  }
  const price = key.match(/^preco:(\d+)$/);
  if (price) {
    const item = (data.inventory || []).find((p) => String(p.id) === price[1]);
    if (!item || item.current_price == null || item.current_cost == null) return null;
    const margin = item.current_price ? (item.current_price - item.current_cost) / item.current_price * 100 : null;
    return {kind: 'price', price: item.current_price, cost: item.current_cost, margin};
  }
  return null;
}

function actionLiveHtml(live, unit, closed) {
  if (!live) return '';
  if (live.kind === 'price') {
    return `<p class="action-live">Hoje no Mobne: preço <b>${money(live.price)}</b>, custo <b>${money(live.cost)}</b>${
      live.margin == null ? '' : `, margem de <b>${pct(live.margin)}</b>`}</p>`;
  }
  if (live.gone) {
    return `<p class="action-live done">${icon('circle-check')} ${closed
      ? 'O alerta não voltou a aparecer no painel' : 'O alerta não aparece mais no painel: pode concluir'}</p>`;
  }
  const words = (n) => `${num(n)} ${n === 1 ? unit[0] : unit[1]}`;
  const diff = live.was == null ? null : live.now - live.was;
  const delta = diff == null ? ''
    : diff === 0 ? `<span class="action-delta flat">${closed ? 'igual a quando foi criada' : 'sem mudança desde a criação'}</span>`
      : `<span class="action-delta ${diff < 0 ? 'good' : 'bad'}">${diff > 0 ? '+' : '−'}${num(Math.abs(diff))} desde a criação</span>`;
  return `<p class="action-live">No painel hoje: <b>${words(live.now)}</b> ${delta}</p>`;
}

function actionSource(a, period) {
  const key = String(a.alert_key || '');
  if (ALERT_FILTERS[key]) {
    return {href: routeHash('estoque', period, new URLSearchParams({filtro: ALERT_FILTERS[key]})), label: 'Ver produtos'};
  }
  if (key === 'integracao') return {href: '#/configuracoes/integracoes', label: 'Abrir integrações'};
  if (key.startsWith('vencimento:')) return {href: routeHash('contas-pagar'), label: 'Abrir Contas a pagar'};
  const closing = key.match(/^fechamento:(\d{4}-\d{2})$/);
  if (closing) return {href: routeHash('financeiro', closing[1]), label: 'Abrir Resultado gerencial'};
  if (key.startsWith('mapa:')) return {href: routeHash('mapa', period), label: 'Abrir no mapa'};
  const price = key.match(/^preco:(\d+)$/);
  if (price) return {href: `#/produto/${period}?id=${price[1]}`, label: 'Ver produto'};
  return null;
}

// The same alert already handled before this action was opened ("Já tratado antes").
function previousHandled(a, list) {
  return (list || [])
    .filter((x) => String(x.id) !== String(a.id) && x.alert_key === a.alert_key && !isPendingAction(x)
      && x.resolved_at && x.resolved_at <= a.created_at)
    .sort((x, y) => (x.resolved_at < y.resolved_at ? 1 : -1))[0] || null;
}

function isActionOverdue(a, today) {
  return isPendingAction(a) && !!a.due_date && a.due_date < today;
}

function actionStats(list, today) {
  const since = shiftIsoDay(today, -30);
  return {
    open: list.filter((a) => a.status === 'open').length,
    inProgress: list.filter((a) => a.status === 'in_progress').length,
    overdue: list.filter((a) => isActionOverdue(a, today)).length,
    resolved30: list.filter((a) => a.status === 'resolved' && a.resolved_at && isoDay(new Date(a.resolved_at)) >= since).length,
  };
}

function filterActions(list, tab, origin, today) {
  return (list || []).filter((a) => {
    if (origin && actionOrigin(a.alert_key) !== origin) return false;
    if (tab === 'atrasadas') return isActionOverdue(a, today);
    return (ACTION_TAB_STATUSES[tab] || ACTION_TAB_STATUSES.pendentes).includes(a.status);
  });
}

// Overdue first, then priority, then newest.
function sortActions(list, today) {
  return list.slice().sort((x, y) => (isActionOverdue(y, today) - isActionOverdue(x, today))
    || ((ACTION_PRIORITY_RANK[x.priority] ?? 1) - (ACTION_PRIORITY_RANK[y.priority] ?? 1))
    || (x.created_at < y.created_at ? 1 : x.created_at > y.created_at ? -1 : 0));
}

function pendingActionCount(list) {
  return (list || []).filter(isPendingAction).length;
}

// What deserves a line on the Resumo: overdue actions and pending ones untouched for a week.
function actionDigest(list, today) {
  const pending = (list || []).filter(isPendingAction);
  const staleSince = shiftIsoDay(today, -7);
  const oldest = pending.slice().sort((x, y) => (x.created_at < y.created_at ? -1 : 1))[0];
  return {
    pending: pending.length,
    overdue: pending.filter((a) => isActionOverdue(a, today)).length,
    stale: pending.filter((a) => !isActionOverdue(a, today) && isoDay(new Date(a.updated_at)) <= staleSince).length,
    oldestDays: oldest ? daysBetween(isoDay(new Date(oldest.created_at)), today) : 0,
  };
}

function actionsNoticeText(digest) {
  const bits = [];
  if (digest.overdue) bits.push(`${digest.overdue} ${digest.overdue === 1 ? 'ação atrasada' : 'ações atrasadas'}`);
  if (digest.stale) bits.push(`${digest.stale} ${digest.stale === 1 ? 'parada' : 'paradas'} há mais de 7 dias`);
  return bits.join(' · ');
}

/* ---------------------------------------------------------------- Página */

const ACTIONS_STATE = {list: [], people: [], data: null, tab: 'pendentes', origin: ''};
const ACTION_GROUP_LABELS = {open: 'A fazer', in_progress: 'Em andamento', resolved: 'Concluídas', dismissed: 'Descartadas'};
const ACTION_EMPTY = {
  open: 'Nada esperando alguém assumir.',
  in_progress: 'Ninguém está cuidando de uma ação agora.',
  resolved: 'Nenhuma ação concluída.',
  dismissed: 'Nenhuma ação descartada.',
};

function actionsHeader() {
  return `<div class="actions-head">
    <div>
      <h1 class="page-title">Central de ações</h1>
      <p class="actions-lead">O que precisa ser resolvido, quem está cuidando e o que já foi feito.</p>
    </div>
    <button type="button" class="btn-primary" data-new-action>Nova ação</button>
  </div>`;
}

async function renderAcoes(data, token) {
  token = token || beginPage();
  const params = APP.routeParams || new URLSearchParams();
  const tab = params.get('situacao');
  const origin = params.get('origem');
  Object.assign(ACTIONS_STATE, {
    data: data || APP.dashboard || null,
    tab: tab === 'atrasadas' || ACTION_TAB_STATUSES[tab] ? tab : 'pendentes',
    origin: ACTION_ORIGINS[origin] ? origin : '',
  });
  const content = document.getElementById('content');
  content.innerHTML = `<div class="actions-page">${actionsHeader()}<div class="skeleton-block" aria-label="Carregando ações"></div></div>`;
  let list, people;
  try {
    [list, people] = await Promise.all([
      api(`/api/companies/${APP.company}/actions`),
      api(`/api/companies/${APP.company}/actions/assignees`).catch(() => []),
    ]);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;  // usuário já saiu desta rota
    content.innerHTML = `<div class="actions-page">${actionsHeader()}
      <div class="story-box">Não foi possível carregar as ações: ${esc(e.message)}</div></div>`;
    return;
  }
  if (!APP.pageState.isCurrent(token)) return;  // resposta obsoleta: descarta em silêncio
  Object.assign(ACTIONS_STATE, {list, people});
  updateActionsBadge(list);
  content.innerHTML = `<div class="actions-page">
    ${actionsHeader()}
    <div class="actions-stats" id="actions-stats"></div>
    <div class="actions-toolbar" id="actions-toolbar"></div>
    <p class="actions-status" id="actions-status" role="status" aria-live="polite"></p>
    <div class="actions-body" id="actions-body"></div>
  </div>`;
  const page = content.querySelector('.actions-page');
  page.addEventListener('click', onActionsClick);
  page.addEventListener('change', (ev) => {
    if (ev.target.id === 'actions-origin') setActionsFilter(ACTIONS_STATE.tab, ev.target.value);
  });
  renderActionsBody();
}

function renderActionsBody() {
  const s = ACTIONS_STATE;
  const statsEl = document.getElementById('actions-stats');
  if (!statsEl) return;  // the partner already left the Central
  const today = isoDay(new Date());
  const stats = actionStats(s.list, today);
  const stat = (tab, n, label, sub, pressed, extra) => `<button type="button" class="action-stat${extra}" data-actions-tab="${tab}" aria-pressed="${pressed}">
      <span class="action-stat-value">${num(n)}</span><span class="action-stat-label">${label}</span><span class="action-stat-sub">${sub}</span></button>`;
  statsEl.innerHTML =
    stat('pendentes', stats.open, 'A fazer', 'ninguém assumiu ainda', false, '') +
    stat('pendentes', stats.inProgress, 'Em andamento', 'alguém está cuidando', false, '') +
    stat('atrasadas', stats.overdue, 'Atrasadas', stats.overdue ? 'passaram do prazo' : 'nenhuma no momento', s.tab === 'atrasadas', stats.overdue ? ' alert' : '') +
    stat('concluidas', stats.resolved30, 'Concluídas', 'nos últimos 30 dias', s.tab === 'concluidas', '');

  const present = new Set(s.list.map((a) => actionOrigin(a.alert_key)));
  document.getElementById('actions-toolbar').innerHTML = `
    <div class="actions-tabs-scroll"><div class="actions-tabs" role="group" aria-label="Situação">${ACTION_TABS.map(([key, label]) =>
      `<button type="button" class="actions-tab" data-actions-tab="${key}" aria-pressed="${s.tab === key}">${label}
        <span class="actions-tab-count">${num(filterActions(s.list, key, s.origin, today).length)}</span></button>`).join('')}</div></div>
    <label class="visually-hidden" for="actions-origin">Origem</label>
    <select id="actions-origin" class="actions-select">
      <option value="">Todas as origens</option>
      ${Object.entries(ACTION_ORIGINS).filter(([key]) => present.has(key) || key === s.origin)
        .map(([key, o]) => `<option value="${key}"${s.origin === key ? ' selected' : ''}>${esc(o.label)}</option>`).join('')}
    </select>
    ${s.tab === 'atrasadas' ? '<span class="actions-filter-note">Só atrasadas <button type="button" class="btn-link" data-actions-tab="pendentes">limpar filtro</button></span>' : ''}`;

  const visible = filterActions(s.list, s.tab, s.origin, today);
  const statuses = ACTION_TAB_STATUSES[s.tab] || ACTION_TAB_STATUSES.pendentes;
  document.getElementById('actions-body').innerHTML = !s.list.length
    ? `<div class="actions-empty">Nenhuma ação criada ainda. Use “Criar ação” nos pontos de atenção do Resumo executivo,
        em Preços e margens ou no Mapa de produtos, ou clique em “Nova ação”.</div>`
    : statuses.map((status) => {
      const items = sortActions(visible.filter((a) => a.status === status), today);
      const terminal = status === 'resolved' || status === 'dismissed';
      const body = !items.length ? `<div class="actions-empty">${ACTION_EMPTY[status]}</div>`
        : terminal ? `<div class="action-done-list">${items.map((a) => actionRowHtml(a, today)).join('')}</div>`
          : items.map((a) => actionCardHtml(a, today)).join('');
      return `<section class="action-group" aria-labelledby="action-group-${status}">
        <h2 class="action-group-title" id="action-group-${status}">${ACTION_GROUP_LABELS[status]}
          <span class="action-group-count">${num(items.length)}</span></h2>${body}</section>`;
    }).join('');
  loadActionResults();
}

function actionCardHtml(a, today) {
  const s = ACTIONS_STATE;
  const origin = ACTION_ORIGINS[actionOrigin(a.alert_key)];
  const view = actionDisplay(a);
  const period = s.data ? s.data.period : APP.period;
  const source = actionSource(a, period);
  const previous = previousHandled(a, s.list);
  const late = isActionOverdue(a, today) ? daysBetween(a.due_date, today) : 0;
  const chips = view.products.length ? `<ul class="action-chips" aria-label="Alguns produtos">${
    view.products.map((p) => `<li title="${esc(p)}">${esc(p)}</li>`).join('')}${view.more ? `<li class="more">+${num(view.more)}</li>` : ''}</ul>` : '';
  const initial = (a.assignee_name || '?').trim().charAt(0).toUpperCase() || '?';
  const who = a.assignee
    ? `<span class="action-who"><span class="action-avatar" aria-hidden="true">${esc(initial)}</span>${esc(a.assignee_name || 'Responsável')}</span>`
    : '<span class="action-who none">Sem responsável</span>';
  const due = a.due_date
    ? `<span class="action-due${late ? ' late' : ''}">${icon('calendar')} Prazo ${shortDay(a.due_date)}${late ? ` · atrasada ${late} ${late === 1 ? 'dia' : 'dias'}` : ''}</span>`
    : '';
  const primary = a.status === 'open'
    ? `<button type="button" class="btn-primary" data-action-take="${esc(a.id)}">Assumir</button>`
    : `<button type="button" class="btn-primary" data-action-conclude="${esc(a.id)}">Concluir</button>`;
  const secondStep = a.status === 'open'
    ? `<button type="button" data-action-conclude="${esc(a.id)}">Concluir</button>`
    : `<button type="button" data-action-reopen="${esc(a.id)}">Voltar para “A fazer”</button>`;
  return `<article class="action-card" id="action-${esc(a.id)}">
    <div class="action-origin-icon" aria-hidden="true">${icon(origin.icon, {size: 20})}</div>
    <div class="action-main">
      <div class="action-top"><span>${esc(origin.label)}</span>
        <span class="action-priority ${esc(a.priority)}">${esc(ACTION_PRIORITY_LABELS[a.priority] || a.priority)}</span></div>
      <h3 class="action-title">${esc(view.title)}</h3>
      ${chips}
      ${actionLiveHtml(actionLiveState(a, s.data), actionUnit(a), false)}
      <p class="action-meta"><span>Criada ${actionAge(a.created_at, today)}</span>${who}${due}</p>
      ${previous ? `<p class="action-previous">${icon('link')} Já tratado antes: ${previous.status === 'resolved' ? 'concluída' : 'descartada'}
        em ${shortDay(isoDay(new Date(previous.resolved_at)))} ·
        <button type="button" class="btn-link" data-action-history="${esc(previous.id)}">ver histórico</button></p>` : ''}
    </div>
    <div class="action-side">
      ${primary}
      ${source ? `<a class="action-source" href="${esc(source.href)}">${esc(source.label)} →</a>` : ''}
      <details class="action-menu">
        <summary class="btn-secondary">Mais<span class="visually-hidden"> opções para esta ação</span></summary>
        <div class="action-menu-list">
          ${secondStep}
          <button type="button" data-action-edit="${esc(a.id)}">Responsável e prazo</button>
          <button type="button" data-action-history="${esc(a.id)}">Histórico</button>
          <button type="button" class="danger" data-action-dismiss="${esc(a.id)}">Descartar…</button>
        </div>
      </details>
    </div>
  </article>`;
}

function actionRowHtml(a, today) {
  const origin = ACTION_ORIGINS[actionOrigin(a.alert_key)];
  const resolved = a.status === 'resolved';
  const live = actionLiveState(a, ACTIONS_STATE.data);
  return `<div class="action-done" id="action-${esc(a.id)}">
    <div class="action-origin-icon small" aria-hidden="true">${icon(origin.icon)}</div>
    <div class="action-done-text">
      <p class="action-done-title">${esc(actionDisplay(a).title)}</p>
      <p class="action-done-note">${a.status_note
        ? `<b>${resolved ? 'O que foi feito:' : 'Motivo:'}</b> ${esc(a.status_note)}` : 'Sem anotação'}</p>
      ${resolved && live && live.kind === 'count' ? actionLiveHtml(live, actionUnit(a), true) : ''}
      <div class="action-result" data-action-result="${esc(a.id)}" hidden></div>
    </div>
    <div class="action-done-side">
      <span class="action-status ${esc(a.status)}">${resolved ? 'Concluída' : 'Descartada'} em ${a.resolved_at ? shortDay(isoDay(new Date(a.resolved_at))) : '—'}</span>
      <button type="button" class="btn-secondary" data-action-history="${esc(a.id)}">Histórico</button>
    </div>
  </div>`;
}

function onActionsClick(event) {
  const target = event.target.closest('button');
  if (!target) return;
  const d = target.dataset;
  if (d.actionsTab) return setActionsFilter(d.actionsTab, ACTIONS_STATE.origin);
  if ('newAction' in d) return openNewActionDrawer(target);
  const id = d.actionTake || d.actionConclude || d.actionReopen || d.actionEdit || d.actionHistory || d.actionDismiss;
  if (!id) return;
  const menu = target.closest('details');
  if (menu) menu.open = false;
  const a = ACTIONS_STATE.list.find((x) => String(x.id) === String(id));
  if (!a) return;
  if (d.actionTake) return quickTransition(a, 'in_progress', target, 'Você assumiu a ação. Ela está em “Em andamento”.');
  if (d.actionReopen) return quickTransition(a, 'open', target, 'A ação voltou para “A fazer”.');
  if (d.actionConclude) return openConcludeDrawer(a, target);
  if (d.actionDismiss) return openDismissDrawer(a, target);
  if (d.actionEdit) return openEditDrawer(a, target);
  if (d.actionHistory) return openHistoryDrawer(a, target);
}

// The filter lives in the URL (Voltar, favoritos) without refetching the list.
function setActionsFilter(tab, origin) {
  Object.assign(ACTIONS_STATE, {tab, origin});
  const params = new URLSearchParams();
  if (tab !== 'pendentes') params.set('situacao', tab);
  if (origin) params.set('origem', origin);
  APP.routeParams = params;
  history.replaceState(null, '', routeHash('acoes', null, params));
  if (APP.moduleRoutes) APP.moduleRoutes.remember(location.hash);
  renderActionsBody();
}

async function transitionAction(a, toStatus, note, message) {
  await api(`/api/companies/${APP.company}/actions/${a.id}/transition`, {
    method: 'POST',
    body: JSON.stringify({to_status: toStatus, note: note || ''}),
  });
  await reloadActions(message);
}

async function quickTransition(a, toStatus, trigger, message) {
  trigger.disabled = true;
  try {
    await transitionAction(a, toStatus, '', message);
  } catch (e) {
    if (trigger.isConnected) trigger.disabled = false;
    announceActions('Não foi possível atualizar a ação: ' + e.message, true);
  }
}

async function reloadActions(message) {
  ACTIONS_STATE.list = await api(`/api/companies/${APP.company}/actions`);
  updateActionsBadge(ACTIONS_STATE.list);
  renderActionsBody();
  if (message) announceActions(message, false);
}

function announceActions(message, isError) {
  const box = document.getElementById('actions-status');
  if (!box) return;
  box.textContent = message;
  box.classList.toggle('error', !!isError);
}

/* ---------------------------------------------------------------- Gavetas */

const ACTION_DISMISS_REASONS = ['Não é um problema de verdade', 'Já foi resolvido por outro caminho', 'Não vale o esforço agora', 'Outro motivo'];
const ACTION_EVENT_TEXT = {open: 'Voltou para “A fazer”', in_progress: 'Assumida', resolved: 'Concluída', dismissed: 'Descartada'};

function actionEventText(e) {
  if (e.event_type === 'created') return 'Ação criada';
  if (e.event_type === 'updated') return e.note || 'Ação atualizada';
  return ACTION_EVENT_TEXT[e.to_status] || 'Situação alterada';
}

function actionWhen(timestamp) {
  return new Date(timestamp).toLocaleString('pt-BR', {dateStyle: 'short', timeStyle: 'short'});
}

function dismissNote(reason, note) {
  return [reason, String(note || '').trim()].filter(Boolean).join('. ').slice(0, 2000);
}

function actionEditPayload(fields) {
  return {assignee: fields.assignee.value || null, due_date: fields.due_date.value || null, priority: fields.priority.value};
}

function manualActionPayload(fields, stamp) {
  const title = fields.title.value.trim();
  if (!title) throw new Error('Escreva o que precisa ser feito.');
  return {
    alert_key: `manual:${stamp.toString(36)}`, alert_version: '1', title,
    priority: fields.priority.value, assignee: fields.assignee.value || null, due_date: fields.due_date.value || null,
  };
}

function priorityOptions(selected) {
  return Object.entries(ACTION_PRIORITY_LABELS)
    .map(([key, label]) => `<option value="${key}"${key === selected ? ' selected' : ''}>${label}</option>`).join('');
}

function peopleOptions(selected) {
  return '<option value="">Sem responsável</option>' + (ACTIONS_STATE.people || [])
    .map((p) => `<option value="${esc(p.id)}"${String(p.id) === String(selected) ? ' selected' : ''}>${esc(p.name || 'Sem nome')}</option>`).join('');
}

function drawerButtons(cancelLabel, submitLabel, submitClass) {
  return `<p class="form-error" role="alert" data-form-error></p>
    <div class="drawer-actions"><button type="button" class="btn-secondary" data-drawer-close>${cancelLabel}</button>
      <button type="submit" class="${submitClass}">${submitLabel}</button></div>`;
}

// Submit runs `run(form)`; success closes the drawer, a failure stays visible inside it.
function bindDrawerForm(drawer, run) {
  const form = drawer.dialog.querySelector('form');
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const button = form.querySelector('[type="submit"]');
    const error = form.querySelector('[data-form-error]');
    button.disabled = true;
    error.textContent = '';
    try {
      await run(form);
      drawer.close();
    } catch (e) {
      button.disabled = false;
      error.textContent = e.message;
    }
  });
}

function openConcludeDrawer(a, trigger) {
  const drawer = openDrawer({title: 'Concluir ação', trigger, body: `
    <form class="drawer-form action-form">
      <p class="action-form-lead">${esc(actionDisplay(a).title)}</p>
      ${actionLiveHtml(actionLiveState(a, ACTIONS_STATE.data), actionUnit(a), false)}
      <label for="action-note">O que foi feito?</label>
      <textarea id="action-note" name="note" class="login-input" rows="4" maxlength="2000"
        placeholder="Ex.: pedido feito à distribuidora, chega na quinta"></textarea>
      <p class="field-help">Opcional. Fica no histórico para os outros sócios.</p>
      ${drawerButtons('Cancelar', 'Concluir ação', 'btn-primary')}
    </form>`});
  bindDrawerForm(drawer, (form) =>
    transitionAction(a, 'resolved', form.elements.note.value.trim(), 'Ação concluída. Ela está em “Concluídas”.'));
}

function openDismissDrawer(a, trigger) {
  const drawer = openDrawer({title: 'Descartar ação?', trigger, body: `
    <form class="drawer-form action-form">
      <p class="action-form-lead">${esc(actionDisplay(a).title)}</p>
      <p class="field-help">A ação sai das pendentes. Se o alerta voltar, dá para criar outra.</p>
      <label for="action-reason">Motivo</label>
      <select id="action-reason" name="reason" class="login-input">${ACTION_DISMISS_REASONS.map((r) => `<option>${esc(r)}</option>`).join('')}</select>
      <label for="action-dismiss-note">Anotação</label>
      <textarea id="action-dismiss-note" name="note" class="login-input" rows="3" maxlength="1800" placeholder="Opcional"></textarea>
      ${drawerButtons('Manter ação', 'Descartar', 'btn-secondary action-danger')}
    </form>`});
  bindDrawerForm(drawer, (form) =>
    transitionAction(a, 'dismissed', dismissNote(form.elements.reason.value, form.elements.note.value), 'Ação descartada.'));
}

async function openHistoryDrawer(a, trigger) {
  const lead = `<p class="action-form-lead">${esc(actionDisplay(a).title)}</p>`;
  const drawer = openDrawer({title: 'Histórico da ação', trigger, body: `${lead}<div class="skeleton-block" aria-label="Carregando histórico"></div>`});
  try {
    const events = await api(`/api/companies/${APP.company}/actions/${a.id}/events`);
    drawer.setBody(`${lead}<ol class="action-timeline">${events.map((e) => `<li>
      <p>${esc(actionEventText(e))}${e.created_by_name ? ` <span class="action-timeline-who">· ${esc(e.created_by_name)}</span>` : ''}</p>
      ${e.event_type !== 'updated' && e.note ? `<p class="action-timeline-note">“${esc(e.note)}”</p>` : ''}
      <p class="action-timeline-when">${esc(actionWhen(e.created_at))}</p></li>`).join('')}</ol>`);
  } catch (e) {
    drawer.setBody(`${lead}<p class="form-error" role="alert">Não foi possível carregar o histórico: ${esc(e.message)}</p>`);
  }
}

function openEditDrawer(a, trigger) {
  const drawer = openDrawer({title: 'Responsável e prazo', trigger, body: `
    <form class="drawer-form action-form">
      <p class="action-form-lead">${esc(actionDisplay(a).title)}</p>
      <label for="action-assignee">Responsável</label>
      <select id="action-assignee" name="assignee" class="login-input">${peopleOptions(a.assignee)}</select>
      <label for="action-due">Prazo</label>
      <input id="action-due" name="due_date" type="date" class="login-input" value="${esc(a.due_date || '')}">
      <label for="action-priority">Prioridade</label>
      <select id="action-priority" name="priority" class="login-input">${priorityOptions(a.priority)}</select>
      ${drawerButtons('Cancelar', 'Salvar', 'btn-primary')}
    </form>`});
  bindDrawerForm(drawer, async (form) => {
    await api(`/api/companies/${APP.company}/actions/${a.id}`, {method: 'PATCH', body: JSON.stringify(actionEditPayload(form.elements))});
    await reloadActions('Ação atualizada.');
  });
}

function openNewActionDrawer(trigger) {
  const drawer = openDrawer({title: 'Nova ação', trigger, body: `
    <form class="drawer-form action-form">
      <label for="new-action-title">O que precisa ser feito?</label>
      <input id="new-action-title" name="title" class="login-input" required maxlength="240" placeholder="Ex.: Negociar prazo com a distribuidora">
      <label for="new-action-priority">Prioridade</label>
      <select id="new-action-priority" name="priority" class="login-input">${priorityOptions('medium')}</select>
      <label for="new-action-assignee">Responsável</label>
      <select id="new-action-assignee" name="assignee" class="login-input">${peopleOptions(null)}</select>
      <label for="new-action-due">Prazo</label>
      <input id="new-action-due" name="due_date" type="date" class="login-input">
      ${drawerButtons('Cancelar', 'Criar ação', 'btn-primary')}
    </form>`});
  bindDrawerForm(drawer, async (form) => {
    await api(`/api/companies/${APP.company}/actions`, {method: 'POST', body: JSON.stringify(manualActionPayload(form.elements, Date.now()))});
    setActionsFilter('pendentes', '');
    await reloadActions('Ação criada.');
  });
}

/* ---------------------------------------------------------------- Resultado depois de concluir */

function actionResultHtml(r) {
  if (!r || r.kind === 'none' || r.kind === 'pending') return '';
  const margin = (m) => (m == null ? 'sem custo' : pct(m));
  if (!r.after) return `<p class="action-result-line">Resultado: começa a medir amanhã, pronto em ${shortDay(r.ready_on)}.</p>`;
  const partial = r.kind === 'measuring' ? ` (parcial: ${num(r.days)} de ${num(r.total)} dias, completo em ${shortDay(r.ready_on)})` : '';
  return `<p class="action-result-line">Resultado${partial}: margem de <b>${margin(r.before.margin)}</b> para <b>${margin(r.after.margin)}</b>;
    faturamento do produto de <b>${money(r.before.revenue)}</b> nos 30 dias antes para <b>${money(r.after.revenue)}</b> depois.</p>`;
}

async function loadActionResults() {
  for (const slot of document.querySelectorAll('[data-action-result]')) {
    const a = ACTIONS_STATE.list.find((x) => String(x.id) === slot.dataset.actionResult);
    if (!a || a.status !== 'resolved' || !/^preco:\d+$/.test(a.alert_key)) continue;
    try {
      const html = actionResultHtml(await api(`/api/companies/${APP.company}/actions/${a.id}/result`));
      if (!slot.isConnected) continue;
      slot.innerHTML = html;
      slot.hidden = !html;
    } catch (e) {
      /* the row simply stays without a result line */
    }
  }
}

/* ---------------------------------------------------------------- Menu e Resumo */

function updateActionsBadge(list) {
  const badge = document.querySelector('[data-actions-badge]');
  if (!badge) return;
  const n = pendingActionCount(list);
  badge.innerHTML = `${n > 99 ? '99+' : n}<span class="visually-hidden"> ${n === 1 ? 'ação pendente' : 'ações pendentes'}</span>`;
  badge.hidden = n === 0;
}

async function refreshActionsBadge() {
  if (APP.company == null) return;
  try {
    updateActionsBadge(await api(`/api/companies/${APP.company}/actions`));
  } catch (e) {
    /* the badge keeps its last value */
  }
}

// One line under the Resumo welcome, only when something is overdue or stuck for a week.
async function loadResumoActionsNotice() {
  const box = document.getElementById('resumo-actions');
  if (!box) return;
  let list;
  try {
    list = await api(`/api/companies/${APP.company}/actions`);
  } catch (e) {
    return;
  }
  if (!box.isConnected) return;
  updateActionsBadge(list);
  const digest = actionDigest(list, isoDay(new Date()));
  const text = actionsNoticeText(digest);
  if (!text) return;
  const params = new URLSearchParams(digest.overdue ? {situacao: 'atrasadas'} : {});
  box.innerHTML = `${icon('triangle-alert')} ${esc(text)} <a href="${routeHash('acoes', null, params)}">Ver ações →</a>`;
  box.hidden = false;
}

/* Two reminders the backend keeps by itself, asked for once per login: last month's
 * closing (created from day 5 on, resolved when the month is reviewed) and one action
 * per bill about to fall due (resolved when the bill is paid). No finance access:
 * both answer 403 and nothing happens. */
async function ensureClosingReminder() {
  if (APP.company == null) return;
  try {
    await api(`/api/companies/${APP.company}/actions/closing-reminder`, {method: 'POST'});
  } catch (e) {
    /* the badge below still counts whatever exists */
  }
  try {
    await api(`/api/companies/${APP.company}/actions/due-reminders`, {method: 'POST'});
  } catch (e) {
    /* same: the reminders are a convenience, never a blocker */
  }
  refreshActionsBadge();
}

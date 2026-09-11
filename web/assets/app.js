/* Mercado duBairro — dashboard frontend.
 * Talks only to the authenticated /api/* backend (backend/api.py). No Excel
 * import, no localStorage as source of truth, no external script/style
 * loads — the backend's CSP is script-src 'self'; style-src 'self', so
 * every interaction here uses addEventListener + CSS classes, never
 * onclick="" attributes or style="" strings. Charts are hand-rolled SVG
 * to avoid a third-party CDN entirely. */
'use strict';

const APP = {
  csrf: null,
  companies: [],
  company: null,
  periods: [],       // [{period, updated_at, version, documents}], newest first
  period: null,
  page: 'resumo',
  routeParams: new URLSearchParams(),  // ?query part of the #/página/período route (e.g. filtro=ruptura)
  pickerYear: null,   // year shown in the period popover (browsing it does not change the period)
  status: null,       // last /status payload
  dashboard: null,    // last /dashboard payload
  pollTimer: null,
  estoque: {q: '', sort: 'revenue', dir: 'desc'},  // Produtos & Estoque search/sort (filter lives in the route)
};

/* ---------------------------------------------------------------- fetch */

async function api(path, opts) {
  opts = opts || {};
  const headers = Object.assign({}, opts.headers || {});
  if (opts.body) headers['Content-Type'] = 'application/json';
  if (opts.method && opts.method !== 'GET') headers['x-csrf-token'] = APP.csrf || '';
  const res = await fetch(path, Object.assign({credentials: 'same-origin'}, opts, {headers}));
  if (res.status === 401) {
    showLogin('Sua sessão expirou. Entre novamente.');
    throw new Error('unauthenticated');
  }
  if (!res.ok) {
    let detail = 'Erro ' + res.status;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

/* ---------------------------------------------------------------- format */

const money = (cents) => cents == null ? 'Indisponível' :
  new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'}).format(cents / 100);
const pct = (v) => v == null ? '—' : v.toFixed(2).replace('.', ',') + '%';
const num = (v) => v == null ? '—' : new Intl.NumberFormat('pt-BR').format(v);
const dt = (iso) => iso ? new Date(iso).toLocaleString('pt-BR') : '—';
const MONTHS = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'})[c]);

/* ---------------------------------------------------------------- charts (self-hosted SVG, no CDN) */

function svgLineChart(points) {
  if (!points.length) return '<div class="chart-empty">Sem dados no período.</div>';
  const w = 700, h = 260, pad = 30;
  const values = points.map((p) => p.value);
  const max = Math.max(...values, 0), min = Math.min(...values, 0);
  const range = (max - min) || 1;
  const stepX = points.length > 1 ? (w - pad * 2) / (points.length - 1) : 0;
  const x = (i) => pad + i * stepX;
  const y = (v) => h - pad - ((v - min) / range) * (h - pad * 2);
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  const dots = points.map((p, i) =>
    `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="3" class="chart-dot"><title>${esc(p.label)}: ${esc(p.display)}</title></circle>`
  ).join('');
  return `<svg viewBox="0 0 ${w} ${h}" class="chart-svg" preserveAspectRatio="none">
    <line x1="${pad}" y1="${(h - pad).toFixed(1)}" x2="${w - pad}" y2="${(h - pad).toFixed(1)}" class="chart-axis"/>
    <path d="${path}" class="chart-line"/>${dots}
  </svg>`;
}

function svgBarChart(points) {
  if (!points.length) return '<div class="chart-empty">Sem dados.</div>';
  const w = 700, h = 260, pad = 30;
  const max = Math.max(...points.map((p) => p.value), 1);
  const bw = (w - pad * 2) / points.length;
  const bars = points.map((p, i) => {
    const bh = (p.value / max) * (h - pad * 2 - 14);
    const bx = pad + i * bw + bw * 0.15;
    const by = h - pad - bh;
    return `<rect x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${(bw * 0.7).toFixed(1)}" height="${bh.toFixed(1)}" class="chart-bar"><title>${esc(p.label)}: ${esc(p.display)}</title></rect>`;
  }).join('');
  const labels = points.map((p, i) =>
    `<text x="${(pad + i * bw + bw / 2).toFixed(1)}" y="${(h - pad + 14).toFixed(1)}" class="chart-label" text-anchor="middle">${esc(p.label)}</text>`
  ).join('');
  return `<svg viewBox="0 0 ${w} ${h}" class="chart-svg" preserveAspectRatio="none">
    <line x1="${pad}" y1="${(h - pad).toFixed(1)}" x2="${w - pad}" y2="${(h - pad).toFixed(1)}" class="chart-axis"/>
    ${bars}${labels}
  </svg>`;
}

/* ---------------------------------------------------------------- boot */

function boot() {
  document.getElementById('login-form').addEventListener('submit', onLoginSubmit);
  document.getElementById('custo-fixo-input').addEventListener('change', updateCustoFixo);
  document.querySelectorAll('img[data-fallback]').forEach((img) => {
    img.addEventListener('error', () => img.classList.add('hidden'));
  });
  document.addEventListener('click', onDelegatedClick);
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    if (isPickerOpen()) closePeriodPicker(true); else closeNav(true);
  });
  window.addEventListener('hashchange', onRouteChange);
  setupChartTooltip();
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    // Insight charts are drawn at their pixel width; redraw from the cached payload.
    resizeTimer = setTimeout(() => { if (INSIGHT_PAGES.includes(APP.page) && APP.dashboard) renderPage(); }, 250);
  });
  refreshSession();
}

const INSIGHT_PAGES = ['precos', 'mapa', 'diagnostico', 'sazonalidade', 'visao'];

function setupChartTooltip() {
  const tip = document.createElement('div');
  tip.className = 'chart-tip hidden';
  document.body.appendChild(tip);
  document.addEventListener('mouseover', (ev) => {
    const el = ev.target.closest && ev.target.closest('[data-tip]');
    if (!el || !el.dataset.tip) return tip.classList.add('hidden');
    tip.textContent = el.dataset.tip;
    tip.classList.remove('hidden');
  });
  document.addEventListener('mousemove', (ev) => {
    if (tip.classList.contains('hidden')) return;
    const x = ev.clientX + 14, y = ev.clientY + 14;
    tip.style.left = Math.max(8, Math.min(x, window.innerWidth - tip.offsetWidth - 8)) + 'px';
    tip.style.top = (y + tip.offsetHeight > window.innerHeight - 8 ? ev.clientY - tip.offsetHeight - 10 : y) + 'px';
  });
}

function onDelegatedClick(ev) {
  // Outside click closes the period popover; checked before any handler re-renders the target.
  if (isPickerOpen() && !ev.target.closest('.period-control')) closePeriodPicker();
  if (ev.target.closest('[data-nav-toggle]')) {
    return document.body.classList.contains('nav-open') ? closeNav(true) : openNav();
  }
  if (ev.target.closest('[data-nav-close]')) return closeNav(true);
  if (ev.target.closest('.nav-item')) return closeNav();  // the link's href drives the route (hashchange)
  const nav = ev.target.closest('[data-nav]');
  if (nav) return navigate(nav.dataset.nav);
  if (ev.target.closest('[data-period-toggle]')) return isPickerOpen() ? closePeriodPicker(true) : openPeriodPicker();
  const step = ev.target.closest('[data-period-step]');
  if (step) return stepPeriod(parseInt(step.dataset.periodStep, 10));
  const filter = ev.target.closest('[data-estoque-filter]');
  if (filter) return setEstoqueFilter(filter.dataset.estoqueFilter);
  const sort = ev.target.closest('[data-sort]');
  if (sort) return sortEstoque(sort.dataset.sort);
  const explainBtn = ev.target.closest('[data-explain]');
  if (explainBtn) return explainBtn.nextElementSibling.classList.toggle('open');
  const tab = ev.target.closest('[data-tab]');
  if (tab) {
    const bar = tab.closest('.tabs');
    bar.querySelectorAll('[data-tab]').forEach((b) => b.classList.toggle('active', b === tab));
    for (let s = bar.nextElementSibling; s && s.classList.contains('tab-content'); s = s.nextElementSibling) {
      s.classList.toggle('active', s.id === tab.dataset.tab);
    }
    return;
  }
  const legend = ev.target.closest('[data-legend]');
  if (legend) {
    const off = legend.classList.toggle('off');
    document.querySelectorAll(`[data-series="${legend.dataset.legend}-${legend.dataset.s}"]`)
      .forEach((el) => el.classList.toggle('series-off', off));
    return;
  }
  const year = ev.target.closest('[data-year]');
  if (year) return selectYear(year.dataset.year);
  const period = ev.target.closest('[data-period]');
  if (period) return selectPeriod(period.dataset.period);
  const sync = ev.target.closest('[data-sync]');
  if (sync) return triggerSync(sync.dataset.sync);
  if (ev.target.closest('[data-logout]')) return logout();
}

async function refreshSession() {
  try {
    const session = await api('/api/session');
    onAuthenticated(session);
  } catch (e) {
    showLogin();
  }
}

function showLogin(message) {
  document.getElementById('loading').classList.add('hidden');
  document.getElementById('app').classList.add('hidden');
  document.getElementById('login-screen').classList.remove('hidden');
  document.getElementById('login-error').textContent = message || '';
  document.getElementById('login-password').focus();
}

async function onLoginSubmit(ev) {
  ev.preventDefault();
  const pass = document.getElementById('login-password').value;
  const btn = document.getElementById('login-submit');
  btn.disabled = true;
  document.getElementById('login-error').textContent = '';
  try {
    const res = await fetch('/api/login', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password: pass}),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Não foi possível entrar.');
    }
    const session = await api('/api/session');
    document.getElementById('login-screen').classList.add('hidden');
    onAuthenticated(session);
  } catch (e) {
    document.getElementById('login-error').textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function logout() {
  try { await api('/api/logout', {method: 'POST'}); } catch (e) {}
  location.reload();
}

async function onAuthenticated(session) {
  APP.csrf = session.csrf;
  APP.companies = session.companies || [];
  document.getElementById('sidebar-user').textContent = session.user || '';
  document.getElementById('loading').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  if (!APP.companies.length) {
    renderEmptyState('Nenhuma empresa liberada para esta credencial Mobne.');
    return;
  }
  APP.company = APP.companies[0].id;
  await refreshStatus();
  onRouteChange();  // applies #/página/período from the URL (reload, favoritos), else the defaults
  // The automatic worker (backend/sync.py Worker) can finish a sync with nobody watching
  // the "sync" page; without this, new months only show up after a manual reload.
  setInterval(async () => {
    await refreshStatus();
    if (APP.page === 'sync') return renderSyncPage();
    // Redraw only when the data changed — a blind redraw would wipe the Estoque search mid-typing.
    const info = APP.periods.find((p) => p.period === APP.period);
    if (!APP.dashboard || (info && info.version !== APP.dashboard.version)) renderPage();
  }, 60000);
}

/* ---------------------------------------------------------------- status/period */

async function refreshStatus() {
  APP.status = await api(`/api/companies/${APP.company}/status`);
  APP.periods = APP.status.periods || [];
  buildPeriodSelector();
  const hasActiveJob = (APP.status.jobs || []).some((j) => j.state === 'queued' || j.state === 'running');
  if (hasActiveJob && !APP.pollTimer) {
    APP.pollTimer = setInterval(async () => {
      await refreshStatus();
      if (APP.page === 'sync') renderSyncPage();
    }, 4000);
  } else if (!hasActiveJob && APP.pollTimer) {
    clearInterval(APP.pollTimer);
    APP.pollTimer = null;
    APP.dashboard = null;  // a finished sync may have refreshed catalogs/sales
  }
}

const periodLabel = (period) => `${MONTHS[parseInt(period.slice(5), 10) - 1]}/${period.slice(0, 4)}`;

// Months Mobne actually has sales for, oldest first — what ‹ › step through.
const dataPeriods = () => APP.periods.filter((p) => p.documents > 0).map((p) => p.period).sort();
function neighborPeriod(delta) {
  const seq = dataPeriods();
  return delta < 0 ? seq.filter((p) => p < APP.period).pop() : seq.find((p) => p > APP.period);
}

/* Top-bar period control: "‹ Set/2026 ▾ ›" plus a popover with the year/month grid. */
function buildPeriodSelector() {
  const current = document.getElementById('period-current');
  const status = document.getElementById('period-status');
  const steps = document.querySelectorAll('[data-period-step]');
  if (!APP.periods.length) {
    current.textContent = 'Sem períodos';
    current.disabled = true;
    steps.forEach((b) => { b.disabled = true; });
    status.textContent = 'Nenhum período sincronizado ainda.';
    return;
  }
  if (!APP.period || !APP.periods.some((p) => p.period === APP.period)) {
    // Prefer the newest period that actually has Mobne sales over one Mobne reports empty
    // (e.g. before the store was onboarded) so the dashboard never opens on a blank month.
    const withData = APP.periods.find((p) => p.documents > 0);
    APP.period = (withData || APP.periods[0]).period;
  }
  current.disabled = false;
  current.innerHTML = `<span aria-hidden="true">📅</span> ${esc(periodLabel(APP.period))} <span class="caret" aria-hidden="true">▾</span>`;
  current.setAttribute('aria-label', `Período ${periodLabel(APP.period)} — trocar período`);
  steps.forEach((b) => {
    const target = neighborPeriod(parseInt(b.dataset.periodStep, 10));
    b.disabled = !target;
    b.title = target ? periodLabel(target) : '';
  });
  const info = APP.periods.find((p) => p.period === APP.period);
  status.textContent = !info ? '' : info.documents
    ? `Dados atualizados em ${dt(info.updated_at)}` : 'Mobne não tem vendas registradas neste período';
  // The grid is (re)drawn when the popover opens; redrawing it here would steal keyboard focus.
}

function renderPickerGrid() {
  const years = [...new Set(APP.periods.map((p) => p.period.slice(0, 4)))].sort().reverse();
  const year = APP.pickerYear || APP.period.slice(0, 4);
  document.getElementById('year-tabs').innerHTML = years.map((y) =>
    `<button type="button" class="tab-btn-period ${y === year ? 'active' : ''}" aria-pressed="${y === year}" data-year="${y}">${y}</button>`
  ).join('');
  const known = new Map(APP.periods.map((p) => [p.period, p]));
  document.getElementById('month-buttons').innerHTML = MONTHS.map((label, i) => {
    const period = `${year}-${String(i + 1).padStart(2, '0')}`;
    const info = known.get(period);
    const empty = info && !info.documents;  // synced, but Mobne has no sales for this period
    const active = period === APP.period;
    const disabled = info ? '' : 'disabled';  // empty stays clickable, so its explanation is reachable
    const cls = ['month-btn', active ? 'active' : '', empty ? 'month-btn-empty' : ''].filter(Boolean).join(' ');
    const title = empty ? ' title="Mobne não tem vendas registradas neste período"' : '';
    return `<button type="button" class="${cls}" aria-pressed="${active}" ${disabled}${title} data-period="${period}">${label}</button>`;
  }).join('');
}

const isPickerOpen = () => { const p = document.getElementById('period-popover'); return !!p && !p.hidden; };

function openPeriodPicker() {
  if (!APP.period) return;
  APP.pickerYear = APP.period.slice(0, 4);
  renderPickerGrid();
  document.getElementById('period-popover').hidden = false;
  document.getElementById('period-current').setAttribute('aria-expanded', 'true');
  const target = document.querySelector('#month-buttons .month-btn.active') ||
    document.querySelector('#month-buttons .month-btn:not(:disabled)');
  if (target) target.focus();
}

function closePeriodPicker(returnFocus) {
  if (!isPickerOpen()) return;
  document.getElementById('period-popover').hidden = true;
  const toggle = document.getElementById('period-current');
  toggle.setAttribute('aria-expanded', 'false');
  if (returnFocus) toggle.focus();
}

// Browsing another year in the popover only redraws the grid; the period changes on a month click.
function selectYear(year) {
  APP.pickerYear = year;
  renderPickerGrid();
  const tab = document.querySelector(`#year-tabs [data-year="${year}"]`);
  if (tab) tab.focus();
}

function selectPeriod(period) {
  closePeriodPicker(true);
  go(APP.page, period, APP.page === 'estoque' ? APP.routeParams : null);  // keep Estoque's filter
}

function stepPeriod(delta) {
  const target = neighborPeriod(delta);
  if (target) go(APP.page, target, APP.page === 'estoque' ? APP.routeParams : null);
}

/* ---------------------------------------------------------------- nav: drawer + #/página/período routes */

function openNav() {
  document.body.classList.add('nav-open');
  document.querySelector('.nav-backdrop').hidden = false;
  document.querySelector('[data-nav-toggle]').setAttribute('aria-expanded', 'true');
  const first = document.querySelector('.nav-item.active') || document.querySelector('.nav-item');
  if (first) first.focus();
}

function closeNav(returnFocus) {
  if (!document.body.classList.contains('nav-open')) return;
  document.body.classList.remove('nav-open');
  document.querySelector('.nav-backdrop').hidden = true;
  const toggle = document.querySelector('[data-nav-toggle]');
  toggle.setAttribute('aria-expanded', 'false');
  if (returnFocus) toggle.focus();
}

const PAGES = ['resumo', 'precos', 'mapa', 'diagnostico', 'sazonalidade', 'visao', 'estoque', 'sync'];

function parseRoute() {
  const [path, query] = location.hash.replace(/^#\/?/, '').split('?');
  const [page, period] = (path || '').split('/');
  return {page: PAGES.includes(page) ? page : null,
    period: /^\d{4}-\d{2}$/.test(period || '') ? period : null,
    params: new URLSearchParams(query || '')};
}

function routeHash(page, period, params) {
  const q = params ? params.toString() : '';
  return `#/${page}${period ? '/' + period : ''}${q ? '?' + q : ''}`;
}

// Every page/period change goes through the URL, so Voltar, reload and favoritos all work.
function go(page, period, params) {
  const hash = routeHash(page, period || APP.period, params);
  if (location.hash === hash) onRouteChange(); else location.hash = hash;  // → hashchange → onRouteChange
}

function navigate(page) { go(page); }

function onRouteChange() {
  if (!APP.company) return;  // before login the hash is kept and applied by onAuthenticated
  const r = parseRoute();
  const page = r.page || 'resumo';
  const period = r.period && APP.periods.some((p) => p.period === r.period) ? r.period : APP.period;
  const hash = routeHash(page, period, r.params);
  if (location.hash !== hash) history.replaceState(null, '', hash);  // normalize, no extra history entry
  const pageChanged = page !== APP.page;
  APP.page = page;
  APP.period = period;
  APP.routeParams = r.params;
  document.querySelectorAll('.nav-item').forEach((a) => {
    const on = a.dataset.page === page;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    a.setAttribute('href', routeHash(a.dataset.page, period));  // open-in-new-tab keeps the period
  });
  closeNav();
  closePeriodPicker();
  buildPeriodSelector();
  renderPage();
  if (pageChanged) window.scrollTo(0, 0);
}

async function renderPage() {
  if (APP.page === 'sync') return renderSyncPage();
  if (!APP.period) {
    return renderEmptyState('Nenhum período sincronizado ainda. Vá em "Sincronização Mobne" e clique em Sincronizar agora.');
  }
  // Every data page reads the same /dashboard payload; reuse it until the period or its version changes.
  const info = APP.periods.find((p) => p.period === APP.period);
  const cached = APP.dashboard && APP.dashboard.period === APP.period && APP.dashboardCompany === APP.company &&
    (!info || info.version === APP.dashboard.version);
  if (!cached) {
    document.getElementById('content').innerHTML = '<div class="loading loading-inline"><div class="loading-spinner"></div>Carregando…</div>';
    try {
      APP.dashboard = await api(`/api/companies/${APP.company}/dashboard?period=${APP.period}`);
      APP.dashboardCompany = APP.company;
    } catch (e) {
      APP.dashboard = null;
      if (e.status === 404) return renderEmptyState(e.message);
      return renderEmptyState('Não foi possível carregar os dados: ' + e.message);
    }
    document.getElementById('custo-fixo-input').value = (APP.dashboard.fixed_cost_cents / 100).toFixed(2);
  }
  const renderers = {resumo: renderResumo, estoque: renderEstoque, precos: renderPrecos, mapa: renderMapa,
    diagnostico: renderDiagnostico, sazonalidade: renderSazonalidade, visao: renderVisao};
  if (APP.page === 'sync') return renderSyncPage();  // user navigated away mid-fetch
  (renderers[APP.page] || renderResumo)(APP.dashboard);
}

function renderEmptyState(message) {
  document.getElementById('content').innerHTML = `
    <div class="page-title">Mercado duBairro</div>
    <div class="story-box mt-16">${esc(message)}</div>`;
}

function headerBlock(data) {
  const [y, m] = data.period.split('-');
  const label = `${MONTHS[parseInt(m, 10) - 1]}/${y}`;
  const partial = data.partial_month ? ` · em andamento (dados até ${data.as_of})` : '';
  return `
    <div class="page-title">📊 Resumo Executivo</div>
    <div class="page-subtitle">Vendas PDV reconciliadas diretamente do Mobne — sem Excel, sem localStorage.</div>
    <span class="periodo-badge">${label}${partial}</span>
    <span class="periodo-badge muted">Atualizado ${dt(data.updated_at)} · v${data.version}</span>
    ${reconciliationBanner(data.reconciliation)}
    ${dataQualityBanner(data.totals)}
  `;
}

function reconciliationBanner(r) {
  if (r.exact_match) {
    return `<div class="disclosure-banner">✅ Reconciliado exatamente com a Análise Mobne — ${money(r.receipt_revenue)} conferido, sem divergência.</div>`;
  }
  return `<div class="disclosure-banner warn">⚠️ Diferença de ${money(r.difference)} entre Cupom e Análise Mobne
    (${r.missing_documents} documento${r.missing_documents === 1 ? '' : 's'}: ${r.missing_document_ids.join(', ')}).
    Publicado por estar dentro da tolerância declarada de ${money(r.tolerance_cents)}. Cupom é a receita oficial.</div>`;
}

// Mobne keeps an explicit item cost of R$0 as a real known cost (see backend/models.py
// receipt()) — but a high share of zero-cost items usually means the store had no
// purchase-cost history yet (new product/onboarding), not that items were free.
// Jul-Aug/2025 was 100% zero-cost; steady state since Nov/2025 is ~7-9%.
const ZERO_COST_WARN_THRESHOLD = 0.15;
function dataQualityBanner(t) {
  if (!t.zero_cost_ratio || t.zero_cost_ratio <= ZERO_COST_WARN_THRESHOLD) return '';
  return `<div class="disclosure-banner warn">⚠️ ${pct(t.zero_cost_ratio * 100)} dos itens vendidos neste período
    estão com custo zero no Mobne (sem histórico de compra) — lucro e margem deste mês provavelmente estão
    superestimados e não devem ser comparados com meses de custo completo.</div>`;
}

/* ---------------------------------------------------------------- Resumo Executivo helpers */

// value is a % change (e.g. mom.revenue_change); positiveIsGood=false flips the color (cancel rate, etc.)
function deltaChip(label, value, positiveIsGood) {
  if (positiveIsGood == null) positiveIsGood = true;
  if (value == null) return `<span class="delta-chip">${label}: —</span>`;
  const good = positiveIsGood ? value >= 0 : value <= 0;
  const cls = Math.abs(value) < 0.05 ? '' : (good ? 'delta-positive' : 'delta-negative');
  return `<span class="delta-chip ${cls}">${label}: ${value > 0 ? '+' : ''}${pct(value)}</span>`;
}

function kpiCard(title, value, valueCls, deltasHtml, subtitle) {
  return `<div class="kpi-card">
    <div class="kpi-title">${title}</div>
    <div class="kpi-value ${valueCls || ''}">${value}</div>
    ${subtitle ? `<div class="kpi-subtitle">${subtitle}</div>` : ''}
    ${deltasHtml ? `<div class="kpi-deltas">${deltasHtml}</div>` : ''}
  </div>`;
}

// Each backend alert type opens Produtos & Estoque filtered to exactly the products it counts
// (ESTOQUE_FILTERS below uses the same predicates as backend/api.py dashboard() alerts).
const ALERT_FILTERS = {estoque: 'ruptura', preco: 'abaixo-custo', custo: 'sem-custo'};

function alertsBlock(alerts) {
  if (!alerts || !alerts.length) return '';
  return `<div class="alert-list">${alerts.map((a) => {
    const f = ALERT_FILTERS[a.type];
    const link = f ? `<a class="alert-action" href="${routeHash('estoque', APP.period, new URLSearchParams({filtro: f}))}">Ver produtos →</a>` : '';
    return `<div class="alert-card severity-${esc(a.severity)}"><span>⚠️ ${esc(a.message)}</span>${link}</div>`;
  }).join('')}</div>`;
}

// "The month in one sentence" — revenue vs. last month and vs. the same month last year
// (each silently omitted when there is no reliable base to compare against, see backend/api.py
// comparison_for()), plus margin and how far revenue landed from the break-even point.
function resumoNarrative(data) {
  const t = data.totals, cmp = data.comparison, mom = data.comparison_mom;
  const [y, m] = data.period.split('-');
  const bits = [`${MONTHS[parseInt(m, 10) - 1]}/${y} faturou ${money(t.revenue)}`];
  if (mom && mom.revenue_change != null) bits.push(`${mom.revenue_change >= 0 ? '+' : ''}${pct(mom.revenue_change)} sobre o mês anterior`);
  if (cmp && cmp.revenue_change != null) bits.push(`${cmp.revenue_change >= 0 ? '+' : ''}${pct(cmp.revenue_change)} sobre ${esc(cmp.period.slice(0, 4))}`);
  let text = bits.join(', ') + '.';
  if (t.margin == null) {
    return text + ' Margem indisponível: há itens vendidos sem custo conhecido no período.';
  }
  text += ` Margem de ${pct(t.margin)}`;
  if (data.break_even_cents == null) return text + '.';
  return text + (data.break_even_gap_pct >= 0
    ? `, e o ponto de equilíbrio (${money(data.break_even_cents)}) foi superado com folga de ${pct(data.break_even_gap_pct)}.`
    : `, e a receita ficou ${pct(Math.abs(data.break_even_gap_pct))} abaixo do ponto de equilíbrio (${money(data.break_even_cents)}).`);
}

function renderResumo(data) {
  const t = data.totals, cmp = data.comparison, mom = data.comparison_mom;
  const deltas = (change) => [deltaChip('M/M', mom && mom[change]), deltaChip('A/A', cmp && cmp[change])].join('');
  const cancelBase = t.receipts + t.cancelled;
  const cancelRate = cancelBase ? (t.cancelled / cancelBase) * 100 : null;

  const dailyPoints = (data.daily || []).map((d) => ({label: d.date, value: d.revenue / 100, display: money(d.revenue)}));
  const timelinePoints = (data.timeline || []).map((tl) => ({label: tl.period, value: tl.revenue / 100, display: money(tl.revenue)}));
  const topProfit = (data.products || []).filter((p) => p.profit != null).slice().sort((a, b) => b.profit - a.profit).slice(0, 10);
  const topProfitSum = topProfit.reduce((s, p) => s + p.profit, 0);
  const topPoints = topProfit.slice().reverse()
    .map((p) => ({label: p.name.length > 16 ? p.name.slice(0, 16) + '…' : p.name, value: p.profit / 100, display: money(p.profit)}));

  document.getElementById('content').innerHTML = `
    ${headerBlock(data)}
    ${alertsBlock(data.alerts)}
    <div class="story-box">💡 ${esc(resumoNarrative(data))}</div>

    <div class="kpi-grid kpi-grid-4">
      ${kpiCard('Faturamento', money(t.revenue), '', deltas('revenue_change'), `${num(t.receipts)} documentos · ${num(t.cancelled)} cancelados`)}
      ${kpiCard('Lucro Bruto', money(t.profit), t.profit == null ? 'kpi-unavailable' : (t.profit >= 0 ? 'kpi-positive' : 'kpi-negative'),
        deltas('profit_change'), t.unknown > 0 ? num(t.unknown) + ' itens sem custo — não estimados' : 'Todos os itens com custo')}
      ${kpiCard('Margem', pct(t.margin), t.margin == null ? 'kpi-unavailable' : '', '', `Ticket médio: ${money(t.ticket)}`)}
      ${kpiCard('Ticket médio', money(t.ticket), '', deltas('ticket_change'), '')}
      ${kpiCard('Nº de cupons', num(t.receipts), '', deltas('receipts_change'), '')}
      ${kpiCard('Taxa de cancelamento', pct(cancelRate), '', '', `${num(t.cancelled)} de ${num(cancelBase)} documentos`)}
      ${kpiCard('Resultado simulado', money(data.simulated_net), data.simulated_net == null ? 'kpi-unavailable' : (data.simulated_net >= 0 ? 'kpi-positive' : 'kpi-negative'),
        '', `Custo fixo cadastrado: ${money(data.fixed_cost_cents)}`)}
      ${kpiCard('Ponto de equilíbrio', money(data.break_even_cents), data.break_even_cents == null ? 'kpi-unavailable' : '',
        '', data.break_even_gap_pct == null ? '' : `Folga: ${data.break_even_gap_pct >= 0 ? '+' : ''}${pct(data.break_even_gap_pct)}`)}
    </div>
    <div class="story-box">
      Resultado simulado = receita − custo dos itens conhecidos − custo fixo cadastrado. Não é o lucro líquido contábil
      (despesas reais não confirmadas com o Mobne). Ponto de equilíbrio = custo fixo ÷ margem do período; ambos ficam
      indisponíveis quando há itens sem custo conhecido, em vez de usar uma margem parcial como se fosse a real.
    </div>

    <div class="row">
      <div class="col-60">
        <div class="section-header">Receita diária</div>
        <div class="chart-container chart-box">${svgLineChart(dailyPoints)}</div>
      </div>
      <div class="col-40">
        <div class="section-header">Categorias</div>
        <div class="data-table-container table-scroll-sm">
          <table class="data-table"><thead><tr><th>Categoria</th><th>Receita</th><th>Margem</th></tr></thead>
          <tbody>${(data.categories || []).map((c) => `<tr><td>${esc(c.name)}</td><td>${money(c.revenue)}</td><td>${pct(c.margin)}</td></tr>`).join('')}</tbody></table>
        </div>
      </div>
    </div>

    <div class="section-header">Top 10 produtos por lucro</div>
    <div class="chart-container chart-box">${svgBarChart(topPoints)}</div>
    ${topProfit.length ? `<div class="story-box">💡 Os 10 produtos mais lucrativos representam ${t.profit ? pct(topProfitSum / t.profit * 100) : '—'} do lucro do mês.
      ${esc(topProfit[0].name)} lidera com ${money(topProfit[0].profit)}.
      <button type="button" class="btn-link" data-nav="mapa">Ver curva ABC completa em Mapa de Produtos →</button></div>` : ''}

    <div class="section-header">Histórico mensal</div>
    <div class="chart-container chart-box">${svgBarChart(timelinePoints)}</div>
  `;
}

/* ---------------------------------------------------------------- Produtos & Estoque */

// 'ruptura', 'abaixo-custo' and 'sem-custo' must stay identical to the predicates behind the
// Resumo alerts (backend/api.py dashboard()), so "Ver produtos" lists exactly what was counted.
const ESTOQUE_FILTERS = [
  {key: '', label: 'Todos', test: () => true},
  {key: 'curva-a', label: 'Curva A', test: (p) => p.abc === 'A'},
  {key: 'ruptura', label: 'Curva A sem estoque', test: (p) => p.abc === 'A' && (p.stock == null || p.stock <= 0)},
  {key: 'estoque-zerado', label: 'Estoque ≤ 0', test: (p) => p.stock != null && p.stock <= 0},
  {key: 'abaixo-custo', label: 'Preço abaixo do custo',
    test: (p) => p.current_price != null && p.current_cost != null && p.current_price < p.current_cost},
  {key: 'sem-custo', label: 'Vendido sem custo', test: (p) => p.unknown > 0},
];

const ESTOQUE_COLS = [
  {key: 'name', label: 'Produto'}, {key: 'category', label: 'Categoria'},
  {key: 'stock', label: 'Estoque atual', num: true}, {key: 'current_price', label: 'Preço atual', num: true},
  {key: 'current_cost', label: 'Custo atual', num: true}, {key: 'revenue', label: 'Receita no período', num: true},
  {key: 'margin', label: 'Margem', num: true}, {key: 'abc', label: 'ABC'},
];

const fold = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const estoqueFilter = () => ESTOQUE_FILTERS.find((f) => f.key === (APP.routeParams.get('filtro') || '')) || ESTOQUE_FILTERS[0];

function renderEstoque(data) {
  const inv = data.inventory || [];
  const active = estoqueFilter().key;
  document.getElementById('content').innerHTML = `
    <div class="page-title">📦 Produtos &amp; Estoque</div>
    <div class="page-subtitle">Estoque e preço são o retrato ATUAL do Mobne — não representam o histórico do período selecionado.</div>
    <span class="periodo-badge muted">Estoque: ${dt(data.stock_updated_at)}</span>
    <span class="periodo-badge muted">Preços: ${dt(data.prices_updated_at)}</span>
    <div class="estoque-toolbar">
      <label class="field-label" for="estoque-search">Buscar produto ou categoria</label>
      <input type="search" id="estoque-search" class="search-input" placeholder="Ex.: banana, cerveja…" autocomplete="off" value="${esc(APP.estoque.q)}">
      <div class="filter-chips" role="group" aria-label="Filtros rápidos">
        ${ESTOQUE_FILTERS.map((f) => `<button type="button" class="chip ${f.key === active ? 'active' : ''}" aria-pressed="${f.key === active}" data-estoque-filter="${f.key}">
          ${esc(f.label)}<span class="chip-count">${num(inv.filter(f.test).length)}</span></button>`).join('')}
      </div>
    </div>
    <div id="estoque-count" class="result-count" role="status" aria-live="polite"></div>
    <div class="data-table-container table-scroll-tall"><table class="data-table" id="estoque-table"></table></div>
  `;
  const input = document.getElementById('estoque-search');
  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { APP.estoque.q = input.value; renderEstoqueTable(); }, 150);
  });
  renderEstoqueTable();
}

// Redraws only the table + count, so the search box keeps focus while typing.
function renderEstoqueTable() {
  const data = APP.dashboard;
  const table = document.getElementById('estoque-table');
  if (!data || !table) return;
  const inv = data.inventory || [];
  const filter = estoqueFilter();
  const q = fold(APP.estoque.q).trim();
  const {sort, dir} = APP.estoque;
  const col = ESTOQUE_COLS.find((c) => c.key === sort) || ESTOQUE_COLS[5];
  const rows = inv.filter(filter.test)
    .filter((p) => !q || fold(p.name).includes(q) || fold(p.category).includes(q))
    .sort((a, b) => {
      const va = a[col.key], vb = b[col.key];
      if (va == null || vb == null) return va == null ? (vb == null ? 0 : 1) : -1;  // blanks last, either direction
      const c = col.num ? va - vb : String(va).localeCompare(String(vb), 'pt-BR');
      return dir === 'asc' ? c : -c;
    });

  const head = ESTOQUE_COLS.map((c) => {
    const sorted = c.key === col.key ? (dir === 'asc' ? 'ascending' : 'descending') : 'none';
    return `<th class="${c.num ? 'num' : ''}" aria-sort="${sorted}"><button type="button" class="th-sort" data-sort="${c.key}">${esc(c.label)}</button></th>`;
  }).join('');
  const body = rows.map((p) => {
    const noStock = p.stock != null && p.stock <= 0;
    const underCost = p.current_price != null && p.current_cost != null && p.current_price < p.current_cost;
    return `<tr><td>${esc(p.name)}</td><td>${esc(p.category)}</td>
      <td class="num ${noStock ? 'cell-alert' : ''}">${p.stock == null ? '—' : num(p.stock)}</td>
      <td class="num ${underCost ? 'cell-alert' : ''}">${money(p.current_price)}</td>
      <td class="num">${money(p.current_cost)}</td><td class="num">${money(p.revenue)}</td>
      <td class="num">${pct(p.margin)}</td><td>${esc(p.abc)}</td></tr>`;
  }).join('') || `<tr><td colspan="${ESTOQUE_COLS.length}">Nenhum produto encontrado com esse filtro e busca.</td></tr>`;
  table.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
  document.getElementById('estoque-count').textContent =
    `Mostrando ${num(rows.length)} de ${num(inv.length)} produtos${filter.key ? ` · filtro: ${filter.label}` : ''}${q ? ` · busca: “${APP.estoque.q.trim()}”` : ''}`;
}

// Filter chips update the route in place (replaceState, no hashchange) so focus stays on the chip.
function setEstoqueFilter(key) {
  const params = new URLSearchParams(APP.routeParams);
  if (key) params.set('filtro', key); else params.delete('filtro');
  APP.routeParams = params;
  history.replaceState(null, '', routeHash('estoque', APP.period, params));
  document.querySelectorAll('[data-estoque-filter]').forEach((b) => {
    const on = b.dataset.estoqueFilter === key;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
  });
  renderEstoqueTable();
}

function sortEstoque(key) {
  const col = ESTOQUE_COLS.find((c) => c.key === key);
  if (!col) return;
  if (APP.estoque.sort === key) APP.estoque.dir = APP.estoque.dir === 'asc' ? 'desc' : 'asc';
  else Object.assign(APP.estoque, {sort: key, dir: col.num ? 'desc' : 'asc'});
  renderEstoqueTable();
  const btn = document.querySelector(`#estoque-table [data-sort="${key}"]`);
  if (btn) btn.focus();  // the header was redrawn; keep keyboard users where they were
}

/* ---------------------------------------------------------------- sync page */

function jobBadge(state) {
  const map = {queued: 'badge-info', running: 'badge-info', completed: 'badge-success',
    completed_with_errors: 'badge-warning', failed: 'badge-error'};
  const label = {queued: 'Na fila', running: 'Em execução', completed: 'Concluído',
    completed_with_errors: 'Concluído com falhas', failed: 'Falhou'};
  return `<span class="${map[state] || 'badge-muted'}">${label[state] || state}</span>`;
}

function renderSyncPage() {
  const s = APP.status;
  const catalogs = s.catalogs || {};
  const catalogRows = ['categories', 'products', 'stock', 'prices'].map((key) => {
    const c = catalogs[key];
    const labels = {categories: 'Categorias', products: 'Produtos', stock: 'Estoque', prices: 'Preços'};
    return `<tr><td>${labels[key]}</td><td>${c ? num(c.count) : '—'}</td><td>${c ? dt(c.updated_at) : 'Não sincronizado'}</td></tr>`;
  }).join('');

  document.getElementById('content').innerHTML = `
    <div class="page-title">🔗 Sincronização Mobne</div>
    <div class="page-subtitle">${esc(s.source)} · intervalo automático: ${s.interval_minutes} min</div>

    <div class="btn-row">
      <button class="btn-primary btn-wide" data-sync="recent">Sincronizar agora (recente)</button>
      <button class="btn-secondary" data-sync="reconcile">Reconciliar todo o histórico</button>
      <button class="btn-secondary" data-sync="history">Carregar histórico completo</button>
    </div>

    <div class="section-header">Catálogos</div>
    <div class="data-table-container">
      <table class="data-table"><thead><tr><th>Base</th><th>Itens</th><th>Atualizado</th></tr></thead>
      <tbody>${catalogRows}</tbody></table>
    </div>

    <div class="section-header">Períodos de vendas sincronizados</div>
    <div class="data-table-container table-scroll-md">
      <table class="data-table"><thead><tr><th>Período</th><th>Documentos</th><th>Atualizado</th><th>Versão</th></tr></thead>
      <tbody>${(s.periods || []).map((p) => `<tr><td>${p.period}</td>
        <td>${p.documents ? num(p.documents) : 'Sem vendas no Mobne'}</td>
        <td>${dt(p.updated_at)}</td><td>${p.version}</td></tr>`).join('') || '<tr><td colspan="4">Nenhum período ainda.</td></tr>'}</tbody></table>
    </div>

    <div class="section-header">Execuções recentes</div>
    <div class="jobs-list">
      ${(s.jobs || []).map((j) => `<div class="job-row">
          <div><strong>${j.mode}</strong> ${jobBadge(j.state)}
            <div class="job-detail">${esc(j.detail || '')}</div>
            ${j.error ? `<div class="job-error">${esc(j.error)}</div>` : ''}
          </div>
          <div class="job-detail">${dt(j.updated_at)}${j.total ? ` · ${j.completed}/${j.total}` : ''}</div>
        </div>`).join('') || '<div class="story-box">Nenhuma execução ainda.</div>'}
    </div>
  `;
}

async function triggerSync(mode) {
  try {
    await api(`/api/companies/${APP.company}/sync`, {method: 'POST', body: JSON.stringify({mode})});
    await refreshStatus();
    renderSyncPage();
  } catch (e) {
    alert('Não foi possível iniciar a sincronização: ' + e.message);
  }
}

/* ---------------------------------------------------------------- config */

let custoFixoTimer = null, custoStatusTimer = null;

function setCustoStatus(text, kind) {
  const el = document.getElementById('custo-fixo-status');
  clearTimeout(custoStatusTimer);
  el.textContent = text;
  el.className = 'sim-status' + (kind ? ' ' + kind : '');
  if (kind === 'ok') custoStatusTimer = setTimeout(() => { el.textContent = ''; el.className = 'sim-status'; }, 5000);
}

function updateCustoFixo() {
  clearTimeout(custoFixoTimer);
  custoFixoTimer = setTimeout(async () => {
    const value = parseFloat(document.getElementById('custo-fixo-input').value);
    if (isNaN(value) || value < 0) return setCustoStatus('Informe um valor em reais, maior ou igual a zero.', 'error');
    setCustoStatus('Salvando…');
    try {
      await api(`/api/companies/${APP.company}/config`, {
        method: 'PUT', body: JSON.stringify({fixed_cost_cents: Math.round(value * 100)}),
      });
      APP.dashboard = null;
      if (APP.page !== 'sync') await renderPage();
      setCustoStatus(`Salvo ✓ ${money(Math.round(value * 100))} — resultado e ponto de equilíbrio recalculados.`, 'ok');
    } catch (e) {
      setCustoStatus('Não foi possível salvar: ' + e.message, 'error');
    }
  }, 500);
}

boot();

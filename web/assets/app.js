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
  settingsSection: 'empresa',
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

/* ---------------------------------------------------------------- boot */

function boot() {
  document.getElementById('login-form').addEventListener('submit', onLoginSubmit);
  document.getElementById('reset-password-form').addEventListener('submit', onResetPasswordSubmit);
  document.getElementById('forgot-password-link').addEventListener('click', showResetPassword);
  document.getElementById('back-to-login-link').addEventListener('click', showLoginForm);
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
    // Every chart is ECharts now (Fase 2/3 + the gauge), so a resize only needs
    // resizeEcharts() — no more full-page rebuild to redraw a fixed-pixel-width SVG.
    resizeTimer = setTimeout(() => { if (typeof resizeEcharts === 'function') resizeEcharts(); }, 250);
  });
  refreshSession();
}

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
  // The app's own hash router (onRouteChange) treats every #... as a /página/período route
  // and rewrites it right back — a plain href="#content" anchor jump never survives that.
  // Move focus manually instead; preventDefault keeps location.hash untouched entirely.
  if (ev.target.closest('.skip-link')) {
    ev.preventDefault();
    document.getElementById('content').focus();
    return;
  }
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
  showLoginForm();
}

async function onLoginSubmit(ev) {
  ev.preventDefault();
  const email = document.getElementById('login-email').value.trim();
  const pass = document.getElementById('login-password').value;
  const btn = document.getElementById('login-submit');
  btn.disabled = true;
  document.getElementById('login-error').textContent = '';
  try {
    const res = await fetch('/api/login', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, password: pass}),
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

function showResetPassword() {
  document.getElementById('login-form').classList.add('hidden');
  document.getElementById('reset-password-form').classList.remove('hidden');
  document.getElementById('reset-password-error').textContent = '';
  document.getElementById('reset-password-success').textContent = '';
  document.getElementById('reset-email').value = document.getElementById('login-email').value;
  document.getElementById('reset-email').focus();
}

function showLoginForm() {
  document.getElementById('reset-password-form').classList.add('hidden');
  document.getElementById('login-form').classList.remove('hidden');
  document.getElementById('login-email').focus();
}

async function onResetPasswordSubmit(ev) {
  ev.preventDefault();
  const email = document.getElementById('reset-email').value.trim();
  const masterPassword = document.getElementById('reset-master-password').value;
  const newPassword = document.getElementById('reset-new-password').value;
  const btn = document.getElementById('reset-password-submit');
  const error = document.getElementById('reset-password-error');
  const success = document.getElementById('reset-password-success');
  btn.disabled = true;
  error.textContent = '';
  success.textContent = '';
  try {
    const res = await fetch('/api/reset-password', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, master_password: masterPassword, new_password: newPassword}),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Não foi possível redefinir a senha.');
    }
    success.textContent = 'Senha redefinida. Você já pode entrar com a nova senha.';
    document.getElementById('reset-master-password').value = '';
    document.getElementById('reset-new-password').value = '';
    document.getElementById('login-email').value = email;
    document.getElementById('login-password').value = '';
  } catch (e) {
    error.textContent = e.message;
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
    renderBootstrapForm();
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
  current.innerHTML = `${icon('calendar')} ${esc(periodLabel(APP.period))} <span class="caret" aria-hidden="true">▾</span>`;
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

const PAGES = ['resumo', 'precos', 'mapa', 'diagnostico', 'sazonalidade', 'visao', 'estoque',
  'financeiro', 'despesas', 'contas-pagar', 'emprestimos', 'fluxo-caixa', 'sync', 'configuracoes'];
const FINANCE_PAGES = ['financeiro', 'despesas', 'contas-pagar', 'emprestimos', 'fluxo-caixa'];
const SETTINGS_ROUTES = ['configuracoes/empresa', 'configuracoes/usuarios', 'configuracoes/calendario',
  'configuracoes/metas', 'configuracoes/alertas', 'configuracoes/integracoes'];
const SETTINGS_SECTIONS = SETTINGS_ROUTES.map((route) => route.split('/')[1]);

function parseRoute() {
  const [path, query] = location.hash.replace(/^#\/?/, '').split('?');
  const [page, segment] = (path || '').split('/');
  return {page: PAGES.includes(page) ? page : null,
    period: /^\d{4}-\d{2}$/.test(segment || '') ? segment : null,
    section: SETTINGS_SECTIONS.includes(segment) ? segment : 'empresa',
    params: new URLSearchParams(query || '')};
}

function routeHash(page, period, params) {
  if (page === 'configuracoes') {
    const section = SETTINGS_SECTIONS.includes(period) ? period : (APP.settingsSection || 'empresa');
    return `#/configuracoes/${section}`;
  }
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
  APP.settingsSection = page === 'configuracoes' ? r.section : APP.settingsSection;
  const hash = routeHash(page, page === 'configuracoes' ? APP.settingsSection : period, r.params);
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
  // Every render replaces #content's innerHTML somewhere below, which would orphan any
  // ECharts canvas mounted in the previous render (Fase 2 prototype, echarts-charts.js).
  if (typeof disposeEcharts === 'function') disposeEcharts();
  if (APP.page === 'configuracoes') return renderSettingsPage();
  if (APP.page === 'sync') return renderSyncPage();
  // Finance pages read their own /finance/* endpoints by competence — they don't need
  // the Mobne /dashboard payload this function fetches below for every other page.
  if (FINANCE_PAGES.includes(APP.page)) return renderFinancePage();
  if (!APP.period) {
    return renderEmptyState('Nenhum período sincronizado ainda. Vá em "Sincronização Mobne" e clique em Sincronizar agora.');
  }
  // Every data page reads the same /dashboard payload; reuse it until the period or its version changes.
  const info = APP.periods.find((p) => p.period === APP.period);
  const cached = APP.dashboard && APP.dashboard.period === APP.period && APP.dashboardCompany === APP.company &&
    (!info || info.version === APP.dashboard.version);
  if (!cached) {
    document.getElementById('content').innerHTML = `
      <div class="skeleton-page">
        <div class="skeleton-line skeleton-title"></div>
        <div class="skeleton-line skeleton-sub"></div>
        <div class="kpi-grid kpi-grid-4">${'<div class="skeleton-card"></div>'.repeat(4)}</div>
        <div class="skeleton-block"></div>
      </div>`;
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

// A brand-new database has no company yet — the first sync registers companies,
// so there is nothing for the normal per-company UI to show beforehand.
// This is the one-time bootstrap: the Mobne company id is entered directly, and the sync
// endpoint validates it against the live Mobne API itself (see trigger_sync in backend/api.py).
function renderBootstrapForm() {
  document.getElementById('content').innerHTML = `
    <div class="page-title">Mercado duBairro</div>
    <div class="story-box mt-16">Nenhuma empresa sincronizada ainda nesta base de dados.
      Informe o ID da empresa na Mobne para carregar os dados pela primeira vez.</div>
    <form id="bootstrap-form" class="login-card mt-16">
      <label for="bootstrap-company-id" class="field-label">ID da empresa (Mobne)</label>
      <input type="number" id="bootstrap-company-id" class="login-input" min="1" step="1" required>
      <button type="submit" class="btn-primary" id="bootstrap-submit">Sincronizar</button>
      <div id="bootstrap-status" class="sim-status" role="status" aria-live="polite"></div>
    </form>`;
  document.getElementById('bootstrap-form').addEventListener('submit', onBootstrapSubmit);
}

async function onBootstrapSubmit(ev) {
  ev.preventDefault();
  const id = parseInt(document.getElementById('bootstrap-company-id').value, 10);
  const btn = document.getElementById('bootstrap-submit');
  const status = document.getElementById('bootstrap-status');
  btn.disabled = true;
  status.className = 'sim-status';
  status.textContent = 'Iniciando sincronização — aguarde…';
  try {
    const {job_id} = await api(`/api/companies/${id}/sync`, {method: 'POST', body: JSON.stringify({mode: 'recent'})});
    // Company registration precedes sales ingestion. Follow the job, not the company list.
    for (let attempt = 0; attempt < 100; attempt++) {
      const job = await api(`/api/sync-jobs/${job_id}`);
      if (job.state === 'failed' || job.state === 'completed_with_errors') {
        throw new Error(job.error || job.detail || 'A sincronização não foi concluída.');
      }
      if (job.state === 'completed') {
        const session = await api('/api/session');
        if (!(session.companies || []).some((company) => company.id === id)) {
          throw new Error('A empresa não está disponível após a sincronização.');
        }
        // Open the requested company even when Mobne returns multiple companies.
        session.companies.sort((a, b) => Number(b.id === id) - Number(a.id === id));
        status.className = 'sim-status ok';
        status.textContent = 'Sincronizado! Carregando o painel…';
        await onAuthenticated(session);
        return;
      }
      status.textContent = job.detail || 'Aguardando o início da sincronização…';
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
    status.textContent = 'A sincronização ainda está em andamento. Clique novamente para acompanhar a execução.';
    btn.disabled = false;
  } catch (e) {
    status.className = 'sim-status error';
    status.textContent = 'Não foi possível sincronizar: ' + e.message;
    btn.disabled = false;
  }
}

function headerBlock(data) {
  const [y, m] = data.period.split('-');
  const label = `${MONTHS[parseInt(m, 10) - 1]}/${y}`;
  const partial = data.partial_month ? ` · em andamento (dados até ${data.as_of})` : '';
  return `
    <div class="page-title">${icon('bar-chart-3')} Resumo Executivo</div>
    <div class="page-subtitle">Vendas PDV reconciliadas diretamente do Mobne — sem Excel, sem localStorage.</div>
    <span class="periodo-badge">${label}${partial}</span>
    <span class="periodo-badge muted">Atualizado ${dt(data.updated_at)} · v${data.version}</span>
    ${reconciliationBanner(data.reconciliation)}
    ${dataQualityBanner(data.totals)}
  `;
}

function reconciliationBanner(r) {
  if (r.exact_match) {
    return `<div class="disclosure-banner">${icon('circle-check')} Reconciliado exatamente com a Análise Mobne — ${money(r.receipt_revenue)} conferido, sem divergência.</div>`;
  }
  return `<div class="disclosure-banner warn">${icon('triangle-alert')} Diferença de ${money(r.difference)} entre Cupom e Análise Mobne
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
  return `<div class="disclosure-banner warn">${icon('triangle-alert')} ${pct(t.zero_cost_ratio * 100)} dos itens vendidos neste período
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
    return `<div class="alert-card severity-${esc(a.severity)}"><span>${icon('triangle-alert')} ${esc(a.message)}</span>${link}</div>`;
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
    .map((p) => ({label: p.name, value: p.profit / 100, display: money(p.profit)}));

  document.getElementById('content').innerHTML = `
    ${headerBlock(data)}
    ${alertsBlock(data.alerts)}
    <div class="story-box">${icon('lightbulb')} ${esc(resumoNarrative(data))}</div>

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
    ${explain('Resultado simulado e Ponto de equilíbrio',
      'Resultado simulado = receita − custo dos itens conhecidos − custo fixo cadastrado do mês. Ponto de equilíbrio = custo fixo ÷ margem do período.',
      'Nenhum dos dois é o lucro líquido contábil — despesas reais não são confirmadas com o Mobne; são apenas simulações a partir das vendas e do custo cadastrado.',
      'Ambos ficam indisponíveis quando há itens vendidos sem custo conhecido no período, em vez de usar uma margem parcial como se fosse a real.')}

    <div class="row">
      <div class="col-60">
        <div class="section-header">Receita diária</div>
        <div class="chart-container chart-box" id="echart-daily"></div>
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
    <div class="chart-container chart-h-330" id="echart-top10"></div>
    ${topProfit.length ? `<div class="story-box">${icon('lightbulb')} Os 10 produtos mais lucrativos representam ${t.profit ? pct(topProfitSum / t.profit * 100) : '—'} do lucro do mês.
      ${esc(topProfit[0].name)} lidera com ${money(topProfit[0].profit)}.
      <button type="button" class="btn-link" data-nav="mapa">Ver curva ABC completa em Mapa de Produtos →</button></div>` : ''}

    <div class="section-header">Histórico mensal</div>
    <div class="chart-container chart-box" id="echart-timeline"></div>
  `;
  // Mounted after innerHTML so the container elements exist; each sizes itself off its
  // own CSS height (.chart-h-*) rather than a fixed viewBox like the old SVG charts.
  mountEchartLine(document.getElementById('echart-daily'), dailyPoints);
  mountEchartBar(document.getElementById('echart-timeline'), timelinePoints);
  mountEchartBarH(document.getElementById('echart-top10'), topPoints);
}

/* ---------------------------------------------------------------- Produtos & Estoque */

// 'ruptura', 'abaixo-custo' and 'sem-custo' must stay identical to the predicates behind the
// Resumo alerts (backend/api.py dashboard()), so "Ver produtos" lists exactly what was counted.
const ESTOQUE_FILTERS = [
  {key: '', label: 'Todos', test: () => true},
  {key: 'curva-a', label: 'Curva A', test: (p) => p.abc === 'A'},
  {key: 'ruptura', label: 'Curva A sem estoque', test: (p) => p.abc === 'A' && p.stock != null && p.stock <= 0},
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
    <div class="page-title">${icon('package')} Produtos &amp; Estoque</div>
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

const SYNC_MODE_LABELS = {recent: 'Recente', history: 'Histórico completo', reconcile: 'Reconciliação completa', month: 'Mês específico'};

function renderSyncPage() {
  const s = APP.status;
  const catalogs = s.catalogs || {};
  const catalogRows = ['categories', 'products', 'stock', 'prices'].map((key) => {
    const c = catalogs[key];
    const labels = {categories: 'Categorias', products: 'Produtos', stock: 'Estoque', prices: 'Preços'};
    return `<tr><td>${labels[key]}</td><td>${c ? num(c.count) : '—'}</td><td>${c ? dt(c.updated_at) : 'Não sincronizado'}</td></tr>`;
  }).join('');

  document.getElementById('content').innerHTML = `
    <div class="page-title">${icon('link')} Sincronização Mobne</div>
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
          <div><strong>${SYNC_MODE_LABELS[j.mode] || j.mode}</strong> ${jobBadge(j.state)}
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

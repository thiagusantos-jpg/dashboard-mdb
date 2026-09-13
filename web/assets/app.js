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
  module: null,        // 'analises' | 'financeiro': last module shown; Configurações keeps it
  moduleRoutes: null,  // last route of each module (navigation.js), restored by the switcher
  financePeriod: null, // competence month of Financeiro pages, independent of synced sales months
  settingsSection: 'empresa',
  routeParams: new URLSearchParams(),  // ?query part of the #/página/período route (e.g. filtro=ruptura)
  pickerYear: null,   // year shown in the period popover (browsing it does not change the period)
  status: null,       // last /status payload
  dashboard: null,    // last /dashboard payload
  statusTimer: null,  // background status/dashboard refresh (see STATUS_POLL_MS)
  pollTimer: null,    // 4s polling while a sync job is queued/running
  estoque: {q: '', sort: 'revenue', dir: 'desc'},  // Produtos & Estoque search/sort (filter lives in the route)
  // Page-load coordinator (page-state.js, loaded before this file): tells a
  // render whether its response still belongs to the page on screen, and
  // whether a background refresh may repaint over a form being edited.
  // The inert fallback only exists so this shell can be loaded on its own in a
  // unit test; in the browser the <script> tag is asserted by the test suite.
  pageState: typeof createPageState === 'function' ? createPageState() : {
    begin: () => null, isCurrent: () => true, markDirty: () => {},
    isDirty: () => false, canRefresh: () => true, reset: () => {},
  },
};

/* ---------------------------------------------------------------- fetch */

// A request that never answers used to hang the page forever with a skeleton.
const API_TIMEOUT_MS = 30000;

const isAbortError = (e) => !!e && (e.name === 'AbortError' || e.aborted === true);

async function api(path, opts) {
  opts = opts || {};
  const headers = Object.assign({}, opts.headers || {});
  if (opts.body) headers['Content-Type'] = 'application/json';
  if (opts.method && opts.method !== 'GET') headers['x-csrf-token'] = APP.csrf || '';
  // Caller-supplied opts.signal (a page leaving, a cancelled edit) is composed
  // with the timeout, so whichever fires first aborts this one request only.
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  let timer = null;
  if (controller) {
    if (opts.signal) {
      if (opts.signal.aborted) controller.abort();
      else opts.signal.addEventListener('abort', () => controller.abort());
    }
    timer = setTimeout(() => controller.abort(), opts.timeout || API_TIMEOUT_MS);
  }
  let res;
  try {
    res = await fetch(path, Object.assign({credentials: 'same-origin'}, opts,
      {headers, signal: controller ? controller.signal : opts.signal}));
  } catch (e) {
    // An abort is not a session expiry: never show the login screen for it.
    if (isAbortError(e)) {
      const cancelled = !!(opts.signal && opts.signal.aborted);
      const err = new Error(cancelled ? 'Consulta cancelada.'
        : `A consulta passou de ${Math.round((opts.timeout || API_TIMEOUT_MS) / 1000)} segundos sem resposta.`);
      err.name = 'AbortError';
      err.aborted = true;
      err.timedOut = !cancelled;
      throw err;
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
  if (res.status === 401) {
    showLogin('Sua sessão expirou. Entre novamente.');
    throw new Error('unauthenticated');
  }
  if (!res.ok) {
    // detail is either a plain message or the shared {code, message, fields}
    // contract; callers always get a readable message plus the flagged fields.
    let detail = null;
    try { detail = (await res.json()).detail; } catch (e) {}
    const message = typeof detail === 'string' ? detail : (detail && detail.message);
    const err = new Error(message || 'Erro ' + res.status);
    err.status = res.status;
    err.code = detail && typeof detail === 'object' ? detail.code : undefined;
    err.fields = detail && Array.isArray(detail.fields) ? detail.fields : [];
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

/* ------------------------------------------------- page-load coordination */

/* Every render path starts here. The token identifies this attempt at this
 * route; anything fetched under an older token is discarded instead of being
 * written to the DOM. Never re-read APP.page/APP.period after an await to
 * decide whether a response is still wanted — ask isCurrent(token). */
function beginPage() {
  const segment = APP.page === 'configuracoes' ? (APP.settingsSection || 'empresa') : (APP.period || '');
  return APP.pageState.begin(`${APP.page}/${segment}`);
}

/* Marks the page dirty while the user edits a form, so no background refresh
 * replaces the fields under them. addEventListener only — the CSP forbids
 * inline handlers. Only a boolean is recorded, never the typed values. */
function watchForm(form) {
  if (!form) return form;
  const mark = () => APP.pageState.markDirty(true);
  form.addEventListener('input', mark);
  form.addEventListener('change', mark);
  return form;
}

// Called after a save actually succeeded, or when the user discards on purpose.
function clearDirty() { APP.pageState.markDirty(false); }

/* Asked before committing to another route. Cancel = "Continuar editando",
 * and the caller keeps the original route. */
function confirmDiscardChanges() {
  if (APP.pageState.canRefresh()) return true;
  const discard = confirm('Há alterações não salvas nesta página.\n\n' +
    'OK — Descartar alterações e sair\nCancelar — Continuar editando');
  if (discard) clearDirty();
  return discard;
}

const REFRESH_BANNER_ID = 'refresh-available';

/* Background refresh found newer data while a form is dirty: announce it
 * without touching a single field. */
function showRefreshAvailable() {
  const content = document.getElementById('content');
  if (!content || document.getElementById(REFRESH_BANNER_ID)) return;
  const bar = document.createElement('div');
  bar.id = REFRESH_BANNER_ID;
  bar.className = 'disclosure-banner warn';
  bar.setAttribute('role', 'status');
  bar.textContent = 'Novos dados disponíveis. Seus campos preenchidos foram mantidos. ';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn-link';
  btn.textContent = 'Atualizar agora (descarta o que não foi salvo)';
  btn.addEventListener('click', () => { clearDirty(); renderPage(); });
  bar.appendChild(btn);
  content.insertBefore(bar, content.firstChild);
}

function stopTimers() {
  stopStatusPolling();
  if (APP.pollTimer) { clearInterval(APP.pollTimer); APP.pollTimer = null; }
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
  if (typeof initProfile === 'function') initProfile();
  APP.moduleRoutes = typeof createModuleRoutes === 'function' ? createModuleRoutes(safeSessionStorage()) : null;
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
  if (sync) return triggerSync(sync.dataset.sync, sync);
  const createAction = ev.target.closest('[data-create-action]');
  if (createAction) return onCreateActionFromAlert({currentTarget: createAction});
  if (ev.target.closest('[data-logout]')) return logout();
}

async function refreshSession() {
  try {
    const session = await api('/api/session');
    onAuthenticated(session);
  } catch (e) {
    // A timeout is not an expired session — say what actually happened.
    showLogin(isAbortError(e) ? e.message + ' Verifique a conexão e entre novamente.' : undefined);
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
  // Stop the background timers and invalidate every request in flight first,
  // so nothing from this session can paint over the login screen.
  stopTimers();
  APP.pageState.reset();
  try { await api('/api/logout', {method: 'POST'}); } catch (e) {}
  location.reload();
}

async function onAuthenticated(session) {
  // A repeated login (session expired, bootstrap finishing, tests) must not
  // leave the previous session's intervals running alongside the new ones.
  stopTimers();
  APP.pageState.reset();
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
  if (!APP.financePeriod) APP.financePeriod = navCurrentMonth();
  await refreshStatus();
  onRouteChange();  // applies #/página/período from the URL (reload, favoritos), else the defaults
  // The automatic worker (backend/sync.py Worker) can finish a sync with nobody watching
  // the "sync" page; without this, new months only show up after a manual reload.
  startStatusPolling();
}

/* Status only changes when a sync publishes a new dataset — once a day on the
 * cron, or on demand — so a tight poll buys nothing, and a dashboard forgotten in
 * a background tab was polling all day for a reader who wasn't there, keeping the
 * database endpoint awake (it autosuspends after 5 idle minutes) purely to answer
 * nobody. Poll only while the tab is actually on screen, and catch up the instant
 * it is again. A sync in progress is unaffected: its live progress runs on the
 * separate 4s pollTimer in refreshStatus(). */
const STATUS_POLL_MS = 300000;

function startStatusPolling() {
  stopStatusPolling();
  if (document.hidden) return;
  APP.statusTimer = setInterval(backgroundRefresh, STATUS_POLL_MS);
}

function stopStatusPolling() {
  if (APP.statusTimer) { clearInterval(APP.statusTimer); APP.statusTimer = null; }
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) return stopStatusPolling();
  startStatusPolling();
  backgroundRefresh();  // returning to the tab must show current data, not a 5-minute-old view
});

/* The 60s tick: polling status is kept apart from rendering, so a status
 * request that fails (backend restarting, network blip) leaves whatever is on
 * screen exactly as it is instead of blanking the page. */
async function backgroundRefresh() {
  try {
    await refreshStatus();
  } catch (e) {
    return;  // status indisponível: preserva o conteúdo já renderizado
  }
  if (APP.page === 'configuracoes' && APP.settingsSection === 'integracoes') {
    if (typeof refreshSyncPanel === 'function') refreshSyncPanel();
    return;
  }
  // Redraw only when the data changed — a blind redraw would wipe the Estoque search mid-typing.
  const info = APP.periods.find((p) => p.period === APP.period);
  if (APP.dashboard && !(info && info.version !== APP.dashboard.version)) return;
  // Never repaint a form being edited; offer the new data instead.
  if (!APP.pageState.canRefresh()) return showRefreshAvailable();
  renderPage();
}

/* ---------------------------------------------------------------- status/period */

async function refreshStatus() {
  APP.status = await api(`/api/companies/${APP.company}/status`);
  APP.periods = APP.status.periods || [];
  buildPeriodSelector();
  const hasActiveJob = (APP.status.jobs || []).some((j) => j.state === 'queued' || j.state === 'running');
  if (hasActiveJob && !APP.pollTimer) {
    APP.pollTimer = setInterval(async () => {
      try {
        await refreshStatus();
      } catch (e) {
        return;  // uma leitura de status que falhou não apaga a página
      }
      if (APP.page === 'configuracoes' && APP.settingsSection === 'integracoes' && typeof refreshSyncPanel === 'function') refreshSyncPanel();
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
  // Análises filters by synced sales month; Financeiro competence pages by any
  // calendar month; the other pages carry their own filters (see navigation.js).
  const mode = navRules.mode(APP.page);
  const control = document.getElementById('period-control');
  if (control) control.hidden = mode === 'none';
  if (mode === 'none') { status.innerHTML = syncStatusLink(); return; }
  if (mode === 'competence') return buildCompetenceSelector(current, status, steps);
  if (!APP.periods.length) {
    current.textContent = 'Sem períodos';
    current.disabled = true;
    steps.forEach((b) => { b.disabled = true; });
    status.innerHTML = '<a href="#/configuracoes/integracoes" class="status-link">Nenhum período sincronizado ainda.</a>';
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
  status.innerHTML = syncStatusLink() || (!info ? '' : `<a href="#/configuracoes/integracoes" class="status-link">${info.documents
    ? `Dados atualizados em ${dt(info.updated_at)}` : 'Mobne não tem vendas registradas neste período'}</a>`);
  // The grid is (re)drawn when the popover opens; redrawing it here would steal keyboard focus.
}

// Compact status outside Configurações: a running sync, linked to where it can be followed.
function syncStatusLink() {
  const active = ((APP.status && APP.status.jobs) || []).some((j) => j.state === 'queued' || j.state === 'running');
  return active ? '<a href="#/configuracoes/integracoes" class="status-link">Sincronizando dados do Mobne…</a>' : '';
}

function buildCompetenceSelector(current, status, steps) {
  if (!APP.financePeriod) APP.financePeriod = navCurrentMonth();
  current.disabled = false;
  current.innerHTML = `${icon('calendar')} Competência ${esc(periodLabel(APP.financePeriod))} <span class="caret" aria-hidden="true">▾</span>`;
  current.setAttribute('aria-label', `Competência ${periodLabel(APP.financePeriod)} — trocar mês`);
  steps.forEach((b) => {
    b.disabled = false;
    b.title = periodLabel(navShift(APP.financePeriod, parseInt(b.dataset.periodStep, 10)));
  });
  const sync = syncStatusLink();
  status.innerHTML = 'Mês de competência dos lançamentos, independente das vendas sincronizadas.' + (sync ? ` · ${sync}` : '');
}

function renderPickerGrid() {
  const competence = navRules.mode(APP.page) === 'competence';
  const selected = competence ? APP.financePeriod : APP.period;
  const thisYear = Number(navCurrentMonth().slice(0, 4));
  const years = competence
    ? [thisYear + 1, thisYear, thisYear - 1, thisYear - 2, thisYear - 3].map(String)
    : [...new Set(APP.periods.map((p) => p.period.slice(0, 4)))].sort().reverse();
  const year = APP.pickerYear || selected.slice(0, 4);
  document.getElementById('year-tabs').innerHTML = years.map((y) =>
    `<button type="button" class="tab-btn-period ${y === year ? 'active' : ''}" aria-pressed="${y === year}" data-year="${y}">${y}</button>`
  ).join('');
  const known = new Map(APP.periods.map((p) => [p.period, p]));
  document.getElementById('month-buttons').innerHTML = MONTHS.map((label, i) => {
    const period = `${year}-${String(i + 1).padStart(2, '0')}`;
    const info = known.get(period);
    const empty = !competence && info && !info.documents;  // synced, but Mobne has no sales for this period
    const active = period === selected;
    const disabled = competence || info ? '' : 'disabled';  // empty stays clickable, so its explanation is reachable
    const cls = ['month-btn', active ? 'active' : '', empty ? 'month-btn-empty' : ''].filter(Boolean).join(' ');
    const title = empty ? ' title="Mobne não tem vendas registradas neste período"' : '';
    return `<button type="button" class="${cls}" aria-pressed="${active}" ${disabled}${title} data-period="${period}">${label}</button>`;
  }).join('');
}

const isPickerOpen = () => { const p = document.getElementById('period-popover'); return !!p && !p.hidden; };

function openPeriodPicker() {
  const selected = navRules.mode(APP.page) === 'competence' ? APP.financePeriod : APP.period;
  if (!selected) return;
  APP.pickerYear = selected.slice(0, 4);
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
  if (navRules.mode(APP.page) === 'competence') return go(APP.page, navShift(APP.financePeriod, delta));
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

/* Module and route rules live in navigation.js; the guards keep this shell
 * loadable on its own in the vm-based unit tests. */
const navRules = {
  canonical: (hash) => (typeof canonicalRoute === 'function' ? canonicalRoute(hash) : hash),
  module: (page) => (typeof moduleForPage === 'function' ? moduleForPage(page) : null),
  mode: (page) => (typeof periodModeForPage === 'function' ? periodModeForPage(page) : 'sales'),
};
const navShift = (period, delta) => (typeof shiftMonth === 'function' ? shiftMonth(period, delta) : period);
const navCurrentMonth = () => (typeof currentMonth === 'function' ? currentMonth() : new Date().toISOString().slice(0, 7));
const MODULE_LABELS = {analises: 'Análises', financeiro: 'Financeiro'};

const PAGES = ['resumo', 'precos', 'mapa', 'diagnostico', 'sazonalidade', 'visao', 'estoque', 'reposicao', 'produto', 'acoes',
  'financeiro', 'despesas', 'contas-pagar', 'emprestimos', 'fluxo-caixa', 'conciliacao', 'recebiveis', 'configuracoes'];
const FINANCE_PAGES = ['financeiro', 'despesas', 'contas-pagar', 'emprestimos', 'fluxo-caixa', 'conciliacao', 'recebiveis'];
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
  const segment = navRules.mode(page) === 'none' ? null : period;
  const q = params ? params.toString() : '';
  return `#/${page}${segment ? '/' + segment : ''}${q ? '?' + q : ''}`;
}

// Every page/period change goes through the URL, so Voltar, reload and favoritos all work.
function go(page, period, params) {
  const fallback = navRules.mode(page) === 'competence' ? APP.financePeriod : APP.period;
  const hash = routeHash(page, period || fallback, params);
  if (location.hash === hash) onRouteChange(); else location.hash = hash;  // → hashchange → onRouteChange
}

function navigate(page) { go(page); }

/* Shows only the active module's menu, points each module link at that
 * module's last route, names the module in the top bar and announces a switch.
 * Configurações/Minha conta belong to no module and keep the last one open. */
function applyModuleNav(page) {
  const module = navRules.module(page);
  const previous = APP.module;
  if (module) APP.module = module;
  const active = APP.module || 'analises';
  document.querySelectorAll('[data-module-nav]').forEach((el) => { el.hidden = el.dataset.moduleNav !== active; });
  document.querySelectorAll('[data-module-link]').forEach((link) => {
    if (link.dataset.moduleLink === active) link.setAttribute('aria-current', 'true');
    else link.removeAttribute('aria-current');
    if (APP.moduleRoutes) link.setAttribute('href', APP.moduleRoutes.routeFor(link.dataset.moduleLink));
  });
  const label = document.getElementById('topbar-module');
  if (label) label.textContent = page === 'configuracoes' ? 'Configurações' : MODULE_LABELS[active];
  const announce = document.getElementById('module-status');
  if (announce && module && previous && module !== previous) announce.textContent = `Módulo ${MODULE_LABELS[module]} aberto.`;
}

function onRouteChange() {
  if (!APP.company) return;  // before login the hash is kept and applied by onAuthenticated
  // Retired routes (#/sync, #/emprestimos) resolve to their new home first.
  const canonical = navRules.canonical(location.hash);
  if (canonical !== location.hash) history.replaceState(null, '', canonical);
  const r = parseRoute();
  const page = r.page || 'resumo';
  const mode = navRules.mode(page);
  const period = mode === 'sales' && r.period && APP.periods.some((p) => p.period === r.period) ? r.period : APP.period;
  const financePeriod = mode === 'competence' && r.period ? r.period : APP.financePeriod;
  // Leaving a page with unsaved edits (nav link, Voltar, period change, go())
  // asks first; "Continuar editando" keeps the original route on screen.
  const section = page === 'configuracoes' ? r.section : APP.settingsSection;
  const leaving = page !== APP.page || period !== APP.period || financePeriod !== APP.financePeriod ||
    (page === 'configuracoes' && section !== APP.settingsSection);
  if (leaving && !confirmDiscardChanges()) {
    const stayPeriod = APP.page === 'configuracoes' ? APP.settingsSection
      : navRules.mode(APP.page) === 'competence' ? APP.financePeriod : APP.period;
    const stay = routeHash(APP.page, stayPeriod, APP.routeParams);
    if (location.hash !== stay) history.replaceState(null, '', stay);
    return;
  }
  APP.settingsSection = section;
  const segment = page === 'configuracoes' ? APP.settingsSection : mode === 'competence' ? financePeriod : period;
  const hash = routeHash(page, segment, r.params);
  if (location.hash !== hash) history.replaceState(null, '', hash);  // normalize, no extra history entry
  if (APP.moduleRoutes) APP.moduleRoutes.remember(hash);
  const pageChanged = page !== APP.page;
  APP.page = page;
  APP.period = period;
  APP.financePeriod = financePeriod;
  APP.routeParams = r.params;
  document.querySelectorAll('.nav-item').forEach((a) => {
    const on = a.dataset.page === page;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    const linkPeriod = navRules.mode(a.dataset.page) === 'competence' ? financePeriod : period;
    a.setAttribute('href', routeHash(a.dataset.page, linkPeriod));  // open-in-new-tab keeps the period
  });
  applyModuleNav(page);
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
  // Claimed before any dispatch, so even a synchronous page (sync) invalidates
  // a fetch still in flight for the page the user just left.
  const token = beginPage();
  if (APP.page === 'configuracoes') return renderSettingsPage(token);
  // Finance pages read their own /finance/* endpoints by competence — they don't need
  // the Mobne /dashboard payload this function fetches below for every other page.
  if (FINANCE_PAGES.includes(APP.page)) return renderFinancePage(token);
  if (!APP.period) {
    return renderEmptyState('Nenhum período sincronizado ainda. Abra Configurações → Integrações e sincronização e clique em Sincronizar agora.');
  }
  // Identity of this request, captured before the await: APP may already point
  // at another company/period by the time the response arrives.
  const company = APP.company, period = APP.period, page = APP.page;
  // Every data page reads the same /dashboard payload; reuse it until the period or its version changes.
  const info = APP.periods.find((p) => p.period === period);
  const cached = APP.dashboard && APP.dashboard.period === period && APP.dashboardCompany === company &&
    (!info || info.version === APP.dashboard.version);
  if (!cached) {
    document.getElementById('content').innerHTML = `
      <div class="skeleton-page">
        <div class="skeleton-line skeleton-title"></div>
        <div class="skeleton-line skeleton-sub"></div>
        <div class="kpi-grid kpi-grid-4">${'<div class="skeleton-card"></div>'.repeat(4)}</div>
        <div class="skeleton-block"></div>
      </div>`;
    let payload;
    try {
      payload = await api(`/api/companies/${company}/dashboard?period=${period}`);
    } catch (e) {
      if (!APP.pageState.isCurrent(token)) return;  // usuário já saiu desta rota
      APP.dashboard = null;
      if (e.status === 404) return renderEmptyState(e.message);
      // GET only: retry re-runs this same render on demand, never automatically.
      return renderRetryState('Não foi possível carregar os dados: ' + e.message);
    }
    if (!APP.pageState.isCurrent(token)) return;  // resposta obsoleta: descarta em silêncio
    APP.dashboard = payload;
    APP.dashboardCompany = company;
  }
  if (!APP.pageState.isCurrent(token)) return;
  const renderers = {resumo: renderResumo, estoque: renderEstoque, precos: renderPrecos, mapa: renderMapa,
    diagnostico: renderDiagnostico, sazonalidade: renderSazonalidade, visao: renderVisao,
    reposicao: renderReposicao, produto: renderProdutoDetalhe, acoes: renderAcoes};
  (renderers[page] || renderResumo)(APP.dashboard);
}

function renderEmptyState(message) {
  document.getElementById('content').innerHTML = `
    <div class="page-title">Mercado duBairro</div>
    <div class="story-box mt-16">${esc(message)}</div>`;
}

/* A failed or timed-out read offers the user the retry instead of looping on
 * its own. Only the page's own GETs are re-issued — a POST is never resent. */
function renderRetryState(message) {
  document.getElementById('content').innerHTML = `
    <div class="page-title">Mercado duBairro</div>
    <div class="story-box mt-16">${esc(message)}</div>
    <div class="btn-row"><button type="button" class="btn-primary" id="retry-page">Tentar novamente</button></div>`;
  document.getElementById('retry-page').addEventListener('click', () => renderPage());
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
    <div class="page-title">${icon('bar-chart-3')} Resumo executivo</div>
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
    return `<div class="alert-card severity-${esc(a.severity)}"><span>${icon('triangle-alert')} ${esc(a.message)}</span>
      ${link}
      <button type="button" class="btn-secondary" data-create-action="${esc(a.type)}" data-action-title="${esc(a.message)}">Criar ação</button></div>`;
  }).join('')}</div>`;
}

async function onCreateActionFromAlert(event) {
  const btn = event.currentTarget;
  const alertKey = btn.dataset.createAction;
  const title = btn.dataset.actionTitle;
  btn.disabled = true;
  btn.textContent = 'Criando…';
  try {
    await api(`/api/companies/${APP.company}/actions`, {
      method: 'POST',
      body: JSON.stringify({alert_key: alertKey, alert_version: title, title}),
    });
    btn.textContent = 'Ação criada ✓';
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Criar ação';
    let message = btn.nextElementSibling;
    if (!message || !message.matches('[data-action-error]')) {
      btn.insertAdjacentHTML('afterend', '<span class="form-error" data-action-error role="alert"></span>');
      message = btn.nextElementSibling;
    }
    message.textContent = 'Não foi possível criar a ação: ' + e.message;
  }
}

// "The month in one sentence" — revenue vs. last month and vs. the same month last year
// (each silently omitted when there is no reliable base to compare against, see backend/api.py
// comparison_for()), plus the gross margin. Results after expenses live in Financeiro.
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
  return text + ` Margem bruta de ${pct(t.margin)}.`;
}

/* Secondary reference to Financeiro in the Resumo: the same endpoint and data
 * status as Resultado gerencial, never a profit figure computed here. Hidden
 * when the profile cannot read finance; a response for a Resumo that was
 * already replaced (period change, navigation) is discarded. */
async function loadResumoManagementCard(period) {
  const box = document.getElementById('resumo-management');
  if (!box || typeof managementResultUrl !== 'function') return;
  let result;
  try {
    result = await api(managementResultUrl(period));
  } catch (e) {
    return;  // no finance access or unavailable: the commercial summary stands alone
  }
  if (!box.isConnected) return;
  const status = managementStatus(result);
  box.innerHTML = `
    <div class="settings-heading">
      <div><h2 id="resumo-management-title">Resultado gerencial — Financeiro</h2><p>${esc(status.detail)}</p></div>
      <span class="badge-warning">${esc(status.label)}</span>
    </div>
    <div class="kpi-grid kpi-grid-3">
      ${kpiCard('Resultado gerencial', money(result.managerial_result_cents),
        result.managerial_result_cents == null ? 'kpi-unavailable' : '', '', 'Após custos e despesas da competência')}
    </div>
    <a class="btn-link" href="${routeHash('financeiro', period)}">Abrir Resultado gerencial →</a>`;
  box.hidden = false;
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
      ${kpiCard('Margem bruta', pct(t.margin), t.margin == null ? 'kpi-unavailable' : '', '',
        t.unknown > 0 ? num(t.unknown) + ' itens sem custo — margem indisponível' : `Lucro bruto: ${money(t.profit)}`)}
      ${kpiCard('Ticket médio', money(t.ticket), '', deltas('ticket_change'), '')}
      ${kpiCard('Nº de cupons', num(t.receipts), '', deltas('receipts_change'), '')}
      ${kpiCard('Lucro bruto', money(t.profit), t.profit == null ? 'kpi-unavailable' : (t.profit >= 0 ? 'kpi-positive' : 'kpi-negative'),
        deltas('profit_change'), t.unknown > 0 ? num(t.unknown) + ' itens sem custo — não estimados' : 'Todos os itens com custo')}
      ${kpiCard('Taxa de cancelamento', pct(cancelRate), '', '', `${num(t.cancelled)} de ${num(cancelBase)} documentos`)}
    </div>
    <section class="management-summary" id="resumo-management" aria-labelledby="resumo-management-title" hidden></section>

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
      <button type="button" class="btn-link" data-nav="mapa">Ver curva ABC completa em Mapa de produtos →</button></div>` : ''}

    <div class="section-header">Histórico mensal</div>
    <div class="chart-container chart-box" id="echart-timeline"></div>
  `;
  loadResumoManagementCard(data.period);
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
    <div class="page-title">${icon('package')} Produtos e estoque</div>
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
    return `<tr><td><a href="#/produto/${esc(APP.period)}?id=${esc(p.id)}">${esc(p.name)}</a></td><td>${esc(p.category)}</td>
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

/* ---------------------------------------------------------------- sync trigger */

// The panel lives in Configurações → Integrações (settings.js syncPanelHtml).
async function triggerSync(mode, button) {
  const status = document.getElementById('sync-action-status');
  if (button) button.disabled = true;
  if (status) status.textContent = '';
  try {
    const result = await api(`/api/companies/${APP.company}/sync`, {method: 'POST', body: JSON.stringify({mode})});
    if (status) {
      status.textContent = result && result.already_running
        ? 'Já existe uma sincronização em andamento: acompanhe o progresso abaixo.'
        : 'Sincronização iniciada.';
    }
    await refreshStatus();
    if (typeof refreshSyncPanel === 'function') refreshSyncPanel();
  } catch (e) {
    if (status) status.textContent = 'Não foi possível iniciar a sincronização: ' + e.message;
    if (button) button.disabled = false;
  }
}

boot();

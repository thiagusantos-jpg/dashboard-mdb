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
  syncPending: null,  // sync mode just clicked, until the status poll sees its job row
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
  APP.userName = session.name || '';
  APP.welcomeMessage = chooseWelcomeMessage(new Date());  // a new one on every login or reload
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
  if (APP.company == null) return;  // login screen or bootstrap form: nothing to refresh yet
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
  // Timers and visibilitychange can fire before login (or on the bootstrap form)
  // picks a company; asking for /companies/null/status only produced server errors.
  if (APP.company == null) return;
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
const SETTINGS_ROUTES = ['configuracoes/empresa', 'configuracoes/usuarios', 'configuracoes/cadastros', 'configuracoes/calendario',
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
    // The Resumo's placeholder has the Resumo's shape (welcome card, four KPIs,
    // attention list, charts row), so nothing jumps when the real page lands.
    document.getElementById('content').innerHTML = page === 'resumo' ? `
      <div class="skeleton-page" aria-busy="true" aria-label="Carregando o resumo">
        <div class="skeleton-block skeleton-welcome"></div>
        <div class="kpi-grid kpi-grid-4">${'<div class="skeleton-card"></div>'.repeat(4)}</div>
        <div class="skeleton-block skeleton-attention"></div>
        <div class="skeleton-row"><div class="skeleton-block"></div><div class="skeleton-block"></div></div>
      </div>` : `
      <div class="skeleton-page" aria-busy="true">
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
  // Every analytics page draws charts; the library arrives only now (C7).
  if (typeof ensureEcharts === 'function') {
    try {
      await ensureEcharts();
    } catch (e) {
      if (!APP.pageState.isCurrent(token)) return;
      return renderRetryState(e.message + ' Verifique a conexão e tente novamente.');
    }
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
// The arrow repeats the color's meaning for anyone who can't tell red from green;
// the hidden word gives screen readers the direction the arrow shows.
function deltaChip(label, value, positiveIsGood) {
  if (positiveIsGood == null) positiveIsGood = true;
  if (value == null) return `<span class="delta-chip">${esc(label)}: —</span>`;
  const flat = Math.abs(value) < 0.05;
  const good = positiveIsGood ? value >= 0 : value <= 0;
  const cls = flat ? '' : (good ? 'delta-positive' : 'delta-negative');
  const arrow = flat ? '=' : (value > 0 ? '▲' : '▼');
  const word = flat ? 'Estável' : (value > 0 ? 'Alta de' : 'Queda de');
  return `<span class="delta-chip ${cls}" title="${word} ${pct(Math.abs(value))} ${esc(label)}">` +
    `<span class="delta-arrow" aria-hidden="true">${arrow}</span><span class="visually-hidden">${word}</span> ` +
    `${pct(Math.abs(value))} <span class="delta-ref">${esc(label)}</span></span>`;
}

// What a comparison chip is measured against. A month in progress is compared on the
// same elapsed days (backend/api.py comparison_for), so the label says "1–13/ago".
function compareRef(data, cmp) {
  if (!cmp || !cmp.period) return '';
  const [y, m] = cmp.period.split('-');
  const month = MONTHS[parseInt(m, 10) - 1].toLowerCase();
  const label = y === data.period.slice(0, 4) ? month : `${month}/${y.slice(2)}`;
  const day = parseInt(String(data.end || '').slice(8, 10), 10);
  return data.partial_month && day ? `1–${day}/${label}` : label;
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

// Backend messages read "N produto(s) …: NOME A, NOME B…". The count is what a partner
// scans; the names are only a sample, so they sit on one muted, truncated line.
function splitAlertMessage(message) {
  const text = String(message || '');
  const at = text.indexOf(': ');
  if (at < 0) return {head: text, detail: ''};
  return {head: text.slice(0, at), detail: 'Ex.: ' + text.slice(at + 2).replace(/\.$/, '')};
}

function alertsBlock(alerts) {
  if (!alerts || !alerts.length) return '';
  return `<section class="attention" aria-labelledby="attention-title">
    <h2 id="attention-title" class="attention-title">${icon('triangle-alert')} Pontos de atenção <span class="attention-count">${alerts.length}</span></h2>
    <ul class="attention-list">${alerts.map((a) => {
      const f = ALERT_FILTERS[a.type];
      const {head, detail} = splitAlertMessage(a.message);
      const link = f ? `<a class="alert-action" href="${routeHash('estoque', APP.period, new URLSearchParams({filtro: f}))}">Ver produtos →</a>` : '';
      return `<li class="attention-item severity-${esc(a.severity)}" data-alert-key="${esc(a.type)}">
        <div class="attention-text"><strong>${esc(head)}</strong>${detail ? `<span class="attention-detail" title="${esc(detail)}">${esc(detail)}</span>` : ''}</div>
        <div class="attention-actions">${link}
          <span class="action-count" data-action-count hidden></span>
          <button type="button" class="btn-secondary" data-create-action="${esc(a.type)}" data-action-title="${esc(a.message)}">Criar ação</button></div>
      </li>`;
    }).join('')}</ul>
  </section>`;
}

// Matched on the alert type only: the alert text (its "version") changes whenever the
// product count moves — 170 becomes 168 — and that must not hide the action already open.
function alertActionCount(list, alertKey) {
  return (list || []).filter((a) => a.alert_key === alertKey && (a.status === 'open' || a.status === 'in_progress')).length;
}

/* Each Resumo alert shows how many actions are already open for it, so a partner
 * follows the existing one instead of creating a duplicate. Unreadable list:
 * the alerts stay exactly as they were, with only "Criar ação". */
async function loadAlertActions() {
  const items = document.querySelectorAll('.attention-item[data-alert-key]');
  if (!items.length) return;
  let list;
  try {
    list = await api(`/api/companies/${APP.company}/actions`);
  } catch (e) {
    return;
  }
  items.forEach((item) => {
    if (!item.isConnected) return;  // Resumo replaced while the request was in flight
    const count = alertActionCount(list, item.dataset.alertKey);
    const slot = item.querySelector('[data-action-count]');
    const btn = item.querySelector('[data-create-action]');
    if (!slot) return;
    slot.innerHTML = count
      ? `<a class="action-state has" href="${routeHash('acoes', APP.period)}">${count} ${count === 1 ? 'ação aberta' : 'ações abertas'} →</a>`
      : '<span class="action-state none">Nenhuma ação</span>';
    slot.hidden = false;
    if (btn && !btn.disabled) btn.textContent = count ? 'Criar outra ação' : 'Criar ação';
  });
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
    loadAlertActions();  // the new action now counts on its alert
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
  const day = parseInt(String(data.end || '').slice(8, 10), 10);
  const partial = data.partial_month && day;
  const sign = (v) => `${v >= 0 ? '+' : ''}${pct(v)}`;
  const bits = [partial
    ? `De 1 a ${day}/${m}, ${MONTHS[parseInt(m, 10) - 1]}/${y} faturou ${money(t.revenue)}`
    : `${MONTHS[parseInt(m, 10) - 1]}/${y} faturou ${money(t.revenue)}`];
  if (mom && mom.revenue_change != null) bits.push(`${sign(mom.revenue_change)} sobre ${partial ? 'os mesmos dias do mês anterior' : 'o mês anterior'}`);
  if (cmp && cmp.revenue_change != null) bits.push(`${sign(cmp.revenue_change)} sobre ${partial ? 'o mesmo período de ' : ''}${esc(cmp.period.slice(0, 4))}`);
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
      ${(() => {
        const be = result.break_even || {};
        const available = be.break_even_cents != null;
        const note = !available ? (be.reason || '')
          : `Faturamento ${Math.abs(be.gap_pct).toFixed(1).replace('.', ',')}% ${be.gap_pct >= 0 ? 'acima' : 'abaixo'}`;
        return kpiCard('Ponto de equilíbrio', available ? money(be.break_even_cents) : 'Indisponível',
          available ? (be.gap_pct >= 0 ? 'kpi-positive' : 'kpi-negative') : 'kpi-unavailable', '', esc(note));
      })()}
    </div>
    <a class="btn-link" href="${routeHash('financeiro', period)}">Abrir Resultado gerencial →</a>`;
  box.hidden = false;
}

/* ---------------------------------------------------------------- Boas-vindas */

const WELCOME_MESSAGES = [
  'Cada venda de hoje é um vizinho escolhendo o seu mercado.',
  'Pequenos ajustes de preço e estoque somam grandes resultados no fim do mês.',
  'Os números mostram o caminho; as decisões de hoje fazem o resultado de amanhã.',
  'Quem acompanha de perto corrige cedo. Bom ter você por aqui.',
  'Produto na prateleira é cliente que volta amanhã.',
  'Constância vence pressa: melhore um ponto por semana.',
  'Margem saudável é o que mantém as portas do bairro abertas.',
  'Conhecer o cliente do bairro é a vantagem que nenhum atacarejo copia.',
];

// Extra lines that only make sense on some days, mixed into the rotation.
function contextualWelcomeMessages(date) {
  const lastDay = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
  const extra = [];
  if (date.getDate() <= 3) extra.push('Mês novo, meta nova: um bom começo facilita o fechamento.');
  if (lastDay - date.getDate() <= 4) extra.push('Reta final do mês: é hora de conferir estoque e preços dos campeões de venda.');
  if (date.getDay() === 1) extra.push('Semana começando: vale olhar os pontos de atenção antes do movimento apertar.');
  if (date.getDay() === 5 || date.getDay() === 6) extra.push('Fim de semana é pico de movimento: garanta os itens da curva A na prateleira.');
  return extra;
}

function welcomeGreeting(hour) {
  if (hour >= 5 && hour < 12) return 'Bom dia';
  if (hour >= 12 && hour < 18) return 'Boa tarde';
  return 'Boa noite';
}

function firstName(name) {
  return String(name || '').trim().split(/\s+/)[0] || '';
}

// Never repeats the previous login's message when there is another to choose.
function pickWelcomeIndex(last, count, random) {
  if (count < 2) return 0;
  if (last == null || last < 0 || last >= count) return Math.floor(random() * count);
  const i = Math.floor(random() * (count - 1));
  return i >= last ? i + 1 : i;
}

const WELCOME_STORAGE_KEY = 'mdb.welcome.last';
function chooseWelcomeMessage(date) {
  const pool = WELCOME_MESSAGES.concat(contextualWelcomeMessages(date));
  let last = null;
  try {
    const stored = localStorage.getItem(WELCOME_STORAGE_KEY);
    last = stored == null ? null : parseInt(stored, 10);
  } catch (e) { /* private window: any message will do */ }
  const index = pickWelcomeIndex(Number.isNaN(last) ? null : last, pool.length, Math.random);
  try { localStorage.setItem(WELCOME_STORAGE_KEY, String(index)); } catch (e) { /* not persisted, still shown */ }
  return pool[index];
}

function welcomeBlock(data) {
  const now = new Date();
  const name = firstName(APP.userName);
  const [y, m] = data.period.split('-');
  const label = `${MONTHS[parseInt(m, 10) - 1]}/${y}`;
  const partial = data.partial_month ? ` · parcial até ${esc(String(data.as_of).slice(8, 10))}/${esc(String(data.as_of).slice(5, 7))}` : '';
  const r = data.reconciliation;
  const recon = r.exact_match
    ? `<span class="meta-pill ok" title="Reconciliado exatamente com a Análise Mobne: ${esc(money(r.receipt_revenue))} conferido.">${icon('circle-check')} Conferido com o Mobne</span>`
    : `<span class="meta-pill warn">${icon('triangle-alert')} Diferença de ${money(r.difference)}</span>`;
  const today = new Intl.DateTimeFormat('pt-BR', {weekday: 'long', day: 'numeric', month: 'long'}).format(now);
  return `<section class="welcome" aria-labelledby="welcome-title">
    <div class="welcome-text">
      <p class="welcome-eyebrow">Resumo executivo · ${esc(today)}</p>
      <h1 id="welcome-title" class="welcome-title">${esc(welcomeGreeting(now.getHours()))}${name ? ', ' + esc(name) : ''}</h1>
      <p class="welcome-message">${esc(APP.welcomeMessage || WELCOME_MESSAGES[0])}</p>
    </div>
    <div class="welcome-meta">
      <span class="meta-pill">${label}${partial}</span>
      ${recon}
      <span class="meta-pill muted">Atualizado ${dt(data.updated_at)}</span>
    </div>
    <div id="resumo-goal" class="welcome-goal" hidden></div>
  </section>
  <p class="welcome-pulse">${icon('lightbulb')} ${esc(resumoNarrative(data))}</p>
  ${r.exact_match ? '' : reconciliationBanner(r)}
  ${dataQualityBanner(data.totals)}`;
}

/* ---------------------------------------------------------------- Números digitados */

// A number as a person in Brazil types it: "80.000,00", "80.000", "80000", "12,5", "R$ 1.234".
// A dot followed by groups of exactly three digits is a thousands separator, never a
// decimal point — reading "80.000" as 80 is how an R$ 80.000 goal was saved as R$ 80.
function parseDecimalBR(text) {
  let s = String(text == null ? '' : text).replace(/R\$|\s|%/g, '');
  if (!s) return NaN;
  if (s.includes(',')) s = s.replace(/\./g, '').replace(',', '.');
  else if (/^-?\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, '');
  return /^-?\d+(\.\d+)?$/.test(s) ? Number(s) : NaN;
}

function formatDecimalBR(value, digits) {
  return Number(value).toLocaleString('pt-BR', {minimumFractionDigits: digits, maximumFractionDigits: digits});
}

/* ---------------------------------------------------------------- Meta do mês */

function moneyShort(cents) {
  const reais = (cents || 0) / 100;
  if (Math.abs(reais) < 1000) return money(cents);
  return `R$ ${(reais / 1000).toFixed(1).replace('.', ',')} mil`;
}

// Where the month stands against its goal: share achieved, where it "should" be by
// calendar day, and the month-end total if the current daily pace holds.
function goalFigures(progress, data) {
  const [y, m] = data.period.split('-').map(Number);
  const daysInMonth = new Date(y, m, 0).getDate();
  const day = Math.min(daysInMonth, parseInt(String(data.end || '').slice(8, 10), 10) || daysInMonth);
  const target = progress.target_cents, achieved = progress.achieved_cents;
  const pctDone = target ? achieved / target * 100 : 0;
  const expectedPct = day / daysInMonth * 100;
  return {pctDone, expectedPct, onTrack: pctDone >= expectedPct, reached: target > 0 && achieved >= target,
    projection: Math.round(achieved / day * daysInMonth)};
}

function goalHtml(progress, data) {
  const f = goalFigures(progress, data);
  const [y, m] = data.period.split('-').map(Number);
  const month = new Intl.DateTimeFormat('pt-BR', {month: 'long'}).format(new Date(y, m - 1, 1));  // "setembro", not "set"
  const pace = f.reached
    ? '<span><b>Meta batida</b> — tudo o que vier agora é resultado extra.</span>'
    : `<span>${progress.remaining_days ? `Faltam <b>${progress.remaining_days} dias de funcionamento</b>` : '<b>Último dia do mês</b>'}${
      progress.required_per_day != null ? ` · precisa de <b>${money(progress.required_per_day)}/dia</b>` : ''}</span>`;
  return `
    <div class="goal-top">
      <span class="goal-label">Meta de ${esc(month)}</span>
      <span class="goal-amount"><strong>${moneyShort(progress.achieved_cents)}</strong> de ${moneyShort(progress.target_cents)}</span>
      <span class="goal-pct ${f.onTrack ? 'on-track' : ''}">${Math.round(f.pctDone)}%</span>
    </div>
    <div class="goal-track" role="img" aria-label="${Math.round(f.pctDone)}% da meta atingida; pelo calendário, o esperado hoje seria ${Math.round(f.expectedPct)}%">
      <div class="goal-fill"></div><div class="goal-pace"></div>
    </div>
    <div class="goal-meta">${pace}<span>No ritmo atual fecha em <b>${moneyShort(f.projection)}</b></span></div>`;
}

/* The goal endpoint measures the month in progress up to today, so it only joins the
 * Resumo of the current month. No goal yet: a quiet link to set one. Unreadable
 * (profile without access, network): the card simply stays without it. */
async function loadResumoGoal(data) {
  const box = document.getElementById('resumo-goal');
  if (!box || !data.partial_month || data.period !== String(data.as_of || '').slice(0, 7)) return;
  let progress;
  try {
    progress = await api(`/api/companies/${APP.company}/goals/progress`);
  } catch (e) {
    return;
  }
  if (!box.isConnected) return;  // Resumo replaced while the request was in flight
  if (!progress) {
    box.innerHTML = '<p class="goal-empty">Nenhuma meta de faturamento para este mês. <a href="#/configuracoes/metas">Definir meta →</a></p>';
    box.hidden = false;
    return;
  }
  const f = goalFigures(progress, data);
  box.innerHTML = goalHtml(progress, data);
  // Widths through the CSSOM: the CSP (style-src 'self') blocks style="" attributes.
  box.querySelector('.goal-fill').style.width = Math.min(100, f.pctDone).toFixed(1) + '%';
  box.querySelector('.goal-pace').style.left = f.expectedPct.toFixed(1) + '%';
  box.hidden = false;
}

/* ---------------------------------------------------------------- Receita diária */

const WEEKDAYS = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'];
function weekdayOf(iso) {
  const [y, m, d] = String(iso).split('-').map(Number);
  return WEEKDAYS[new Date(y, m - 1, d).getDay()];
}

// Average over complete days only: today's partial sales would drag it down and
// flag today as a weak day before the store has even closed.
function dailyStats(daily, data) {
  const days = (daily || []).map((d) => ({date: d.date, revenue: d.revenue, weekday: weekdayOf(d.date),
    inProgress: !!(data.partial_month && d.date === data.as_of)}));
  const complete = days.filter((d) => !d.inProgress);
  const avg = complete.length ? complete.reduce((s, d) => s + d.revenue, 0) / complete.length : null;
  days.forEach((d) => { d.weak = avg != null && !d.inProgress && d.revenue < avg / 2; });
  return {days, avg};
}

function dailyInsight(stats) {
  if (stats.avg == null) return '';
  const weak = stats.days.filter((d) => d.weak).map((d) => `${d.date.slice(8, 10)}/${d.date.slice(5, 7)} (${d.weekday})`);
  const text = `Média de <b>${money(Math.round(stats.avg))}</b> por dia · ${weak.length
    ? `abaixo da metade da média: <b>${esc(weak.join(', '))}</b>` : 'nenhum dia abaixo da metade da média'}.`;
  return stats.days.some((d) => d.inProgress) ? text + ' Hoje ainda está em andamento e não entra na média.' : text;
}

/* ---------------------------------------------------------------- Top 10 por lucro */

// A product's margin read against the store's own margin for the same period:
// at or above it is good; under 60% of it earns little per unit however much it sells.
function marginBand(margin, storeMargin) {
  if (margin == null || storeMargin == null) return 'mid';
  if (margin >= storeMargin) return 'good';
  return margin < storeMargin * 0.6 ? 'low' : 'mid';
}

const MARGIN_BAND_WORDS = {good: 'acima da margem da loja', mid: 'abaixo da margem da loja', low: 'bem abaixo da margem da loja'};

function topProfitHtml(top, storeMargin, period) {
  if (!top.length) return '<div class="chart-empty">Sem produtos com custo conhecido neste período.</div>';
  const max = top[0].profit || 1;
  return `<div class="top-head" aria-hidden="true"><span>Produto</span><span>Lucro</span><span>Margem</span></div>
    <ol class="top-list">${top.map((p) => {
      const band = marginBand(p.margin, storeMargin);
      return `<li>
        <a class="top-name" href="#/produto/${esc(period)}?id=${esc(p.id)}" title="${esc(p.name)}">${esc(p.name)}</a>
        <span class="top-profit">${money(p.profit)}</span>
        <span class="top-margin ${band}">${pct(p.margin)}${storeMargin == null ? '' : `<span class="visually-hidden">, ${MARGIN_BAND_WORDS[band]}</span>`}</span>
        <progress class="cat-bar top-bar" max="${max}" value="${Math.max(0, p.profit)}" aria-hidden="true"></progress>
      </li>`;
    }).join('')}</ol>`;
}

// Names the best-selling-but-thin-margin case among the top five, if there is one.
function topProfitInsight(top, storeMargin) {
  const index = top.slice(0, 5).findIndex((p) => marginBand(p.margin, storeMargin) === 'low');
  if (index < 0) return '';
  const p = top[index];
  return ` ${esc(p.name)} é o ${index + 1}º em lucro, mas com margem de ${pct(p.margin)} (loja: ${pct(storeMargin)}): vende muito e ganha pouco por unidade.`;
}

function categoryBars(categories) {
  const top = (categories || []).slice().sort((a, b) => b.revenue - a.revenue).slice(0, 8);
  if (!top.length) return '<div class="chart-empty">Sem categorias neste período.</div>';
  const max = top[0].revenue || 1;
  return `<ol class="cat-list">${top.map((c) => `<li>
      <div class="cat-row"><span class="cat-name">${esc(c.name)}</span><span class="cat-value">${money(c.revenue)}</span></div>
      <progress class="cat-bar" max="${max}" value="${Math.max(0, c.revenue)}" aria-label="${esc(c.name)}: ${esc(money(c.revenue))}"></progress>
      <div class="cat-meta">Margem ${pct(c.margin)}</div>
    </li>`).join('')}</ol>`;
}

function renderResumo(data) {
  const t = data.totals, cmp = data.comparison, mom = data.comparison_mom;
  const momRef = 'vs ' + (compareRef(data, mom) || 'mês ant.'), yoyRef = 'vs ' + (compareRef(data, cmp) || 'ano ant.');
  const deltas = (change) => [deltaChip(momRef, mom && mom[change]), deltaChip(yoyRef, cmp && cmp[change])].join('');
  const daily = dailyStats(data.daily, data);
  const cancelBase = t.receipts + t.cancelled;
  const cancelRate = cancelBase ? (t.cancelled / cancelBase) * 100 : null;

  const timelinePoints = (data.timeline || []).map((tl) => ({label: tl.period, value: tl.revenue / 100, display: money(tl.revenue)}));
  const topProfit = (data.products || []).filter((p) => p.profit != null).slice().sort((a, b) => b.profit - a.profit).slice(0, 10);
  const topProfitSum = topProfit.reduce((s, p) => s + p.profit, 0);

  // Four answers a partner looks for first: how much came in, what was left, how
  // big a purchase is and how many there were. Margin and cancellations ride along
  // as each card's second line instead of competing as cards of their own.
  document.getElementById('content').innerHTML = `
    ${welcomeBlock(data)}

    <div class="kpi-grid kpi-grid-4">
      ${kpiCard('Faturamento', money(t.revenue), '', deltas('revenue_change'), `${num(t.receipts)} cupons no período`)}
      ${kpiCard('Lucro bruto', money(t.profit), t.profit == null ? 'kpi-unavailable' : (t.profit >= 0 ? 'kpi-positive' : 'kpi-negative'),
        deltas('profit_change'), t.margin == null ? `${num(t.unknown)} itens sem custo — margem indisponível` : `Margem bruta de ${pct(t.margin)}`)}
      ${kpiCard('Ticket médio', money(t.ticket), '', deltas('ticket_change'), 'Valor médio por cupom')}
      ${kpiCard('Cupons', num(t.receipts), '', deltas('receipts_change'),
        cancelRate == null ? '' : `${pct(cancelRate)} cancelados (${num(t.cancelled)})`)}
    </div>

    ${alertsBlock(data.alerts)}
    <section class="management-summary" id="resumo-management" aria-labelledby="resumo-management-title" hidden></section>

    <div class="row">
      <div class="col-60">
        <h2 class="section-header">Receita diária</h2>
        <div class="chart-container chart-box" id="echart-daily"></div>
        <p class="daily-insight">${dailyInsight(daily)}</p>
      </div>
      <div class="col-40">
        <h2 class="section-header">Categorias que mais vendem</h2>
        <div class="cat-card">
          ${categoryBars(data.categories)}
          <button type="button" class="btn-link" data-nav="mapa">Ver todas no Mapa de produtos →</button>
        </div>
      </div>
    </div>

    <h2 class="section-header">Top 10 produtos por lucro</h2>
    <div class="top-card">${topProfitHtml(topProfit, t.margin, data.period)}</div>
    ${topProfit.length ? `<div class="story-box">${icon('lightbulb')} Os 10 produtos mais lucrativos representam ${t.profit ? pct(topProfitSum / t.profit * 100) : '—'} do lucro do mês.
      ${esc(topProfit[0].name)} lidera com ${money(topProfit[0].profit)}.${topProfitInsight(topProfit, t.margin)}
      <button type="button" class="btn-link" data-nav="mapa">Ver curva ABC completa em Mapa de produtos →</button></div>` : ''}

    <h2 class="section-header">Histórico mensal</h2>
    <div class="chart-container chart-box" id="echart-timeline"></div>
  `;
  loadResumoManagementCard(data.period);
  loadResumoGoal(data);
  loadAlertActions();
  // Mounted after innerHTML so the container elements exist; each sizes itself off its
  // own CSS height (.chart-h-*) rather than a fixed viewBox like the old SVG charts.
  mountEchartDaily(document.getElementById('echart-daily'), daily);
  mountEchartBar(document.getElementById('echart-timeline'), timelinePoints);
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

// On Vercel the POST runs the whole sync inside the request (maxDuration 300s,
// vercel.json), so it must outlive API_TIMEOUT_MS; status polling shows progress meanwhile.
const SYNC_REQUEST_TIMEOUT_MS = 320000;
const SYNC_FOLLOW_DELAY_MS = 2000;

async function followSyncProgress() {
  try {
    await refreshStatus();
  } catch (e) {
    return;
  }
  // Once the poll has seen the job row (running or already finished), the card shows the real run.
  if ((APP.status.jobs || []).length) APP.syncPending = null;
  if (typeof refreshSyncPanel === 'function') refreshSyncPanel();
}

// The panel lives in Configurações → Integrações (settings.js syncPanelHtml).
// Progress is the panel's status card; this line only reports what the card can't.
async function triggerSync(mode, button) {
  const status = document.getElementById('sync-action-status');
  const say = (text, ok) => {
    if (!status) return;
    status.textContent = text;
    status.className = ok ? 'form-success' : 'form-error';
  };
  if (button) button.disabled = true;
  say('', true);
  APP.syncPending = mode;
  if (typeof refreshSyncPanel === 'function') refreshSyncPanel();  // instant feedback, before the server answers
  const request = api(`/api/companies/${APP.company}/sync`,
    {method: 'POST', body: JSON.stringify({mode}), timeout: SYNC_REQUEST_TIMEOUT_MS});
  // The job row exists within a second: follow it instead of waiting for the run to end.
  const follow = setTimeout(followSyncProgress, SYNC_FOLLOW_DELAY_MS);
  try {
    // On Vercel this resolves only when the run is over; locally, as soon as it is queued.
    const result = await request;
    clearTimeout(follow);
    if (result && result.already_running) say('Já havia uma sincronização em andamento: o progresso dela aparece abaixo.', true);
    await followSyncProgress();
  } catch (e) {
    clearTimeout(follow);
    if (e.timedOut) {
      // Only the browser stopped waiting; the run and its job row carry on server-side.
      say('A sincronização continua no servidor: o progresso segue abaixo.', true);
      await followSyncProgress();
    } else {
      APP.syncPending = null;
      say('Não foi possível iniciar a sincronização: ' + e.message, false);
      if (typeof refreshSyncPanel === 'function') refreshSyncPanel();
    }
    if (button) button.disabled = false;
  }
}

boot();

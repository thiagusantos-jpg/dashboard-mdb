/* Page-load coordination (task A4): a late response from a route the user
 * already left must never overwrite the current page, and a form being edited
 * must never be clobbered by a background refresh. Same style as the other
 * frontend tests here: the browser source is read as text and executed in a
 * fresh vm context with hand-made document/window stubs — no DOM library. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const pageStateSource = fs.readFileSync(path.join(root, 'web/assets/page-state.js'), 'utf8');

// Just the coordinator, with no DOM at all.
function loadPageState() {
  const context = vm.createContext({console});
  vm.runInContext(pageStateSource, context);
  return context.createPageState;
}

test('a newer page invalidates the previous token and a dirty form blocks refresh', () => {
  const createPageState = loadPageState();
  const state = createPageState();
  const old = state.begin('resumo/2026-09');
  const current = state.begin('configuracoes/empresa');
  assert.equal(state.isCurrent(old), false);
  assert.equal(state.isCurrent(current), true);
  state.markDirty(true);
  assert.equal(state.canRefresh(), false);
  state.reset();
  assert.equal(state.isCurrent(current), false);
});

/* -------------------------------------------------------------------------
 * Integration tests below load the real app.js (page-state.js's only
 * consumer) in a vm context with the same hand-made document/window stubs
 * test_bootstrap_frontend.cjs uses, then replace app.js's own collaborators
 * (api, renderResumo, buildPeriodSelector, onRouteChange, ...) with spies —
 * exactly the pattern that file already established for exercising real
 * app.js control flow without a browser. */

const appSource = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');

function loadApp() {
  const elements = {};
  const element = (id) => elements[id] || (elements[id] = {
    innerHTML: '', textContent: '', value: '', className: '',
    classList: {add() {}, remove() {}},
    addEventListener() {}, appendChild() {}, insertBefore() {}, dataset: {},
  });
  const context = vm.createContext({
    console, URLSearchParams, Intl, AbortController,
    document: {
      addEventListener() {},
      getElementById: element,
      createElement: () => element(Symbol()),
      querySelectorAll: () => [],
    },
    window: {addEventListener() {}},
    location: {hash: '', origin: 'http://localhost', reload() {}},
    history: {replaceState() {}},
    confirm: () => true,
    setTimeout(fn) { fn(); },
    clearTimeout() {},
    // Real setInterval/clearInterval with counters, per the brief: lets the
    // login/logout test assert on how many handles are outstanding without
    // real timers ever firing (nothing here calls the interval callback).
    setInterval(fn) { context.__intervalId += 1; context.__activeIntervals.add(context.__intervalId); return context.__intervalId; },
    clearInterval(id) { context.__activeIntervals.delete(id); },
  });
  context.__intervalId = 0;
  context.__activeIntervals = new Set();
  vm.runInContext(pageStateSource, context);
  // FINANCE_PAGES normally comes from finance.js, loaded before app.js in
  // index.html; only renderPage() reads it, and only past the early-return
  // pages ('configuracoes', 'sync') these tests don't exercise.
  context.FINANCE_PAGES = [];
  vm.runInContext(appSource.replace(/\nboot\(\);\s*$/, ''), context);
  // `const APP = {...}` is a lexical (not a global-object) binding, so — unlike
  // app.js's top-level `function` declarations, already reachable as
  // context.renderPage etc. — it isn't visible as context.APP until bridged
  // explicitly. The object itself is unchanged; this just exposes the same
  // reference so the tests below can set/read its fields.
  vm.runInContext('globalThis.APP = APP;', context);
  // insights.js (Preços/Mapa/Diagnóstico/Sazonalidade/Visão renderers) isn't
  // loaded here; renderPage()'s renderers map references them by name even
  // when the test only exercises the 'resumo' page, so they must at least exist.
  for (const name of ['renderPrecos', 'renderMapa', 'renderDiagnostico', 'renderSazonalidade', 'renderVisao']) {
    context[name] = () => {};
  }
  return {context, elements};
}

test('an old dashboard response never overwrites the current route once a newer one has started', async () => {
  const {context} = loadApp();
  const rendered = [];
  context.renderResumo = (dashboard) => rendered.push(dashboard);
  const deferred = {};
  for (const period of ['2026-07', '2026-08']) {
    let resolve;
    const promise = new Promise((r) => { resolve = r; });
    deferred[period] = {promise, resolve};
  }
  context.api = async (path) => {
    const period = new URL(path, 'http://x').searchParams.get('period');
    return deferred[period].promise;
  };
  context.APP.page = 'resumo';
  context.APP.company = 1;
  context.APP.periods = [{period: '2026-07', version: 1}, {period: '2026-08', version: 1}];

  // Two navigations in a row, older one still in flight when the newer starts —
  // begin()/isCurrent() (not a re-read of APP after the await) decide which wins.
  context.APP.period = '2026-07';
  const first = context.renderPage();
  context.APP.period = '2026-08';
  const second = context.renderPage();

  // Resolve out of order: the OLD (2026-07) request answers after the NEW one.
  deferred['2026-08'].resolve({period: '2026-08', version: 1, fixed_cost_cents: 0});
  await second;
  deferred['2026-07'].resolve({period: '2026-07', version: 1, fixed_cost_cents: 0});
  await first;

  assert.deepEqual(rendered.map((d) => d.period), ['2026-08']);
  assert.equal(context.APP.dashboard.period, '2026-08');
});

test('a dirty form survives a background refresh tick even when the period version changed', async () => {
  const {context} = loadApp();
  context.buildPeriodSelector = () => {};
  const renderPageCalls = [];
  const refreshAvailableCalls = [];
  context.renderPage = () => renderPageCalls.push(1);
  context.showRefreshAvailable = () => refreshAvailableCalls.push(1);
  context.api = async (path) => {
    assert.match(path, /\/status$/);
    return {periods: [{period: '2026-08', version: 2}], jobs: []};
  };
  context.APP.page = 'resumo';
  context.APP.period = '2026-08';
  context.APP.dashboard = {period: '2026-08', version: 1};  // stale vs. the status response above
  context.APP.pageState.markDirty(true);  // user is mid-edit on the current page

  await context.backgroundRefresh();

  assert.deepEqual(renderPageCalls, []);
  assert.deepEqual(refreshAvailableCalls, [1]);
  assert.equal(context.APP.dashboard.version, 1, 'the dirty form\'s underlying data was not replaced');
});

test('a failed status poll preserves whatever was already rendered instead of blanking the page', async () => {
  const {context} = loadApp();
  const renderPageCalls = [];
  context.renderPage = () => renderPageCalls.push(1);
  context.api = async () => { throw new Error('offline'); };
  const previousDashboard = {period: '2026-08', version: 1};
  context.APP.page = 'resumo';
  context.APP.dashboard = previousDashboard;

  await context.backgroundRefresh();

  assert.equal(context.APP.dashboard, previousDashboard);
  assert.deepEqual(renderPageCalls, []);
});

test('repeated login/logout cycles never leave more than one statusTimer or pollTimer outstanding', async () => {
  const {context} = loadApp();
  context.onRouteChange = () => {};
  context.api = async (path, opts) => {
    if (path.endsWith('/status')) return {periods: [], jobs: [{state: 'running'}]};  // keeps pollTimer alive too
    if (path === '/api/logout') return {};
    throw new Error('Unexpected request: ' + path);
  };

  for (let i = 0; i < 4; i++) {
    await context.onAuthenticated({csrf: 'tok', companies: [{id: 1}], user: 'socio@example.com'});
    await context.logout();
  }
  await context.onAuthenticated({csrf: 'tok', companies: [{id: 1}], user: 'socio@example.com'});

  // One login/logout cycle leaves exactly the current session's statusTimer
  // (60s refresh) and pollTimer (4s, kept alive by the still-running job) —
  // never the previous cycles' on top of it.
  assert.equal(context.__activeIntervals.size, 2);
});

test('a request timeout/abort is reported as its own error and never treated as an expired session', async () => {
  const {context} = loadApp();
  const showLoginCalls = [];
  context.showLogin = (msg) => showLoginCalls.push(msg);
  // A stand-in fetch that never resolves on its own — api()'s own timeout
  // (an AbortController composed with any caller signal) must be the thing
  // that ends it, not this test racing a real wait. This harness's setTimeout
  // (borrowed from test_bootstrap_frontend.cjs) runs its callback immediately,
  // so the abort may already have happened before this promise executor runs —
  // handle both orderings the way a real fetch()/AbortSignal pair would.
  context.fetch = (url, opts) => new Promise((resolve, reject) => {
    const onAbort = () => {
      const err = new Error('aborted');
      err.name = 'AbortError';
      reject(err);
    };
    if (opts.signal.aborted) onAbort();
    else opts.signal.addEventListener('abort', onAbort);
  });

  await assert.rejects(
    context.api('/api/companies/1/dashboard?period=2026-08', {timeout: 5}),
    (err) => err.name === 'AbortError' && /segundos sem resposta/.test(err.message)
  );
  assert.deepEqual(showLoginCalls, [], 'a timeout must not be shown as an expired session');
});

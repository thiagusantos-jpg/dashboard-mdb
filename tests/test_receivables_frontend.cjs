const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');

const root = path.join(__dirname, '..');

test('receivables navigation and page exist', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const receivables = fs.readFileSync(path.join(root, 'web/assets/receivables.js'), 'utf8');
  const reconciliation = fs.readFileSync(path.join(root, 'web/assets/reconciliation.js'), 'utf8');

  assert.match(html, /data-page="recebiveis"/);
  assert.match(html, /assets\/receivables\.js/);
  assert.match(app, /['"]recebiveis['"]/);
  assert.match(receivables, /function renderRecebiveis/);
  // The Stone XML upload lives with the other data sources, in Conciliação.
  assert.match(reconciliation, /receivables-import/);
  assert.match(receivables, /openStoneImportForm/);
});

/* ---------------------------------------------------------------------------
 * Page-load coordination (task A4, closing the gap the final review flagged):
 * renderRecebiveis must accept a token, discard a stale response for a route
 * the user already left, and wrap the import form with watchForm()/clearDirty()
 * so a background refresh never clobbers an in-progress import selection. Same
 * vm-execution approach as test_page_state_frontend.cjs: real source, hand-made
 * document/window stubs, no DOM library. */

const pageStateSource = fs.readFileSync(path.join(root, 'web/assets/page-state.js'), 'utf8');
const receivablesSource = fs.readFileSync(path.join(root, 'web/assets/receivables.js'), 'utf8');

function loadReceivables() {
  const elements = {};
  const element = (id) => elements[id] || (elements[id] = {
    innerHTML: '', textContent: '', value: '',
    addEventListener() {}, dataset: {},
  });
  const listeners = {};
  const formElement = (id) => {
    const el = element(id);
    el.addEventListener = (evt, fn) => { listeners[id] = listeners[id] || {}; listeners[id][evt] = fn; };
    return el;
  };
  const context = vm.createContext({
    console,
    document: {
      getElementById(id) { return id === 'receivables-rate-edit' ? formElement(id) : (elements[id] || (id === 'content' ? element(id) : null)); },
    },
  });
  vm.runInContext(pageStateSource, context);
  context.APP = {company: 1, pageState: context.createPageState()};
  context.beginPage = () => context.APP.pageState.begin('recebiveis');
  context.watchForm = (form) => {
    if (!form) return form;
    const mark = () => context.APP.pageState.markDirty(true);
    form.addEventListener('input', mark);
    form.addEventListener('change', mark);
    return form;
  };
  context.clearDirty = () => context.APP.pageState.markDirty(false);
  context.esc = (s) => String(s == null ? '' : s);
  context.money = (cents) => cents == null ? 'Indisponível' : String(cents);
  context.dateBR = (iso) => iso || '—';
  context.kpi = (title, value, subtitle) => `<div class="kpi-title">${title}</div><div class="kpi-value ">${value}</div>${subtitle || ''}`;
  context.addMonthsISO = (iso) => iso;
  context.todayISO = () => '2026-09-16';
  context.icon = () => '';
  context.financeBasePath = () => '/api/companies/1/finance';
  context.financeError = (title, subtitle, error, retry) => { context.__financeError = {title, subtitle, error, retry}; };
  vm.runInContext(receivablesSource, context);
  return {context, elements, listeners};
}

test('a stale receivables response never overwrites a newer page once the user has navigated on', async () => {
  const {context, elements} = loadReceivables();
  const deferred = {};
  for (const key of ['first', 'second']) {
    let resolve;
    deferred[key] = {promise: new Promise((r) => { resolve = r; }), resolve};
  }
  let call = 0;
  context.api = async () => {
    call += 1;
    return call === 1 ? deferred.first.promise : deferred.second.promise;
  };

  const firstRender = context.renderRecebiveis(); // token #1
  const secondRender = context.renderRecebiveis(); // token #2, invalidates #1

  const settlement = {settlement_date: '2026-09-01', count: 1, gross_cents: 100, fee_cents: 5, net_cents: 95};
  // Newer navigation answers first, older one answers late — the late one must be dropped.
  deferred.second.resolve([[], [settlement], {effective_rate_pct: 1, contracted_rate_pct: 1, variance_pct: 0}]);
  await secondRender;
  const contentAfterSecond = elements.content.innerHTML;
  assert.match(contentAfterSecond, /Agenda de recebimentos/);

  deferred.first.resolve([[], [], {effective_rate_pct: null, contracted_rate_pct: null, variance_pct: null}]);
  await firstRender;

  assert.equal(elements.content.innerHTML, contentAfterSecond, 'the stale (first) response must not repaint #content');
});

test('the agenda uses the styled table, a heading and past days as well as upcoming ones', async () => {
  const {context, elements} = loadReceivables();
  const calls = [];
  context.api = async (url) => {
    calls.push(url);
    if (url.endsWith('/cash-accounts')) return [{id: '7', name: 'Stone'}];
    if (url.includes('expected-settlements')) {
      return [
        {settlement_date: '2026-09-01', count: 2, gross_cents: 1000, fee_cents: 20, net_cents: 980},
        {settlement_date: '2026-10-01', count: 1, gross_cents: 500, fee_cents: 10, net_cents: 490},
      ];
    }
    return {effective_rate_pct: 2, contracted_rate_pct: null, variance_pct: null};
  };
  await context.renderRecebiveis();
  const html = elements.content.innerHTML;
  assert.match(html, /<h1 class="page-title">/);
  assert.match(html, /<table class="data-table">/);
  assert.ok(calls.some((url) => url.includes('expected-settlements?start=2026-08-17&end=')), 'the window starts 30 days before today');
  assert.match(html, /Já deveria ter caído/);
  assert.match(html, /A receber/);
  assert.match(html, /A receber \(próximos 60 dias\)<\/div><div class="kpi-value ">490/, 'only upcoming net amounts count as to receive');
  assert.match(html, /Informar taxa contratada/);
  assert.match(html, /receivables-import-open/);
  assert.doesNotMatch(html, /receivables-create-account/);
});

test('without a cash account the page offers to create one instead of a dead select', async () => {
  const {context, elements} = loadReceivables();
  context.api = async (url) => (url.includes('effective-fee-report')
    ? {effective_rate_pct: null, contracted_rate_pct: null, variance_pct: null} : []);
  await context.renderRecebiveis();
  assert.match(elements.content.innerHTML, /receivables-create-account/);
  assert.match(elements.content.innerHTML, /Criar conta de caixa/);
});

test('the contracted rate accepts Brazilian decimals and rejects nonsense', () => {
  const {context} = loadReceivables();
  const ok = context.buildContractedRateRequest({rate_pct: '1,49%', effective_from: '2026-09-16'});
  assert.equal(ok.method, 'PUT');
  assert.equal(ok.path, '/api/companies/1/finance/receivables/contracted-rate');
  assert.deepEqual({...ok.body}, {rate_pct: 1.49, effective_from: '2026-09-16'});
  assert.ok(context.buildContractedRateRequest({rate_pct: 'abc', effective_from: '2026-09-16'}).errors.rate_pct);
  assert.ok(context.buildContractedRateRequest({rate_pct: '35', effective_from: '2026-09-16'}).errors.rate_pct);
  assert.ok(context.buildContractedRateRequest({rate_pct: '1', effective_from: ''}).errors.effective_from);
});

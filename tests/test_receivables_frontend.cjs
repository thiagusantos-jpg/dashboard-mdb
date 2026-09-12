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

  assert.match(html, /data-page="recebiveis"/);
  assert.match(html, /assets\/receivables\.js/);
  assert.match(app, /['"]recebiveis['"]/);
  assert.match(receivables, /receivables-import/);
  assert.match(receivables, /function renderRecebiveis/);
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
      getElementById(id) { return id === 'receivables-import-form' ? formElement(id) : element(id); },
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
  context.kpi = (title, value) => `<div>${title}:${value}</div>`;
  context.addMonthsISO = (iso) => iso;
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

test('the import form is wrapped with watchForm so an in-progress selection marks the page dirty', async () => {
  const {context, elements, listeners} = loadReceivables();
  context.api = async () => [[], [], {effective_rate_pct: null, contracted_rate_pct: null, variance_pct: null}];

  await context.renderRecebiveis();

  assert.ok(listeners['receivables-import-form'], 'the import form must have listeners attached');
  assert.equal(context.APP.pageState.isDirty(), false);
  listeners['receivables-import-form'].change(); // simulate the user picking a file / account
  assert.equal(context.APP.pageState.isDirty(), true, 'watchForm() must mark the page dirty on change');
  assert.equal(context.APP.pageState.canRefresh(), false, 'a dirty import form must block a background refresh');
});

test('a successful import clears the dirty flag before re-rendering', async () => {
  const {context, elements} = loadReceivables();
  context.api = async () => [[], [], {effective_rate_pct: null, contracted_rate_pct: null, variance_pct: null}];
  await context.renderRecebiveis();
  context.APP.pageState.markDirty(true);

  elements['receivables-account'] = {value: 'acc-1'};
  elements['receivables-file'] = {files: [{name: 'conciliacao.xml'}]};
  context.FormData = function () { this.append = () => {}; };
  context.fetch = async () => ({
    ok: true,
    json: async () => ({imported: 2, duplicates: 1}),
  });

  await context.onImportReceivables({preventDefault() {}});

  assert.equal(context.APP.pageState.isDirty(), false, 'clearDirty() must run after a successful import');
});

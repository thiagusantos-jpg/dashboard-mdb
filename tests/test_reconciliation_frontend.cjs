const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('reconciliation navigation and page exist', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const reconciliation = fs.readFileSync(path.join(root, 'web/assets/reconciliation.js'), 'utf8');

  assert.match(html, /data-page="conciliacao"/);
  assert.match(html, /assets\/reconciliation\.js/);
  assert.match(app, /['"]conciliacao['"]/);
  assert.match(reconciliation, /finance\/reconciliation/);
  assert.match(reconciliation, /function renderConciliacao/);
});

test('ambiguous suggestions surface as a pending choice, not an automatic pick', () => {
  const reconciliation = fs.readFileSync(path.join(root, 'web/assets/reconciliation.js'), 'utf8');
  assert.match(reconciliation, /Sugestão pendente/);
  assert.match(reconciliation, /data-confirm-form/);
});

/* Fontes de dados: the checklist and the bank-statement review, exercised on
 * the real source in a vm with hand-made stubs (no DOM library). */
const vm = require('node:vm');

function loadReconciliation() {
  const context = vm.createContext({console, JSON});
  context.APP = {company: 1, csrf: 'tok'};
  context.esc = (s) => String(s == null ? '' : s).replace(/</g, '&lt;');
  context.money = (cents) => `R$${cents}`;
  context.dateBR = (iso) => (iso ? String(iso).slice(0, 10) : '—');
  context.financeBasePath = () => '/api/companies/1/finance';
  context.formActions = (label) => `<button type="submit">${label}</button>`;
  vm.runInContext(fs.readFileSync(path.join(root, 'web/assets/reconciliation.js'), 'utf8'), context);
  return context;
}

test('without cash accounts the checklist asks for one first', () => {
  const context = loadReconciliation();
  const html = context.reconciliationSourcesSection([]);
  assert.match(html, /Criar conta de caixa/);
  assert.match(html, /data-source-action="create-account"/);
  assert.doesNotMatch(html, /data-source-action="bank"/);
});

test('the checklist shows, per account, which source is missing and how to fill it', () => {
  const context = loadReconciliation();
  const html = context.reconciliationSourcesSection([{
    cash_account_id: '9007199254740993', name: 'Stone', kind: 'payment',
    stone: {count: 3, last_import_at: '2026-09-15T10:00:00+00:00', last_settlement_date: '2026-10-01'},
    bank: {count: 0, last_import_at: null},
  }]);
  assert.match(html, /Em dia<\/span> <span class="source-detail">Vendas até 2026-10-01 · importado em 2026-09-15/);
  assert.match(html, /Falta<\/span> <span class="source-detail">Nunca importado/);
  assert.match(html, /data-source-action="stone" data-account="9007199254740993"/, 'big ids travel as strings');
  assert.match(html, /data-source-action="bank" data-account="9007199254740993"/);
});

test('the bank review names the already-recorded movement and keeps the backend pre-selection', () => {
  const context = loadReconciliation();
  const html = context.bankPreviewBody({
    count: 2, start: '2026-09-01', end: '2026-09-02', total_cents: -500, preview_hash: 'h',
    items: [
      {external_id: 'a', date: '2026-09-01', amount_cents: -850, description: 'PIX Hortifruti', candidate_cash_event_ids: ['55'], decision: 'link:55'},
      {external_id: 'b', date: '2026-09-02', amount_cents: 350, description: 'TED', candidate_cash_event_ids: [], decision: 'new'},
    ],
  }, {'55': {occurred_at: '2026-08-31', description: 'Pagamento Hortifruti'}});
  assert.match(html, /1 linha\(s\) parecem ser movimentos que já estão no painel/);
  assert.match(html, /<option value="link:55" selected>Já lançado: 2026-08-31 Pagamento Hortifruti<\/option>/);
  assert.match(html, /data-external-id="a"/);
  assert.doesNotMatch(html, /data-external-id="b"/, 'a line with nothing to choose shows a badge, not a select');
  assert.match(html, /Importar 2 linha\(s\)/);
});

test('committing sends the reviewed decisions with the preview hash', async () => {
  const context = loadReconciliation();
  const appended = {};
  context.FormData = function () { this.append = (k, v) => { appended[k] = v; }; };
  let requested;
  context.fetch = async (url, opts) => { requested = {url, opts}; return {ok: true, json: async () => ({imported: 1, linked: 0, duplicates: 0})}; };
  context.clearDirty = () => {};
  let submitHandler;
  const select = {dataset: {externalId: 'a'}, value: 'new'};
  const form = {
    querySelector: () => null,
    querySelectorAll: (sel) => (sel.startsWith('select') ? [select] : []),
    addEventListener: (evt, fn) => { if (evt === 'submit') submitHandler = fn; },
  };
  context.formUiFor = () => ({clearError() {}, setBusy() {}, showError(m) { throw new Error(m); }});
  let body = '';
  const drawer = {dialog: {querySelector: () => form}, setBody: (html) => { body = html; }};
  let saved = false;
  context.bindBankCommit(drawer, {accountId: '7', file: 'FILE'}, {
    preview_hash: 'hash-1',
    items: [{external_id: 'a', decision: 'link:55'}, {external_id: 'b', decision: 'new'}],
  }, {onSaved: () => { saved = true; }});
  await submitHandler({preventDefault() {}});
  assert.equal(requested.url, '/api/companies/1/finance/cash-accounts/7/bank-imports');
  assert.equal(requested.opts.headers['x-csrf-token'], 'tok');
  assert.equal(appended.preview_hash, 'hash-1');
  assert.equal(appended.file, 'FILE');
  assert.deepEqual(JSON.parse(appended.decisions), {a: 'new', b: 'new'}, 'the user override wins over the pre-selection');
  assert.match(body, /Extrato importado/);
  assert.ok(saved);
});

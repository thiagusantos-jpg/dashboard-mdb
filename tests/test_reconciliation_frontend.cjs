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
  context.kpi = (title, value, subtitle, subCls, valueCls) => `<div class="kpi ${valueCls || ''}">${title}|${value}|${subtitle || ''}</div>`;
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

test('the bank review opens only the lines that need a decision', () => {
  const context = loadReconciliation();
  const html = context.bankPreviewBody({
    count: 3, start: '2026-09-01', end: '2026-09-02', total_cents: -500, preview_hash: 'h', already_imported: 1,
    items: [
      {external_id: 'a', date: '2026-09-01', amount_cents: -850, description: 'PIX Hortifruti', candidate_cash_event_ids: ['55'], decision: 'link:55', already_imported: false},
      {external_id: 'b', date: '2026-09-02', amount_cents: 350, description: 'TED', candidate_cash_event_ids: [], decision: 'new', already_imported: false},
      {external_id: 'c', date: '2026-08-30', amount_cents: 100, description: 'Pix antigo', candidate_cash_event_ids: [], decision: 'new', already_imported: true},
    ],
  }, {'55': {occurred_at: '2026-08-31', description: 'Pagamento Hortifruti'}});
  assert.match(html, /<strong>1<\/strong> nova\(s\)/);
  assert.match(html, /<strong>1<\/strong> para revisar/);
  assert.match(html, /<strong>1<\/strong> já importada\(s\) antes/);
  assert.match(html, /<option value="link:55" selected>Já lançado: 2026-08-31 Pagamento Hortifruti<\/option>/);
  assert.equal((html.match(/data-external-id=/g) || []).length, 1, 'only the ambiguous line gets a select');
  const folded = html.slice(html.indexOf('<details'));
  assert.match(folded, /TED/);
  assert.match(folded, /Já importada/);
  assert.doesNotMatch(folded, /PIX Hortifruti/, 'a line under review is not repeated in the folded list');
  assert.match(html, /Importar 2 linha\(s\) nova\(s\)/);
});

test('a statement already imported says there is nothing new', () => {
  const context = loadReconciliation();
  const items = Array.from({length: 400}, (_, i) => ({external_id: `x${i}`, date: '2026-08-01', amount_cents: 1,
    description: 'x', candidate_cash_event_ids: [], decision: 'new', already_imported: true}));
  const html = context.bankPreviewBody({count: 400, start: '2026-08-01', end: '2026-08-01', total_cents: 400, preview_hash: 'h', already_imported: 400, items}, {});
  assert.match(html, /Confirmar \(nada novo\)/);
  assert.match(html, /Nenhuma linha precisa de revisão/);
  assert.match(html, /as 300 mais recentes/);
  assert.equal((html.match(/<tr>/g) || []).length, 301, 'header plus the 300 capped rows');
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

function stoneCheck(days, summary) {
  return {
    start: '2026-08-17', end: '2026-09-23', tolerance_cents: 100,
    summary: Object.assign({expected_cents: 0, received_cents: 0, difference_cents: 0, due_days: 0, ok_days: 0,
      divergent_days: 0, missing_days: 0, upcoming_days: 0, upcoming_cents: 0, ok_pct: null}, summary),
    days,
  };
}

const stoneDay = (over) => Object.assign({
  cash_account_id: '11', account_name: 'Stone', settlement_date: '2026-09-10', sales: 3,
  expected_cents: 1000, received_cents: null, credit: null, difference_cents: null, status: 'ok',
}, over);

const sourcesFor = (bankCount) => [{cash_account_id: '11', name: 'Stone', stone: {count: 3}, bank: {count: bankCount}}];

test('the Stone check opens on pending days and explains each one', () => {
  const context = loadReconciliation();
  const html = context.reconciliationStoneSection(stoneCheck([
    stoneDay({status: 'ok', credit: {amount_cents: 1000, occurred_at: '2026-09-10', description: 'Repasse'}, received_cents: 1000, difference_cents: 0}),
    stoneDay({settlement_date: '2026-09-11', status: 'divergent', credit: {amount_cents: 700, occurred_at: '2026-09-11', description: 'Crédito'}, received_cents: 700, difference_cents: -300}),
    stoneDay({settlement_date: '2026-09-05', status: 'missing', difference_cents: -1000}),
    stoneDay({settlement_date: '2026-09-20', status: 'upcoming'}),
  ], {expected_cents: 3000, received_cents: 1700, difference_cents: -1300, due_days: 3, ok_days: 1, ok_pct: 33}), sourcesFor(4));
  assert.equal(vm.runInContext("RECONCILIATION_STATE.stoneTab", context), 'pending');
  assert.match(html, /data-stone-tab-button="pending" aria-pressed="true">Pendências <span class="tab-count">2<\/span>/);
  assert.match(html, /Valor diferente/);
  assert.match(html, /Não caiu no banco<\/span><div class="cell-note">Nenhum crédito até 2026-09-08/);
  assert.match(html, /R\$700<div class="cell-note">2026-09-11 · Crédito/);
  assert.match(html, /value-negative">−R\$300/);
  assert.match(html, /kpi kpi-negative">Diferença\|−R\$1300/);
  assert.match(html, /1 de 3 · 2 dia\(s\) para verificar/);
  assert.doesNotMatch(html, /<th>Conta<\/th>/, 'a single account needs no account column');
});

test('a missing deposit on an account without statement asks for the statement, not blames Stone', () => {
  const context = loadReconciliation();
  const html = context.reconciliationStoneSection(stoneCheck([
    stoneDay({settlement_date: '2026-09-05', status: 'missing', difference_cents: -1000}),
  ], {expected_cents: 1000, difference_cents: -1000, due_days: 1, ok_pct: 0}), sourcesFor(0));
  assert.match(html, /Falta o extrato/);
  assert.doesNotMatch(html, /Não caiu no banco/);
  assert.match(html, /1 dia\(s\) sem extrato importado/);
});

test('with nothing pending the check opens on every day and says all arrived', () => {
  const context = loadReconciliation();
  const html = context.reconciliationStoneSection(stoneCheck([
    stoneDay({status: 'ok', credit: {amount_cents: 1000, occurred_at: '2026-09-10', description: 'x'}, received_cents: 1000, difference_cents: 0}),
  ], {expected_cents: 1000, received_cents: 1000, due_days: 1, ok_days: 1, ok_pct: 100}), sourcesFor(1));
  assert.equal(vm.runInContext("RECONCILIATION_STATE.stoneTab", context), 'all');
  assert.match(html, /Tudo o que a Stone devia caiu no banco/);
});

test('an empty period points to the Stone import', () => {
  const context = loadReconciliation();
  const html = context.reconciliationStoneSection(stoneCheck([], {}), []);
  assert.match(html, /Nenhuma venda Stone com repasse neste período/);
  assert.match(html, /17\/08|2026-08-17 a 2026-09-23/);
});

test('the page asks the Stone check for the filtered period and account', () => {
  const source = fs.readFileSync(path.join(root, 'web/assets/reconciliation.js'), 'utf8');
  assert.match(source, /reconciliation\/stone-daily\?\$\{stoneParams\}/);
  assert.match(source, /stoneParams\.set\('cash_account_id', filters\.account\)/);
});

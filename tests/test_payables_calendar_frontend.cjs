/* Contas a pagar: calendário do mês (item 3) e pagamento de várias contas de uma vez (item 1). */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

function loadFinance() {
  const context = vm.createContext({
    console, URLSearchParams, APP: {company: '1'},
    MONTHS: ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'],
    esc: (s) => String(s == null ? '' : s), icon: () => '', dt: (s) => s,
    money: (c) => (c == null ? 'Indisponível' : `R$ ${(c / 100).toFixed(2)}`),
    crypto: {randomUUID: () => 'key-1'},
    document: {getElementById: () => null, querySelectorAll: () => [], createElement: () => ({})},
    api: async () => ({}),
  });
  ['web/assets/loans.js', 'web/assets/finance-forms.js', 'web/assets/finance.js'].forEach((file) => vm.runInContext(read(file), context));
  return context;
}

const TODAY = '2026-09-15';

test('the calendar covers the whole month in Sunday-to-Saturday weeks', () => {
  const f = loadFinance();
  const weeks = Array.from(f.calendarWeeks('2026-09', TODAY), (week) => Array.from(week));
  assert.equal(weeks.length, 5);
  assert.equal(weeks[0].length, 7);
  assert.deepEqual({...weeks[0][0]}, {iso: '2026-08-30', day: 30, inMonth: false, isToday: false});
  assert.deepEqual({...weeks[0][2]}, {iso: '2026-09-01', day: 1, inMonth: true, isToday: false});
  const today = weeks.flat().find((cell) => cell.iso === TODAY);
  assert.equal(today.isToday, true);
  assert.equal(weeks[4][6].iso, '2026-10-03');
});

test('each day carries what falls due on it', () => {
  const f = loadFinance();
  const totals = f.dayTotals([
    {due_date: '2026-09-10', open_cents: 800000},
    {due_date: '2026-09-10', open_cents: 15000},
    {due_date: '2026-09-22', open_cents: 12000},
  ]);
  assert.deepEqual({...totals['2026-09-10']}, {cents: 815000, count: 2});
  assert.deepEqual({...totals['2026-09-22']}, {cents: 12000, count: 1});
  assert.equal(totals['2026-09-11'], undefined);
});

test('the month arrows cross the year', () => {
  const f = loadFinance();
  assert.equal(f.monthShift('2026-01', -1), '2025-12');
  assert.equal(f.monthShift('2026-12', 1), '2027-01');
  assert.equal(f.monthShift('2026-09', 1), '2026-10');
});

const BATCH_ITEMS = [
  {key: 'entry:1', kind: 'entry', id: '1', version: 2, description: 'Enel', due_date: '2026-09-15', open_cents: 60000, allowed_actions: ['details', 'pay']},
  {key: 'entry:2', kind: 'entry', id: '2', version: 1, description: 'Sabesp', due_date: '2026-09-12', open_cents: 15000, allowed_actions: ['details', 'pay']},
  {key: 'entry:3', kind: 'entry', id: '3', version: 1, description: 'Boleto já conciliado', due_date: '2026-09-12', open_cents: 5000, allowed_actions: ['details']},
  {key: 'loan_installment:4', kind: 'loan_installment', id: '4', version: 1, description: 'Parcela 2/12', due_date: '2026-09-20', open_cents: 105000, allowed_actions: ['details', 'pay']},
];

test('only plain bills can be paid in a batch', () => {
  const f = loadFinance();
  assert.deepEqual(Array.from(f.selectableObligations(BATCH_ITEMS), (i) => i.key), ['entry:1', 'entry:2']);
  assert.deepEqual({...f.selectionSummary(BATCH_ITEMS, ['entry:1', 'entry:2'])}, {count: 2, cents: 75000});
  assert.deepEqual({...f.selectionSummary(BATCH_ITEMS, [])}, {count: 0, cents: 0});
});

test('the batch sends one payment per bill, each for its own open balance', () => {
  const f = loadFinance();
  const batch = f.buildBatchPayments(BATCH_ITEMS, ['entry:1', 'entry:2'], {paid_at: '2026-09-15', cash_account_id: '7'});
  assert.equal(batch.errors, undefined);
  assert.deepEqual(Array.from(batch.requests, (r) => r.path), [
    '/api/companies/1/finance/obligations/entry/1/payments',
    '/api/companies/1/finance/obligations/entry/2/payments',
  ]);
  assert.deepEqual(Array.from(batch.requests, (r) => r.body.amount_cents), [60000, 15000]);
  assert.deepEqual(Array.from(batch.requests, (r) => r.body.expected_version), [2, 1]);
  assert.equal(String(batch.requests[0].body.cash_account_id), '7');
  assert.equal(batch.requests[0].description, 'Enel');

  assert.ok(f.buildBatchPayments(BATCH_ITEMS, ['entry:1'], {paid_at: '2026-09-15', cash_account_id: ''}).errors.cash_account_id);
  assert.ok(f.buildBatchPayments(BATCH_ITEMS, [], {paid_at: '2026-09-15', cash_account_id: '7'}).errors.form);
});

test('the page offers both views and the batch payment', () => {
  const finance = read('web/assets/finance.js');
  const page = finance.slice(finance.indexOf('async function renderContasPagar'));
  assert.match(page, /data-payables-view/);
  assert.match(page, /calendarHtml\(/);
  assert.match(finance, /function calendarWeeks\(/);
  assert.match(page, /data-calendar-day/);
  assert.match(page, /data-payables-select/);
  assert.match(page, /buildBatchPayments\(/);
  assert.match(page, /Idempotency-Key/);
  assert.doesNotMatch(page, /style="/);
});

/* Contas a pagar organizada por urgência: quando vence, se o caixa cobre a semana,
 * filtros rápidos e despesas previstas para confirmar. */
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
  });
  vm.runInContext(read('web/assets/finance.js'), context);
  return context;
}

const TODAY = '2026-09-15';

test('due dates read as urgency, counted in calendar days', () => {
  const f = loadFinance();
  const info = (due) => { const i = f.dueInfo(due, TODAY); return [i.bucket, i.days, i.text]; };
  assert.deepEqual(info('2026-09-10'), ['overdue', -5, 'venceu há 5 dias']);
  assert.deepEqual(info('2026-09-14'), ['overdue', -1, 'venceu ontem']);
  assert.deepEqual(info('2026-09-15'), ['today', 0, 'vence hoje']);
  assert.deepEqual(info('2026-09-16'), ['week', 1, 'vence amanhã']);
  assert.deepEqual(info('2026-09-22'), ['week', 7, 'vence em 7 dias']);
  assert.deepEqual(info('2026-09-23'), ['month', 8, 'vence em 8 dias']);
  assert.deepEqual(info('2026-10-15'), ['month', 30, 'vence em 30 dias']);
  assert.deepEqual(info('2026-10-16'), ['later', 31, 'vence em 31 dias']);
});

test('the list is grouped by urgency and each group carries its total', () => {
  const f = loadFinance();
  const items = [
    {key: 'a', due_date: '2026-09-10', open_cents: 800000},
    {key: 'b', due_date: '2026-09-12', open_cents: 15000},
    {key: 'c', due_date: '2026-09-20', open_cents: 12000},
    {key: 'd', due_date: '2026-12-01', open_cents: 30000},
  ];
  const groups = Array.from(f.groupObligations(items, TODAY));
  assert.deepEqual(groups.map((g) => g.label), ['Vencidas', 'Próximos 7 dias', 'Depois']);
  assert.deepEqual(groups.map((g) => g.cents), [815000, 12000, 30000]);
  assert.deepEqual(Array.from(groups[0].items, (i) => i.key), ['a', 'b']);
});

test('cash coverage says whether the balance pays what is due in 7 days', () => {
  const f = loadFinance();
  const ok = f.coverageMessage({cash_balance_cents: 1000000, coverage: {due_cents: 862000, shortfall_cents: 0}});
  assert.equal(ok.tone, 'ok');
  assert.match(ok.text, /cobre/);
  const short = f.coverageMessage({cash_balance_cents: 362000, coverage: {due_cents: 862000, shortfall_cents: 500000}});
  assert.equal(short.tone, 'short');
  assert.match(short.text, /Faltam R\$ 5000\.00/);
  const none = f.coverageMessage({cash_balance_cents: null, coverage: null});
  assert.equal(none.tone, 'none');
  assert.match(none.text, /Fluxo de caixa/);
});

test('quick filters become ranges counted from today', () => {
  const f = loadFinance();
  const json = (quick) => JSON.stringify(f.quickFilter(quick, TODAY));
  assert.equal(json('todas'), JSON.stringify({quick: 'todas', kind: '', status: '', due_from: '', due_to: ''}));
  assert.equal(json('vencidas'), JSON.stringify({quick: 'vencidas', kind: '', status: 'overdue', due_from: '', due_to: ''}));
  assert.equal(json('semana'), JSON.stringify({quick: 'semana', kind: '', status: '', due_from: '2026-09-15', due_to: '2026-09-22'}));
  assert.equal(json('mes'), JSON.stringify({quick: 'mes', kind: '', status: '', due_from: '2026-09-15', due_to: '2026-10-15'}));
  assert.equal(json('emprestimos'), JSON.stringify({quick: 'emprestimos', kind: 'loan_installment', status: '', due_from: '', due_to: ''}));
});

test('confirming a forecast names the version the partner saw', () => {
  const f = loadFinance();
  const request = f.buildForecastConfirm({id: '9007199254740993', version: 3});
  assert.equal(request.method, 'POST');
  assert.equal(request.path, '/api/companies/1/finance/entries/9007199254740993/confirm');
  assert.equal(JSON.stringify(request.body), JSON.stringify({expected_version: 3}));
});

test('the page reads the summary, filters in one click and groups the list', () => {
  const finance = read('web/assets/finance.js');
  const page = finance.slice(finance.indexOf('async function renderContasPagar'), finance.indexOf('/* ---------------------------------------------------------------- Empréstimos'));
  assert.match(page, /obligations\/summary/);
  assert.match(page, /data-quick-filter/);
  assert.match(page, /groupObligations\(/);
  assert.match(page, /coverageMessage\(/);
  assert.match(page, /data-forecast-confirm/);
  assert.doesNotMatch(page, />Filtrar</, 'no submit button just to filter');
  assert.doesNotMatch(page, /style="/);
});

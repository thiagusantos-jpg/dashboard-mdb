const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

function loadCashflow() {
  const context = vm.createContext({
    console,
    esc: (s) => String(s == null ? '' : s),
    money: (c) => (c == null ? 'Indisponível' : `R$ ${(c / 100).toFixed(2)}`),
    dateBR: (d) => d,
  });
  vm.runInContext(read('web/assets/cashflow.js'), context);
  return context;
}

test('cash flow navigation and page exist', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const cashflow = fs.readFileSync(path.join(root, 'web/assets/cashflow.js'), 'utf8');

  assert.match(html, /data-page="fluxo-caixa"/);
  assert.match(html, /assets\/cashflow\.js/);
  assert.match(app, /['"]fluxo-caixa['"]/);
  assert.match(cashflow, /finance\/forecast/);
  assert.match(cashflow, /function renderFluxoCaixa/);
});

test('cash flow distinguishes realized, forecast and simulated styles', () => {
  const cashflow = fs.readFileSync(path.join(root, 'web/assets/cashflow.js'), 'utf8');
  assert.match(cashflow, /Realizado/);
  assert.match(cashflow, /Previsto/);
  assert.match(cashflow, /Simulado/);
});

/* UX pass (2026-09-16): cadastro/lançamento/transferência viram gavetas, a projeção
 * ganha um gráfico de saldo realizado emendando na previsão, e a tabela de movimentos
 * agrupa por dia com o saldo do dia e marca "hoje". */

test('account, movement and transfer are drawers, not inline forms competing with the data', () => {
  const cashflow = fs.readFileSync(path.join(root, 'web/assets/cashflow.js'), 'utf8');
  assert.match(cashflow, /function openCashAccountForm/);
  assert.match(cashflow, /function openCashEventForm/);
  assert.match(cashflow, /function openCashTransferForm/);
  assert.match(cashflow, /data-cash-event-new/);
  assert.match(cashflow, /data-cash-transfer-new/);
  assert.match(cashflow, /data-cash-account-new/);
  // O formulário embutido antigo não deve sobrar.
  assert.doesNotMatch(cashflow, /id="cash-event-form"/);
  assert.doesNotMatch(cashflow, /id="cash-transfer-form"/);
});

test('the balance chart draws and the page reuses the payables outlook card instead of its own alert text', () => {
  const cashflow = fs.readFileSync(path.join(root, 'web/assets/cashflow.js'), 'utf8');
  assert.match(cashflow, /mountEchartCombo/);
  assert.match(cashflow, /outlookHtml\(cashOutlook\(forecastData\)\)/);
  assert.match(cashflow, /disposeEcharts\(\)/);
});

test('realized totals only count money that already moved, not what is still forecast or simulated', () => {
  const f = loadCashflow();
  const days = [
    {date: '2026-09-10', items: [{amount_cents: 50_000, confidence: 'realized'}, {amount_cents: -20_000, confidence: 'realized'}]},
    {date: '2026-09-16', items: [{amount_cents: -80_000, confidence: 'forecast'}, {amount_cents: 30_000, confidence: 'simulated'}]},
  ];
  // {...}: o objeto nasce dentro do vm e deepEqual não cruza realms.
  assert.deepEqual({...f.cashflowRealizedTotals(days)}, {incoming_cents: 50_000, outgoing_cents: -20_000});
});

test('no history means zero realized totals, not a crash', () => {
  const f = loadCashflow();
  assert.deepEqual({...f.cashflowRealizedTotals(null)}, {incoming_cents: 0, outgoing_cents: 0});
  assert.deepEqual({...f.cashflowRealizedTotals([])}, {incoming_cents: 0, outgoing_cents: 0});
});

test('the balance line is solid up to today and dashed from today on, touching at the same point', () => {
  const f = loadCashflow();
  const history = [
    {date: '2026-09-14', balance_cents: 100_000},
    {date: '2026-09-15', balance_cents: 120_000},
    {date: '2026-09-16', balance_cents: 90_000}, // hoje, duplicado do primeiro dia da projeção
  ];
  const forecast = [
    {date: '2026-09-16', balance_cents: 90_000},
    {date: '2026-09-17', balance_cents: 40_000},
    {date: '2026-09-18', balance_cents: -10_000},
  ];
  // Array.from: o array nasce dentro do vm e deepEqual não cruza realms.
  const series = f.cashflowBalanceSeries(history, forecast, '2026-09-16');
  assert.deepEqual(Array.from(series.labels), ['2026-09-14', '2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18']);
  assert.deepEqual(Array.from(series.realized), [100_000, 120_000, 90_000, null, null]);
  assert.deepEqual(Array.from(series.forecast), [null, null, 90_000, 40_000, -10_000]);
});

test('missing history still draws the forecast segment on its own, starting at today', () => {
  const f = loadCashflow();
  const forecast = [{date: '2026-09-16', balance_cents: 5_000}, {date: '2026-09-17', balance_cents: 2_000}];
  const series = f.cashflowBalanceSeries([], forecast, '2026-09-16');
  assert.deepEqual(Array.from(series.labels), ['2026-09-16', '2026-09-17']);
  assert.deepEqual(Array.from(series.realized), [5_000, null]);
  assert.deepEqual(Array.from(series.forecast), [5_000, 2_000]);
});

test('each confidence level gets its own badge color, an unknown one falls back to muted', () => {
  const f = loadCashflow();
  assert.match(f.confidenceBadge('realized'), /badge-success/);
  assert.match(f.confidenceBadge('forecast'), /badge-info/);
  assert.match(f.confidenceBadge('simulated'), /badge-warning/);
  assert.match(f.confidenceBadge('mystery'), /badge-muted/);
});

test('movements group by day; a filtered-out day disappears instead of showing an empty group', () => {
  const f = loadCashflow();
  const days = [
    {date: '2026-09-16', balance_cents: 500, items: [{amount_cents: 1_000, description: 'Venda'}, {amount_cents: -400, description: 'Boleto'}]},
    {date: '2026-09-17', balance_cents: 300, items: [{amount_cents: -200, description: 'Fornecedor'}]},
  ];
  const all = f.movementGroups(days, 'todas');
  assert.equal(all.length, 2);
  const onlyIncoming = f.movementGroups(days, 'entradas');
  assert.equal(onlyIncoming.length, 1);
  assert.equal(onlyIncoming[0].date, '2026-09-16');
  assert.equal(onlyIncoming[0].items.length, 1);
});

test("today's group and a negative-balance day both say so in the text, not only in color", () => {
  const f = loadCashflow();
  const days = [
    {date: '2026-09-16', balance_cents: 500, items: [{amount_cents: 500, description: 'Venda', confidence: 'realized'}]},
    {date: '2026-09-18', balance_cents: -200, items: [{amount_cents: -200, description: 'Boleto', confidence: 'forecast'}]},
  ];
  const html = f.movementGroupsHtml(f.movementGroups(days, 'todas'), '2026-09-16');
  assert.match(html, /class="payables-group today"/);
  assert.match(html, /· Hoje/);
  assert.match(html, /class="payables-group negative"/);
  assert.match(html, /· negativo/);
});

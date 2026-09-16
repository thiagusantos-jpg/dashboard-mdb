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

/* Detalhe por conta: a projeção é consolidada e uma conta a pagar só escolhe de qual conta
 * sai na hora do pagamento, então quem responde "o que entrou e saiu nesta conta" é o
 * extrato do realizado, não um filtro na projeção. */

test('the statement walks the balance backwards from the current one, newest row first', () => {
  const f = loadCashflow();
  // A API devolve do mais novo para o mais antigo; o saldo atual da conta é 200,00.
  const events = [
    {id: 3, occurred_at: '2026-09-16', description: 'Venda', amount_cents: 10_000, kind: 'entry'},
    {id: 2, occurred_at: '2026-09-15', description: 'Boleto', amount_cents: -5_000, kind: 'entry'},
    {id: 1, occurred_at: '2026-09-14', description: 'Saldo inicial', amount_cents: 15_000, kind: 'entry'},
  ];
  const rows = f.statementRows(events, 20_000);
  assert.deepEqual(Array.from(rows.map((r) => r.balance_cents)), [20_000, 10_000, 15_000]);
  // O primeiro lançamento da lista fecha no saldo atual, e cada linha anterior desconta o próprio valor.
  assert.equal(rows[0].description, 'Venda');
});

test('an account with more movements than the API returns still shows honest running balances', () => {
  const f = loadCashflow();
  // Só as 200 mais novas voltam: andando para trás, o que é mais antigo nunca entra na conta.
  const events = [{id: 9, occurred_at: '2026-09-16', description: 'Última', amount_cents: 1_000, kind: 'entry'}];
  const rows = f.statementRows(events, 500_000);
  assert.equal(rows[0].balance_cents, 500_000);
});

test('an empty account says so instead of drawing an empty table', () => {
  const f = loadCashflow();
  assert.deepEqual(Array.from(f.statementRows(null, 0)), []);
  assert.match(f.statementHtml([]), /Nenhum movimento nesta conta ainda/);
});

test('transfers and reversals are tagged in the statement; a plain entry is not', () => {
  const f = loadCashflow();
  const html = f.statementHtml(f.statementRows([
    {id: 3, occurred_at: '2026-09-16', description: 'Para o banco', amount_cents: -1_000, kind: 'transfer'},
    {id: 2, occurred_at: '2026-09-15', description: 'Estorno: erro de digitação', amount_cents: 500, kind: 'reversal'},
    {id: 1, occurred_at: '2026-09-14', description: 'Venda', amount_cents: 2_000, kind: 'entry'},
  ], 1_500));
  assert.match(html, /Transferência<\/span>/);
  assert.match(html, /Estorno<\/span>/);
  assert.doesNotMatch(html, /Venda<\/td>[\s\S]*?payables-tag/);
});

test('the account statement is a drawer and the projection says out loud that it is consolidated', () => {
  const cashflow = fs.readFileSync(path.join(root, 'web/assets/cashflow.js'), 'utf8');
  assert.match(cashflow, /function openCashStatement/);
  assert.match(cashflow, /data-cash-statement/);
  assert.match(cashflow, /cash-events\?cash_account_id=/);
  assert.match(cashflow, /Projeção consolidada de todas as contas/);
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

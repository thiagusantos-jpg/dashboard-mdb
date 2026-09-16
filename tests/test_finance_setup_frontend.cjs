/* Primeiro pacote do financeiro: aviso de custo zero no Resultado gerencial, cadastro
 * guiado das despesas fixas e lembrete de fechamento na Central de Ações. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

function loadFinance() {
  const context = vm.createContext({
    console, URLSearchParams, APP: {company: '1', financePeriod: '2026-08'},
    MONTHS: ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'],
    esc: (s) => String(s == null ? '' : s), icon: () => '', dt: (s) => s,
    money: (c) => (c == null ? 'Indisponível' : `R$ ${(c / 100).toFixed(2)}`),
    crypto: {randomUUID: () => 'k'},
    document: {getElementById: () => null, querySelectorAll: () => [], createElement: () => ({})},
    api: async () => ({}),
  });
  ['web/assets/loans.js', 'web/assets/finance-forms.js', 'web/assets/finance.js'].forEach((file) => vm.runInContext(read(file), context));
  return context;
}

test('the zero-cost warning names the share of revenue and stays quiet below 1%', () => {
  const f = loadFinance();
  const note = f.costQualityNote({cost_quality: {zero_cost_share_pct: 12.4, zero_cost_items: 38, zero_cost_revenue_cents: 992000}});
  assert.match(note, /12,4% do faturamento/);
  assert.match(note, /38 itens/);
  assert.equal(f.costQualityNote({cost_quality: {zero_cost_share_pct: 0.6, zero_cost_items: 2, zero_cost_revenue_cents: 5000}}), null);
  assert.equal(f.costQualityNote({cost_quality: null}), null);
});

test('the fixed expense setup sends only the filled rows, in cents', () => {
  const f = loadFinance();
  const request = f.buildFixedSetupRequest('2026-08', [
    {system_key: 'rent', amount: '3.500,00', due_day: '10'},
    {system_key: 'water', amount: '', due_day: '10'},
    {system_key: 'electricity', amount: '800', due_day: '15'},
  ]);
  assert.equal(request.method, 'POST');
  assert.equal(request.path, '/api/companies/1/finance/fixed-expenses');
  assert.equal(JSON.stringify(request.body), JSON.stringify({period: '2026-08', items: [
    {system_key: 'rent', amount_cents: 350000, due_day: 10},
    {system_key: 'electricity', amount_cents: 80000, due_day: 15},
  ]}));

  assert.ok(f.buildFixedSetupRequest('2026-08', [{system_key: 'rent', amount: '', due_day: '10'}]).errors.form);
  const bad = f.buildFixedSetupRequest('2026-08', [{system_key: 'rent', amount: 'abc', due_day: '40'}]).errors;
  assert.ok(bad['amount-rent']);
  assert.ok(bad['due-rent']);
});

test('forecasts waiting for confirmation are read from the review', () => {
  const f = loadFinance();
  assert.equal(f.pendingForecasts({checks: [{key: 'sales_available', ok: true}, {key: 'forecasts_pending', ok: false, count: 3}]}), 3);
  assert.equal(f.pendingForecasts(null), 0);
});

test('the page offers the setup only when the month has no expense and nothing forecast', () => {
  const finance = read('web/assets/finance.js');
  const page = finance.slice(finance.indexOf('async function renderFinanceiro'), finance.indexOf('/* ---------------------------------------------------------------- Custos e Despesas'));
  assert.match(page, /hasNoExpenses\(result\) && !forecasts/);
  assert.match(page, /fixedSetupHtml\(/);
  assert.match(page, /costQualityNote\(result\)/);
  assert.match(page, /filtro: 'custo-zero'/);
  assert.doesNotMatch(page, /style="/);
});

test('closing reminders link to their month in Resultado gerencial and are requested at login', () => {
  const context = vm.createContext({
    console, URLSearchParams, APP: {company: 1},
    ALERT_FILTERS: {estoque: 'ruptura', preco: 'abaixo-custo', custo: 'sem-custo'},
    routeHash: (page, period, params) => `#/${page}${period ? '/' + period : ''}${params && params.toString() ? '?' + params.toString() : ''}`,
    esc: (s) => String(s == null ? '' : s), num: String, money: String, pct: String, icon: () => '',
  });
  vm.runInContext(read('web/assets/insights.js').match(/function sentenceCase\([\s\S]*?\n}/)[0], context);
  vm.runInContext(read('web/assets/actions.js'), context);
  assert.equal(context.actionOrigin('fechamento:2026-08'), 'fechamento');
  const source = context.actionSource({alert_key: 'fechamento:2026-08'}, '2026-09');
  assert.equal(source.href, '#/financeiro/2026-08');
  assert.match(read('web/assets/actions.js'), /actions\/closing-reminder/);
  assert.match(read('web/assets/app.js'), /ensureClosingReminder\(\)/);
  assert.equal(context.actionOrigin('vencimento:entry:9'), 'vencimento');
  assert.equal(context.actionSource({alert_key: 'vencimento:entry:9'}, '2026-09').href, '#/contas-pagar');
  assert.match(read('web/assets/actions.js'), /actions\/due-reminders/);
});

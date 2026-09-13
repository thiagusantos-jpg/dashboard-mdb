const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('finance navigation contains result expenses and accounts payable pages', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');

  for (const page of ['financeiro', 'despesas', 'contas-pagar']) {
    assert.match(html, new RegExp(`data-page="${page}"`));
    assert.match(app, new RegExp(`['"]${page}['"]`));
  }
  assert.match(html, /assets\/finance\.js/);
});

test('management result labels do not mix owner compensation and distributions', () => {
  const source = fs.readFileSync(path.join(root, 'web/assets/finance.js'), 'utf8');

  assert.match(source, /Pró-labore/);
  assert.match(source, /Distribuição de lucros/);
  assert.match(source, /Resultado gerencial/);
  assert.match(source, /Orçado/);
  assert.match(source, /Realizado/);
});


/* Task C4: the legacy fixed-cost simulator is gone; the management result is
 * the one source, with its data status stated instead of implied. */
const vm = require('node:vm');

function loadFinance() {
  const context = vm.createContext({
    console, APP: {company: '7', financePeriod: '2026-09'}, MONTHS: ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'],
    esc: (s) => String(s == null ? '' : s), icon: () => '', money: (c) => (c == null ? 'Indisponível' : `R$ ${c / 100}`),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'web/assets/finance.js'), 'utf8'), context);
  return context;
}

test('no fixed-cost simulator or simulated result remains in the client', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const insights = fs.readFileSync(path.join(root, 'web/assets/insights.js'), 'utf8');

  // The break-even point came back fed by the cost center (test_break_even_frontend.cjs);
  // the manual fixed cost and the simulated result stay gone.
  assert.doesNotMatch(html, /custo-fixo|Simulador/);
  assert.doesNotMatch(app, /updateCustoFixo|custo-fixo|\/config['`]|fixed_cost_cents|simulated_net|Resultado simulado/);
  assert.doesNotMatch(insights, /fixed_cost_cents|lucro líquido/i);
});

test('the Resumo card and the Financeiro page read the same management result', () => {
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const finance = fs.readFileSync(path.join(root, 'web/assets/finance.js'), 'utf8');
  const {managementResultUrl} = loadFinance();

  assert.equal(managementResultUrl('2026-09'), '/api/companies/7/finance/management-result?period=2026-09');
  assert.match(app, /managementResultUrl\(/);
  assert.match(finance, /managementResultUrl\(/);
});

test('data status is spelled out: missing sales, restricted access, and Em apuração', () => {
  const {managementStatus} = loadFinance();

  const noSales = managementStatus({data_status: {sales_available: false, expenses_reviewed: false, restricted: false, reason: 'Vendas de 2026-10 não sincronizadas.'}});
  assert.equal(noSales.label, 'Em apuração');
  assert.match(noSales.detail, /Vendas de 2026-10 não sincronizadas/);

  const restricted = managementStatus({data_status: {sales_available: true, expenses_reviewed: false, restricted: true, reason: ''}});
  assert.match(restricted.detail, /restrit/i);

  const complete = managementStatus({data_status: {sales_available: true, expenses_reviewed: false, restricted: false, reason: ''}});
  assert.equal(complete.label, 'Em apuração');
  assert.doesNotMatch(complete.detail, /seguro|confiável/i);
});

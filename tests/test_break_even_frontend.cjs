/* Break-even point back in the interface, fed by the cost center instead of a
 * manually typed fixed cost. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

function load(files) {
  const context = vm.createContext({
    console, APP: {company: '1', financePeriod: '2026-09'},
    MONTHS: ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'],
    esc: (s) => String(s == null ? '' : s), icon: () => '', dt: (s) => s,
    // insights.js's kpi() card, reduced to the text it prints.
    kpi: (title, value, subtitle) => `<div class="kpi-card">${title} ${value} ${subtitle || ''}</div>`,
    money: (c) => (c == null ? 'Indisponível' : `R$ ${(c / 100).toFixed(2)}`),
    crypto: {randomUUID: () => 'k'},
    document: {getElementById: () => null, querySelectorAll: () => [], createElement: () => ({})},
    api: async () => ({}),
  });
  files.forEach((file) => vm.runInContext(read(file), context));
  return context;
}

test('the break-even section shows value, gap, costs and contribution margin', () => {
  const f = load(['web/assets/finance.js']);
  const html = f.breakEvenHtml({break_even: {
    fixed_costs_cents: 20000, variable_costs_cents: 10000, contribution_margin_cents: 30000,
    contribution_margin_pct: 30, break_even_cents: 66667, gap_pct: 50, reason: '',
  }, revenue_cents: 100000});
  assert.match(html, /Ponto de equilíbrio/);
  assert.match(html, /R\$ 666\.67/);
  assert.match(html, /R\$ 200\.00/);
  assert.match(html, /R\$ 100\.00/);
  assert.match(html, /30,0%/);
  assert.match(html, /acima/);
});

test('an unavailable break-even says why instead of showing a number', () => {
  const f = load(['web/assets/finance.js']);
  const html = f.breakEvenHtml({break_even: {
    fixed_costs_cents: 0, variable_costs_cents: 0, contribution_margin_cents: null, contribution_margin_pct: null,
    break_even_cents: null, gap_pct: null, reason: 'Nenhuma despesa fixa lançada nesta competência: lance os custos fixos.',
  }});
  assert.match(html, /Indisponível/);
  assert.match(html, /lance os custos fixos/);
});

test('each expense category can be switched between fixed and variable by version', () => {
  const f = load(['web/assets/loans.js', 'web/assets/finance-forms.js']);
  const request = f.buildCostBehaviorRequest({id: '9', version: 3}, 'variable');
  assert.equal(request.method, 'PATCH');
  assert.equal(request.path, '/api/companies/1/finance/accounts/9');
  assert.equal(JSON.stringify(request.body), JSON.stringify({expected_version: 3, cost_behavior: 'variable'}));
  assert.ok(f.buildCostBehaviorRequest({id: '9', version: 3}, 'semi').errors);
  assert.match(read('web/assets/settings.js'), /data-cost-behavior/);
});

test('Resumo and Projeções read the break-even from the management result, never a typed fixed cost', () => {
  const app = read('web/assets/app.js');
  const insights = read('web/assets/insights.js');
  assert.match(app, /Ponto de equilíbrio/);
  assert.match(app, /break_even_cents/);
  assert.match(insights, /managementResultUrl\(/);
  assert.match(insights, /mountEchartGauge\(/);
  assert.doesNotMatch(app + insights, /fixed_cost_cents|custo-fixo/);
});

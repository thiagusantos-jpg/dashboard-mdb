/* Resultado gerencial com hierarquia: o resultado primeiro, a cascata do faturamento
 * ao resultado, um aviso quando nenhuma despesa foi lançada e a revisão compacta. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

function loadFinance() {
  const context = vm.createContext({
    console, APP: {company: '1', financePeriod: '2025-09'},
    MONTHS: ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'],
    esc: (s) => String(s == null ? '' : s), icon: () => '', dt: (s) => s,
    money: (c) => (c == null ? 'Indisponível' : `R$ ${(c / 100).toFixed(2)}`),
  });
  vm.runInContext(read('web/assets/finance.js'), context);
  return context;
}

// Set/2025 as the partner saw it: sales synced, no expense launched.
const SEPT = {
  revenue_cents: 8001750, cogs_cents: 6858830, gross_profit_cents: 1142920,
  operating_expenses_cents: 0, owner_compensation_cents: 0, operating_result_cents: 1142920,
  financial_expenses_cents: 0, managerial_result_cents: 1142920, profit_distribution_cents: 0,
  accounts: [], data_status: {sales_available: true, expenses_reviewed: false, restricted: false, reason: ''},
};

test('the cascade goes from revenue to the managerial result with each share of revenue', () => {
  const f = loadFinance();
  const rows = Array.from(f.dreRows(SEPT));
  assert.deepEqual(rows.map((r) => r.label), ['Receita', 'CMV', 'Lucro bruto', 'Despesas operacionais', 'Pró-labore',
    'Resultado operacional', 'Despesas financeiras', 'Resultado gerencial']);
  assert.deepEqual(rows.map((r) => r.kind), ['base', 'minus', 'subtotal', 'minus', 'minus', 'subtotal', 'minus', 'total']);
  assert.equal(rows[0].pct, 100);
  assert.equal(rows[1].pct, 85.7);
  assert.equal(rows[7].pct, 14.3);
  assert.equal(rows[3].empty, true, 'a zero expense line reads as "sem lançamentos", not as a real zero');
  assert.equal(rows[1].empty, false);

  const unavailable = Array.from(f.dreRows({...SEPT, revenue_cents: null, cogs_cents: null, gross_profit_cents: null,
    operating_result_cents: null, managerial_result_cents: null}));
  assert.equal(unavailable[0].pct, null);
  assert.equal(unavailable[7].cents, null);
});

test('the headline states the result, its margin and its tone', () => {
  const f = loadFinance();
  const good = f.resultHeadline(SEPT);
  assert.equal(good.tone, 'positive');
  assert.equal(good.marginPct, 14.3);
  assert.equal(f.resultHeadline({...SEPT, managerial_result_cents: -50000}).tone, 'negative');
  const none = f.resultHeadline({...SEPT, managerial_result_cents: null});
  assert.equal(none.tone, 'unavailable');
  assert.equal(none.marginPct, null);
});

test('a month without any expense launched is called out, a restricted one is not', () => {
  const f = loadFinance();
  assert.equal(f.hasNoExpenses(SEPT), true);
  assert.equal(f.hasNoExpenses({...SEPT, accounts: [{nature: 'operating_expense', actual_cents: 350000}]}), false);
  assert.equal(f.hasNoExpenses({...SEPT, accounts: [{nature: 'operating_expense', actual_cents: 0, budget_cents: 350000}]}), true);
  assert.equal(f.hasNoExpenses({...SEPT, data_status: {...SEPT.data_status, restricted: true}}), false);
  assert.equal(f.hasNoExpenses({...SEPT, revenue_cents: null}), false, 'without sales the result is already unavailable');
});

test('the review panel summarizes its checks and opens them only when something needs attention', () => {
  const f = loadFinance();
  const allOk = {status: 'in_progress', revision: 'a', checks: [
    {label: 'Vendas do mês sincronizadas do Mobne', ok: true}, {label: 'Conciliações com divergência', ok: true, count: 0}]};
  assert.deepEqual({...f.reviewSummary(allOk)}, {total: 2, pending: 0});
  assert.match(f.reviewPanelHtml(allOk), /Todas as 2 conferências automáticas estão OK/);
  assert.doesNotMatch(f.reviewPanelHtml(allOk), /<details[^>]*open/);

  const pending = {...allOk, checks: [...allOk.checks, {label: 'Recorrências previstas por confirmar', ok: false, count: 2}]};
  assert.match(f.reviewPanelHtml(pending), /1 ponto para conferir/);
  assert.match(f.reviewPanelHtml(pending), /<details[^>]*open/);
});

test('the page leads with the result, keeps the break-even and says the status once', () => {
  const finance = read('web/assets/finance.js');
  const page = finance.slice(finance.indexOf('async function renderFinanceiro'), finance.indexOf('/* ---------------------------------------------------------------- Custos e Despesas'));
  assert.doesNotMatch(page, /periodo-badge/, 'the competence is already in the top bar');
  assert.equal((page.match(/managementStatus\(result\)/g) || []).length, 1);
  assert.ok(page.indexOf('resultHeroHtml(') < page.indexOf('reviewPanelHtml('), 'numbers before the review checklist');
  assert.ok(page.indexOf('dreHtml(') < page.indexOf('reviewPanelHtml('));
  assert.match(page, /breakEvenHtml\(result\)/);
  assert.match(finance, /Nenhuma despesa lançada/);
  assert.match(finance, /Distribuição de lucros/);
  assert.doesNotMatch(page, /kpi\('Despesas operacionais'/, 'the nine equal cards are gone');
});

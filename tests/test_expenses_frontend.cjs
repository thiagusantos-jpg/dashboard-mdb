/* Despesas do mês: para onde o dinheiro foi na competência, agrupado por categoria,
 * comparado com o mês anterior e com a conta vencida em destaque. */
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
    EXPENSE_NATURES: ['operating_expense', 'financial_expense', 'tax_expense'],
    routeHash: (page, period) => `#/${page}${period ? `/${period}` : ''}`,
    statusBadge: (s) => `<span>${s}</span>`,
  });
  vm.runInContext(read('web/assets/finance.js'), context);
  return context;
}

const ACCOUNTS = {'1': {id: '1', name: 'Aluguel'}, '2': {id: '2', name: 'Energia'}};
const ENTRIES = [
  {id: 'a', account_id: '1', description: 'Requinte Imobiliária', amount_cents: 800000, due_date: '2026-09-10', status: 'open'},
  {id: 'b', account_id: '2', description: 'Enel', amount_cents: 120000, due_date: '2026-09-20', status: 'paid'},
  {id: 'c', account_id: '2', description: 'Enel previsto', amount_cents: 99000, due_date: '2026-09-25', status: 'forecast'},
  {id: 'd', account_id: '2', description: 'Enel cancelado', amount_cents: 50000, due_date: '2026-09-25', status: 'cancelled'},
];

test('spending is grouped by category, biggest first, with its share of the total', () => {
  const f = loadFinance();
  const groups = f.expenseGroups(ENTRIES, ACCOUNTS);

  // Array.from: o array nasce dentro do vm e deepEqual não cruza realms.
  assert.deepEqual(Array.from(groups.map((g) => g.name)), ['Aluguel', 'Energia']);
  assert.equal(groups[0].cents, 800000);
  // Previsto e cancelado aparecem na lista, mas não somam: não são dinheiro gasto.
  assert.equal(groups[1].cents, 120000);
  assert.equal(groups[1].items.length, 3);
  assert.equal(groups[0].share, 87);
  assert.equal(groups[1].share, 13);
});

test('an expense with no category still shows up instead of vanishing', () => {
  const f = loadFinance();
  const groups = f.expenseGroups([{id: 'x', account_id: null, description: 'Solta', amount_cents: 1000, due_date: '2026-09-01', status: 'open'}], {});
  assert.equal(groups[0].name, 'Sem categoria');
  assert.equal(groups[0].cents, 1000);
});

test('totals separate money spent from what is only forecast', () => {
  const f = loadFinance();
  const totals = f.expenseTotals(ENTRIES);
  assert.equal(totals.realized_cents, 920000);
  assert.equal(totals.forecast_cents, 99000);
  assert.equal(totals.forecast_count, 1);
});

test('the comparison reads spending up as bad and down as good', () => {
  const f = loadFinance();
  assert.equal(f.expenseDelta(920000, 800000).tone, 'bad');
  assert.match(f.expenseDelta(920000, 800000).text, /\+15% vs mês anterior/);
  assert.equal(f.expenseDelta(700000, 800000).tone, 'good');
  assert.match(f.expenseDelta(700000, 800000).text, /−12,5% vs mês anterior/);
  assert.equal(f.expenseDelta(800000, 800000).text, 'igual ao mês anterior');
  assert.equal(f.expenseDelta(800000, 0).text, 'sem despesas no mês anterior');
  // Sem mês anterior carregado, nada é afirmado.
  assert.equal(f.expenseDelta(800000, null), null);
});

test('the cards answer where the money went, naming the biggest category', () => {
  const f = loadFinance();
  const html = f.expensesCardsHtml(f.expenseTotals(ENTRIES), f.expenseDelta(920000, 800000), f.expenseGroups(ENTRIES, ACCOUNTS));
  assert.match(html, /Maior categoria/);
  assert.match(html, /Aluguel/);
  assert.match(html, /87% do total/);
  assert.match(html, /alert/);
  assert.doesNotMatch(html, /style="/);
});

test('the due date is shown to check the entry, never to raise an alarm', () => {
  const f = loadFinance();
  // ENTRIES[0] venceu em 10/09 e segue em aberto. Cobrar isso é trabalho de Contas a
  // pagar: duas telas alarmando a mesma conta é o que fazia as duas parecerem iguais.
  const row = f.expenseRowHtml(ENTRIES[0], ACCOUNTS);
  assert.match(row, /10\/09\/2026/);
  assert.doesNotMatch(row, /venceu/);
  assert.doesNotMatch(row, /cell-late/);
});

test('the page says out loud what belongs to the other screen', () => {
  const f = loadFinance();
  const notice = f.unpaidNotice(ENTRIES);
  // Só as duas em aberto; previsto, pago e cancelado não são conta a pagar.
  assert.equal(notice.count, 1);
  assert.equal(notice.cents, 800000);
  const html = f.unpaidNoticeHtml(notice);
  assert.match(html, /Uma destas despesas ainda não foi paga/);
  assert.match(html, /Abrir Contas a pagar/);
  assert.doesNotMatch(html, /style="/);

  const many = f.unpaidNotice([ENTRIES[0], {id: 'z', account_id: '2', description: 'Outra', amount_cents: 1000, due_date: '2026-09-11', status: 'overdue'}]);
  assert.match(f.unpaidNoticeHtml(many), /2 destas despesas ainda não foram pagas/);
  // Nada em aberto: nenhuma faixa, nenhum ruído.
  assert.equal(f.unpaidNotice([ENTRIES[1]]), null);
  assert.equal(f.unpaidNoticeHtml(null), '');
});

test('each category header carries its own subtotal', () => {
  const f = loadFinance();
  const html = f.expenseGroupsHtml(f.expenseGroups(ENTRIES, ACCOUNTS), ACCOUNTS);
  assert.match(html, /Aluguel[\s\S]*1 lançamento · R\$ 8000\.00/);
  assert.match(html, /Energia[\s\S]*3 lançamentos · R\$ 1200\.00/);
  assert.doesNotMatch(html, /style="/);
});

test('the page compares with the previous month without depending on it', () => {
  const finance = read('web/assets/finance.js');
  const page = finance.slice(finance.indexOf('async function renderDespesas'));
  assert.match(page, /monthShift\(financeCompetence\(\), -1\)\}\`\)\.catch\(\(\) => null\)/);
  assert.match(page, /expensesCardsHtml\(totals, delta/);
  assert.match(page, /expenseGroupsHtml\(expenseGroups\(visible/);
  assert.match(page, /despesas-q/);
  assert.match(page, /unpaidNoticeHtml\(unpaidNotice\(expenseEntries\)\)/);
  // O selo de competência repetia o seletor do cabeçalho: saiu.
  assert.doesNotMatch(page.slice(0, page.indexOf('const refresh')), /periodo-badge/);
});

/* Taxas e mensalidade da Stone vêm do relatório: não pedem conferência uma a uma. */
const stoneFee = (id, day) => ({id, account_id: '9', description: `Taxas Stone de ${day}/09`, amount_cents: 3000,
  due_date: `2026-09-${day}`, status: 'overdue', source: 'stone_receivable'});

test('Stone automatic expenses are tagged, never "a pagar", and only offer history', () => {
  const f = loadFinance();
  const html = f.expenseRowHtml(stoneFee('s1', '10'), {'9': {name: 'Taxas de adquirentes'}});
  assert.match(html, /Automático · Stone/);
  assert.match(html, /Descontado pela Stone/);
  assert.doesNotMatch(html, /overdue/);
  assert.match(html, /data-entry-action="history"/);
  assert.doesNotMatch(html, /data-entry-action="edit"/);
  assert.equal(f.unpaidNotice([stoneFee('s1', '10'), stoneFee('s2', '11')]), null);
  assert.equal(f.unpaidNotice([stoneFee('s1', '10'), ENTRIES[0]]).count, 1);
});

test('a category made only of Stone daily fees folds into one line', () => {
  const f = loadFinance();
  const fees = ['01', '02', '03', '04'].map((d) => stoneFee(`s${d}`, d));
  const [group] = f.expenseGroups(fees, {'9': {name: 'Taxas de adquirentes'}});
  const html = f.expenseGroupRowsHtml(group, {'9': {name: 'Taxas de adquirentes'}});
  assert.match(html, /4 lançamentos vindos do relatório de recebíveis, já conferidos na importação/);
  assert.equal((html.match(/data-stone-group="9" hidden/g) || []).length, 4);
  const mixed = f.expenseGroups([...fees, Object.assign({}, ENTRIES[1], {account_id: '9'})], {})[0];
  assert.doesNotMatch(f.expenseGroupRowsHtml(mixed, {}), /stone-auto-summary/, 'a manual entry keeps the list open');
});

test('the fixed expense setup shows the terminal fee as automatic when the report covers it', () => {
  const f = loadFinance();
  const html = f.fixedSetupRowsHtml([
    {system_key: 'payment_terminal_rent', label: 'Aluguel da maquininha', stone_automatic: true, configured: false},
    {system_key: 'rent', label: 'Aluguel', stone_automatic: false, configured: false},
  ], '2026-09');
  assert.match(html, /Automática pelo relatório da Stone/);
  assert.doesNotMatch(html, /amount-payment_terminal_rent/);
  assert.match(html, /amount-rent/);
});

/* Projeção de 30 dias somando os recebíveis da Stone (item 7) e baixa sugerida a partir
 * de um débito que já está no caixa (item 8). */
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
    // dateBR não entra aqui: finance.js declara a sua própria (dd/mm/aaaa).
    esc: (s) => String(s == null ? '' : s), icon: () => '', dt: (s) => s,
    money: (c) => (c == null ? 'Indisponível' : `R$ ${(c / 100).toFixed(2)}`),
  });
  vm.runInContext(read('web/assets/finance.js'), context);
  return context;
}

const PROJECTION = {
  start: '2026-09-15',
  end: '2026-09-17',
  days: [
    {date: '2026-09-15', items: [], balance_cents: 100_000},
    {date: '2026-09-16', items: [{amount_cents: -80_000, source: 'financial_entries'}], balance_cents: 20_000},
    {date: '2026-09-17', items: [
      {amount_cents: 50_000, source: 'stone_receivables'},
      {amount_cents: -1_000, source: 'financial_entries'},
    ], balance_cents: 69_000},
  ],
  lowest: {date: '2026-09-16', balance_cents: 20_000},
};

test('the outlook separates what comes in from what goes out and keeps the worst day', () => {
  const f = loadFinance();
  const outlook = f.cashOutlook(PROJECTION);
  // O horizonte pedido (15 a 17), não as três linhas de dia que a projeção devolveu.
  assert.equal(outlook.horizon_days, 2);
  assert.equal(outlook.incoming_cents, 50_000);
  assert.equal(outlook.outgoing_cents, -81_000);
  assert.equal(outlook.final_cents, 69_000);
  assert.equal(outlook.hasStone, true);
  assert.equal(outlook.negative, false);
  assert.equal(outlook.lowest.date, '2026-09-16');
});

test('an empty projection produces no panel at all', () => {
  const f = loadFinance();
  assert.equal(f.cashOutlook(null), null);
  assert.equal(f.cashOutlook({days: []}), null);
  assert.equal(f.outlookHtml(null), '');
});

test('the panel says on which day the cash runs out, not only the month-end balance', () => {
  const f = loadFinance();
  const broke = f.cashOutlook({
    days: [{date: '2026-09-20', items: [{amount_cents: -200_000, source: 'financial_entries'}], balance_cents: -50_000}],
    lowest: {date: '2026-09-20', balance_cents: -50_000},
  });
  assert.equal(broke.negative, true);
  const html = f.outlookHtml(broke);
  assert.match(html, /fica negativo em 20\/09\/2026/);
  assert.doesNotMatch(html, /style="/);
});

test('without Stone in the window the panel says so instead of implying zero', () => {
  const f = loadFinance();
  const html = f.outlookHtml(f.cashOutlook({
    days: [{date: '2026-09-16', items: [{amount_cents: -1_000, source: 'financial_entries'}], balance_cents: 1_000}],
    lowest: {date: '2026-09-16', balance_cents: 1_000},
  }));
  assert.match(html, /Sem recebíveis de cartão no período/);
});

const SUGGESTION = {
  obligation: {kind: 'entry', id: '9', version: 1, description: 'Aluguel', due_date: '2026-09-18',
    total_cents: 80_000, paid_cents: 0, open_cents: 80_000, status: 'overdue'},
  cash_event: {id: '55', cash_account_id: '3', occurred_at: '2026-09-19',
    description: 'Pagamento de boleto', amount_cents: -80_000},
};

test('a suggested write-off names the bill, the debit and offers the button', () => {
  const f = loadFinance();
  const html = f.suggestionsHtml([SUGGESTION]);
  assert.match(html, /Aluguel/);
  assert.match(html, /saída de R\$ 800\.00 em 19\/09\/2026/);
  assert.match(html, /data-suggestion-pay="9"/);
  assert.match(html, /Dar baixa/);
  // O texto precisa deixar claro que nada é quitado sozinho.
  assert.match(html, /nada é quitado sem você confirmar/);
  assert.doesNotMatch(html, /style="/);
});

test('no suggestions means no panel', () => {
  const f = loadFinance();
  assert.equal(f.suggestionsHtml([]), '');
  assert.equal(f.suggestionsHtml(null), '');
});

test('the page asks for both extras without letting either take it down', () => {
  const finance = read('web/assets/finance.js');
  assert.match(finance, /obligations\/payment-suggestions`\)\.catch\(\(\) => null\)/);
  assert.match(finance, /finance\/forecast\?start=\$\{horizonStart\}&end=\$\{isoAddDays\(horizonStart, 30\)\}/);
  assert.match(finance, /\$\{suggestionsHtml\(suggestions\)\}/);
  assert.match(finance, /\$\{outlookHtml\(cashOutlook\(projection\)\)\}/);
});

test('the write-off opens the usual payment drawer, already linked to the movement', () => {
  const finance = read('web/assets/finance.js');
  assert.match(finance, /data-suggestion-pay/);
  assert.match(finance, /linkCashEvent: item\.cash_event/);
  // A gaveta escolhe o modo "vincular" e o movimento sugerido.
  const forms = read('web/assets/finance-forms.js');
  assert.match(forms, /ctx\.linkCashEvent/);
  assert.match(forms, /input\[name="cash_mode"\]\[value="link"\]/);
});

/* Bloco 2 de Contas a pagar no frontend: juros e multa no pagamento (item 4),
 * como a conta é paga (item 5) e repetir o último lançamento do credor (item 6). */
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

const BILL = {kind: 'entry', id: '1', version: 2, open_cents: 60000, description: 'Aluguel', due_date: '2026-09-10'};
const BASE = {amount: '600,00', paid_at: '2026-09-20', cash_mode: 'generate', cash_account_id: '7'};

test('a late fee rides along with the payment, and only when it is filled', () => {
  const f = loadFinance();
  const withFee = f.buildPaymentRequest(BILL, {...BASE, late_fee: '12,34'});
  assert.equal(withFee.body.late_fee_cents, 1234);

  const withoutFee = f.buildPaymentRequest(BILL, {...BASE, late_fee: ''});
  assert.equal('late_fee_cents' in withoutFee.body, false);

  const broken = f.buildPaymentRequest(BILL, {...BASE, late_fee: 'abc'});
  assert.ok(broken.errors.late_fee);
});

test('the drawer says what actually leaves the account', () => {
  const f = loadFinance();
  assert.equal(f.paymentTotalCents(BILL, {...BASE, late_fee: '12,34'}), 61234);
  assert.equal(f.paymentTotalCents(BILL, {...BASE, late_fee: ''}), 60000);
});

test('an expense carries how it is paid', () => {
  const f = loadFinance();
  const candidate = f.entryCandidate({
    description: 'Aluguel', amount: '3.500,00', competence: '2026-09', due_date: '2026-09-10',
    payment_method: 'boleto', payment_code: '  34191790010104351004791020150008291070026000  ',
  });
  assert.equal(candidate.payment_method, 'boleto');
  assert.equal(candidate.payment_code, '34191790010104351004791020150008291070026000');
});

test('a bill on direct debit is left out of the batch payment', () => {
  const f = loadFinance();
  const items = [
    {key: 'entry:1', kind: 'entry', allowed_actions: ['details', 'pay'], payment_method: 'boleto', open_cents: 100},
    {key: 'entry:2', kind: 'entry', allowed_actions: ['details', 'pay'], payment_method: 'debito_automatico', open_cents: 100},
  ];
  assert.deepEqual(Array.from(f.selectableObligations(items), (item) => item.key), ['entry:1']);
  // The row must follow the same rule, or it would offer a checkbox the batch ignores.
  const finance = read('web/assets/finance.js');
  assert.match(finance, /selectableObligations\(\[item\]\)/);
  assert.match(finance, /Débito automático/);
});

test('the supplier suggestion only fills what the partner left empty', () => {
  const f = loadFinance();
  const suggestion = {account_id: '9', amount_cents: 350000, payment_method: 'boleto', competence: '2026-08', description: 'Aluguel'};

  const filled = f.applySuggestion({account_id: '', amount: '', payment_method: '', description: 'Aluguel de setembro'}, suggestion);
  assert.equal(filled.values.account_id, '9');
  assert.equal(filled.values.amount, f.centsToMoneyInput(350000));
  assert.equal(filled.values.payment_method, 'boleto');
  assert.equal(filled.values.description, 'Aluguel de setembro', 'o que foi digitado nunca é sobrescrito');
  assert.deepEqual(Array.from(filled.filled).sort(), ['account_id', 'amount', 'payment_method']);

  const untouched = f.applySuggestion({account_id: '5', amount: '10,00', payment_method: 'pix'}, suggestion);
  assert.deepEqual(Array.from(untouched.filled), []);
});

test('the forms wire the late fee, the method and the supplier suggestion', () => {
  const forms = read('web/assets/finance-forms.js');
  assert.match(forms, /late_fee_cents/);
  assert.match(forms, /data-late-fee/);
  assert.match(forms, /payment_method/);
  assert.match(forms, /last-expense/);
  assert.match(forms, /data-suggestion-clear/);
});

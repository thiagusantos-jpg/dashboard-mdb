/* Shared financial forms (task C2). Same style as the other frontend tests:
 * browser sources run in a vm context with hand-made stubs; api() is a spy,
 * so assertions are on the requests actually sent, not on source text. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

function load({apiImpl} = {}) {
  const calls = [];
  const created = [];
  const context = vm.createContext({
    console,
    APP: {company: '1', period: '2026-09', csrf: 'x'},
    crypto: {randomUUID: (() => { let n = 0; return () => `key-${++n}`; })()},
    esc: (s) => String(s == null ? '' : s),
    money: (c) => `R$ ${(c / 100).toFixed(2)}`,
    dateBR: (iso) => iso,
    document: {
      body: {appendChild() {}},
      activeElement: null,
      createElement(tag) {
        const listeners = {};
        const el = {
          tag, innerHTML: '', open: false, attributes: {}, className: '',
          setAttribute(k, v) { this.attributes[k] = String(v); },
          addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
          querySelector: () => null, querySelectorAll: () => [],
          showModal() { this.open = true; },
          close() { this.open = false; (listeners.close || []).forEach((fn) => fn()); },
          remove() { this.removed = true; },
        };
        created.push(el);
        return el;
      },
      getElementById: () => null,
      querySelectorAll: () => [],
    },
    api: async (url, opts) => {
      calls.push({url, opts: opts || {}});
      return apiImpl ? apiImpl(url, opts || {}, calls.length) : {};
    },
  });
  vm.runInContext(read('web/assets/loans.js'), context);
  vm.runInContext(read('web/assets/finance-forms.js'), context);
  return {context, calls, created};
}

function fakeUi() {
  const ui = {busy: [], errors: [], successes: 0};
  ui.setBusy = (b) => ui.busy.push(b);
  ui.showError = (message, fields) => ui.errors.push({message, fields});
  ui.clearError = () => {};
  return ui;
}

test('pt-BR money input converts to integer cents without float drift', () => {
  const {context} = load();
  assert.equal(context.parseMoneyToCents('1.234,56'), 123456);
  assert.equal(context.parseMoneyToCents('0,10'), 10);
  assert.equal(context.parseMoneyToCents('0,29'), 29);
  assert.equal(context.parseMoneyToCents('1234.5'), 123450);
  assert.equal(context.parseMoneyToCents('R$ 600'), 60000);
  assert.equal(context.parseMoneyToCents('12,345'), null);
  assert.equal(context.parseMoneyToCents('abc'), null);
  assert.equal(context.parseMoneyToCents(''), null);
  assert.equal(context.centsToMoneyInput(123456), '1.234,56');
  assert.equal(context.centsToMoneyInput(60000), '600,00');
});

test('a partially paid obligation pre-fills the open balance, not the total', () => {
  const {context} = load();
  const obligation = {kind: 'entry', id: '123', version: 2, total_cents: 100000, paid_cents: 40000, open_cents: 60000};
  assert.equal(context.paymentDefaults(obligation, '2026-09-13').amount, '600,00');
  assert.equal(context.paymentDefaults(obligation, '2026-09-13').paid_at, '2026-09-13');
});

test('payment request requires choosing how cash moves and keeps ids as strings', () => {
  const {context} = load();
  const obligation = {kind: 'entry', id: '9007199254740993', version: 2, open_cents: 60000};
  const missing = context.buildPaymentRequest(obligation, {amount: '600,00', paid_at: '2026-09-13', cash_mode: 'generate', cash_account_id: ''});
  assert.deepEqual(Object.keys(missing.errors), ['cash_account_id']);

  const request = context.buildPaymentRequest(obligation, {
    amount: '600,00', paid_at: '2026-09-13', cash_mode: 'link', existing_cash_event_id: '9007199254740995',
  });
  assert.equal(request.path, '/api/companies/1/finance/obligations/entry/9007199254740993/payments');
  assert.equal(request.body.amount_cents, 60000);
  assert.equal(request.body.expected_version, 2);
  assert.equal(request.body.existing_cash_event_id, '9007199254740995');
  assert.equal('cash_account_id' in request.body, false);

  const tooMuch = context.buildPaymentRequest(obligation, {amount: '600,01', paid_at: '2026-09-13', cash_mode: 'generate', cash_account_id: '5'});
  assert.deepEqual(Object.keys(tooMuch.errors), ['amount']);
});

test('loan installment payment splits principal and interest that add up to the amount', () => {
  const {context} = load();
  const installment = {kind: 'loan_installment', id: '77', version: 1, open_cents: 105000};
  const wrong = context.buildPaymentRequest(installment, {
    amount: '1.050,00', paid_at: '2026-09-13', cash_mode: 'generate', cash_account_id: '5', principal: '1.000,00', interest: '40,00',
  });
  assert.deepEqual(Object.keys(wrong.errors), ['interest']);
  const right = context.buildPaymentRequest(installment, {
    amount: '1.050,00', paid_at: '2026-09-13', cash_mode: 'generate', cash_account_id: '5', principal: '1.000,00', interest: '50,00',
  });
  assert.equal(right.body.principal_cents, 100000);
  assert.equal(right.body.interest_cents, 5000);
});

test('422 keeps the typed values, re-enables submit, and a double click sends one POST', async () => {
  let reject;
  const {context, calls} = load({apiImpl: () => new Promise((_, r) => { reject = r; })});
  const obligation = {kind: 'entry', id: '123', version: 2, open_cents: 60000};
  const values = {amount: '600,00', paid_at: '2026-09-13', cash_mode: 'generate', cash_account_id: '5'};
  const ui = fakeUi();
  let rendered = 0;
  const controller = context.createFormController({
    buildRequest: (v) => context.buildPaymentRequest(obligation, v),
    onSuccess: () => { rendered++; },
  });

  const first = controller.submit(values, ui);
  const second = controller.submit(values, ui);
  const error = new Error('Valor maior que o saldo.');
  error.status = 422;
  error.fields = ['amount_cents'];
  reject(error);
  const [result] = await Promise.all([first, second]);

  assert.equal(calls.length, 1);
  assert.equal(JSON.parse(calls[0].opts.body).amount_cents, 60000);
  assert.equal(calls[0].opts.headers['Idempotency-Key'], 'key-1');
  assert.equal(result.ok, false);
  assert.equal(values.amount, '600,00');
  assert.equal(rendered, 0);
  assert.deepEqual(ui.busy, [true, false]);
  assert.equal(ui.errors[0].message, 'Valor maior que o saldo.');
  assert.deepEqual([...ui.errors[0].fields], ['amount']);
});

test('a network failure retries with the same idempotency key; a validation error gets a fresh one', async () => {
  let attempt = 0;
  const {context, calls} = load({apiImpl: () => {
    attempt++;
    if (attempt === 1) throw new TypeError('Failed to fetch');
    if (attempt === 2) { const e = new Error('Dados inválidos'); e.status = 422; throw e; }
    return {payment_id: '1'};
  }});
  const obligation = {kind: 'entry', id: '123', version: 2, open_cents: 60000};
  const values = {amount: '600,00', paid_at: '2026-09-13', cash_mode: 'generate', cash_account_id: '5'};
  const controller = context.createFormController({buildRequest: (v) => context.buildPaymentRequest(obligation, v)});

  await controller.submit(values, fakeUi());
  await controller.submit(values, fakeUi());
  await controller.submit(values, fakeUi());

  const keys = calls.map((c) => c.opts.headers['Idempotency-Key']);
  assert.equal(keys[0], keys[1]);
  assert.notEqual(keys[1], keys[2]);
});

test('local validation errors never reach the server', async () => {
  const {context, calls} = load();
  const obligation = {kind: 'entry', id: '123', version: 2, open_cents: 60000};
  const ui = fakeUi();
  const controller = context.createFormController({buildRequest: (v) => context.buildPaymentRequest(obligation, v)});

  const result = await controller.submit({amount: '', paid_at: '2026-09-13', cash_mode: 'generate', cash_account_id: '5'}, ui);

  assert.equal(calls.length, 0);
  assert.equal(result.ok, false);
  assert.deepEqual([...ui.errors[0].fields], ['amount']);
});

test('editing sends only changed fields the entry status still allows', () => {
  const {context} = load();
  const open = {id: '10', version: 3, status: 'open', account_id: '1', counterparty_id: null, description: 'Aluguel',
    amount_cents: 100000, competence: '2026-09', due_date: '2026-09-20', notes: ''};
  const edit = context.buildExpenseRequest(open, {
    mode: 'single', account_id: '1', counterparty_id: '', description: 'Aluguel corrigido', amount: '1.000,00',
    competence: '2026-09', due_date: '2026-09-21', notes: '',
  });
  assert.equal(edit.method, 'PATCH');
  assert.equal(edit.path, '/api/companies/1/finance/entries/10');
  assert.equal(JSON.stringify(edit.body), JSON.stringify({expected_version: 3, description: 'Aluguel corrigido', due_date: '2026-09-21'}));

  const partial = Object.assign({}, open, {status: 'partially_paid'});
  const blocked = context.buildExpenseRequest(partial, {
    mode: 'single', account_id: '1', counterparty_id: '', description: 'Aluguel', amount: '900,00',
    competence: '2026-09', due_date: '2026-09-20', notes: '',
  });
  assert.deepEqual(Object.keys(blocked.errors), ['amount']);
});

test('new expense modes map to the single, recurring and installment endpoints', () => {
  const {context} = load();
  const base = {account_id: '4', counterparty_id: '', description: 'Internet', notes: ''};
  const single = context.buildExpenseRequest(null, Object.assign({mode: 'single', amount: '99,90', competence: '2026-09', due_date: '2026-09-10'}, base));
  assert.equal(single.path, '/api/companies/1/finance/entries');
  assert.equal(single.body.amount_cents, 9990);
  assert.equal(single.body.account_id, '4');

  const recurring = context.buildExpenseRequest(null, Object.assign({mode: 'recurring', amount: '99,90', start_competence: '2026-09', end_competence: '', due_day: '10'}, base));
  assert.equal(recurring.path, '/api/companies/1/finance/recurrences');
  assert.equal(recurring.body.due_day, 10);
  assert.equal('end_competence' in recurring.body, false);

  const installments = context.buildExpenseRequest(null, Object.assign({mode: 'installments', amount: '1.200,00', count: '3', first_due: '2026-09-10', competence_mode: 'single', competence: '2026-09'}, base));
  assert.equal(installments.path, '/api/companies/1/finance/expense-schedules');
  assert.equal(installments.body.total_cents, 120000);
  assert.equal(installments.body.count, 3);
  assert.equal(installments.body.confirmed, true);
});

test('cancel and reversal require a reason and name the obligation version', () => {
  const {context} = load();
  const entry = {id: '10', version: 3};
  assert.deepEqual(Object.keys(context.buildCancelRequest(entry, {reason: ' '}).errors), ['reason']);
  const cancel = context.buildCancelRequest(entry, {reason: 'Lançado em duplicidade'});
  assert.equal(cancel.path, '/api/companies/1/finance/entries/10/cancel');
  assert.equal(cancel.body.expected_version, 3);

  const obligation = {kind: 'entry', id: '10', version: 4};
  assert.deepEqual(Object.keys(context.buildReversalRequest({id: '55'}, obligation, {reason: 'ab', reversed_at: '2026-09-13'}).errors), ['reason']);
  const reversal = context.buildReversalRequest({id: '55'}, obligation, {reason: 'Valor errado', reversed_at: '2026-09-13'});
  assert.equal(reversal.path, '/api/companies/1/finance/payments/55/reverse');
  assert.equal(reversal.body.expected_version, 4);
  assert.equal(reversal.idempotent, true);
});

test('renegotiation keeps the outstanding principal and requires a reason', () => {
  const {context} = load();
  const position = {loan: {id: '8', version: 2}, principal_cents: 100000};
  const request = context.buildRenegotiationRequest(position, {count: '3', first_due: '2026-10-10', interest: '10,00', reason: 'Novo prazo'});
  const principal = request.body.installments.reduce((sum, i) => sum + i.principal_cents, 0);
  assert.equal(request.path, '/api/companies/1/finance/loans/8/renegotiate');
  assert.equal(principal, 100000);
  assert.equal(request.body.installments.length, 3);
  assert.deepEqual(Object.keys(context.buildRenegotiationRequest(position, {count: '3', first_due: '2026-10-10', interest: '0', reason: ''}).errors), ['reason']);
});

test('closing a drawer returns focus to the control that opened it', () => {
  const {context, created} = load();
  let focused = 0;
  const trigger = {focus() { focused++; }};

  const drawer = context.openDrawer({title: 'Registrar pagamento', body: '<p>x</p>', trigger});
  const dialog = created.find((el) => el.tag === 'dialog');
  assert.equal(dialog.open, true);
  assert.match(dialog.innerHTML, /<h2 id="drawer-title-\d+">Registrar pagamento<\/h2>/);
  assert.match(dialog.attributes['aria-labelledby'], /^drawer-title-\d+$/);

  drawer.close();
  assert.equal(dialog.open, false);
  assert.equal(focused, 1);
  assert.equal(dialog.removed, true);
});

test('finance pages use the shared forms, list first, and never alert()', () => {
  const html = read('web/index.html');
  const sources = ['finance.js', 'loans.js', 'cashflow.js', 'reconciliation.js'].map((f) => read(`web/assets/${f}`)).join('\n');
  assert.match(html, /assets\/finance-forms\.js/);
  assert.ok(html.indexOf('assets/finance-forms.js') < html.indexOf('assets/finance.js'));
  assert.doesNotMatch(sources, /\balert\(/);
  assert.doesNotMatch(sources, /onclick=/);
  assert.match(sources, /openExpenseForm/);
  assert.match(sources, /openPaymentForm/);
  assert.match(sources, /openLoanForm/);
  assert.match(sources, /class="data-table"/);
});

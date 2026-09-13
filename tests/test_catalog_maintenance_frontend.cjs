/* Supporting records for a single store (task C5): company data, users,
 * calendar, categories, counterparties and cash accounts. Pure builders run in
 * a vm context; requests are compared as JSON. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const json = (value) => JSON.stringify(value);

function load(files) {
  const context = vm.createContext({
    console, APP: {company: '1'},
    esc: (s) => String(s == null ? '' : s), money: (c) => `R$ ${c / 100}`, dateBR: (iso) => iso,
    crypto: {randomUUID: () => 'k'},
    document: {getElementById: () => null, querySelectorAll: () => [], createElement: () => ({})},
    api: async () => ({}),
  });
  files.forEach((file) => vm.runInContext(read(file), context));
  return context;
}

const FORMS = ['web/assets/loans.js', 'web/assets/finance-forms.js'];

test('company data masks and validates CNPJ and phone', () => {
  const s = load(['web/assets/settings.js']);
  assert.equal(s.formatCnpj('11222333000181'), '11.222.333/0001-81');
  assert.equal(s.formatCnpj('11.222.3'), '11.222.3');
  assert.equal(s.isValidCnpj('11.222.333/0001-81'), true);
  assert.equal(s.isValidCnpj('11.222.333/0001-82'), false);
  assert.equal(s.isValidCnpj('00000000000000'), false);
  assert.equal(s.formatPhone('11987654321'), '(11) 98765-4321');
  assert.equal(s.formatPhone('1133334444'), '(11) 3333-4444');
  assert.equal(s.BR_STATES.length, 27);
  assert.ok(s.BR_STATES.includes('SP'));
});

test('Dados da empresa has no store table, uses a state select, and users can be deactivated', () => {
  const settings = read('web/assets/settings.js');
  const app = read('web/assets/app.js');
  assert.doesNotMatch(settings, /Nova loja|Adicionar loja|settings\/stores/);
  assert.match(settings, /Dados da empresa/);
  assert.match(settings, /<select id="settings-state"/);
  assert.match(settings, /Desativar usuário/);
  const calendar = settings.match(/async function renderCalendarSettings[\s\S]*?\n}\n/)[0];
  assert.doesNotMatch(calendar, /Versão|v\$\{entry\.version\}/);
  assert.match(calendar, /dateBR\(entry\.date\)/);
  assert.match(app, /configuracoes\/cadastros/);
});

test('calendar edits carry the version and removal names it', () => {
  const s = load(['web/assets/settings.js']);
  const created = s.calendarRequest({date: '2026-12-25', status: 'closed', description: ' Natal '}, null);
  assert.equal(created.path, '/api/companies/1/settings/calendar');
  assert.equal(json(created.body), json({date: '2026-12-25', status: 'closed', description: 'Natal', expected_version: null}));
  assert.equal(s.calendarRequest({date: '2026-12-25', status: 'open', description: ''}, {version: 3}).body.expected_version, 3);
  assert.deepEqual(Object.keys(s.calendarRequest({date: '', status: 'closed'}, null).errors), ['date']);
  assert.equal(s.calendarDeletePath({date: '2026-12-25', version: 2}), '/api/companies/1/settings/calendar/2026-12-25?expected_version=2');
});

test('categories, counterparties and cash accounts archive and rename by version', () => {
  const f = load(FORMS);
  const archive = f.buildArchiveRequest('category', {id: '9007199254740993', version: 4}, true);
  assert.equal(archive.method, 'PATCH');
  assert.equal(archive.path, '/api/companies/1/finance/accounts/9007199254740993');
  assert.equal(json(archive.body), json({expected_version: 4, archived: true}));
  assert.equal(f.buildArchiveRequest('counterparty', {id: '2', version: 1}, false).path, '/api/companies/1/finance/counterparties/2');
  assert.equal(f.buildArchiveRequest('cashAccount', {id: '3', version: 1}, true).path, '/api/companies/1/finance/cash-accounts/3');

  const rename = f.buildRenameRequest('cashAccount', {id: '5', version: 2, name: 'Banco'}, {name: ' Banco PJ '});
  assert.equal(json(rename.body), json({expected_version: 2, name: 'Banco PJ'}));
  assert.deepEqual(Object.keys(f.buildRenameRequest('cashAccount', {id: '5', version: 2, name: 'Banco'}, {name: 'Banco'}).errors), ['name']);
  assert.deepEqual(Object.keys(f.buildRenameRequest('category', {id: '5', version: 2, name: 'Luz'}, {name: '  '}).errors), ['name']);
});

test('quick counterparty creation validates the name and keeps the expense form open', () => {
  const f = load(FORMS);
  assert.deepEqual(Object.keys(f.buildCounterpartyRequest({name: '  ', kind: 'supplier'}).errors), ['name']);
  const request = f.buildCounterpartyRequest({name: ' Distribuidora ', kind: ''});
  assert.equal(request.path, '/api/companies/1/finance/counterparties');
  assert.equal(json(request.body), json({name: 'Distribuidora', kind: 'supplier', document: ''}));
  const source = read('web/assets/finance-forms.js');
  assert.match(source, /data-counterparty-new/);
  assert.match(source, /Adicionar e selecionar/);
  assert.match(source, /type="button" class="btn-secondary" data-counterparty-save/);
});

test('cash movements are an explicit Entrada or Saída, in integer cents', () => {
  const c = load([...FORMS, 'web/assets/finance.js', 'web/assets/cashflow.js']);
  const base = {cash_account_id: '5', amount: '1.234,56', occurred_at: '2026-09-13', description: 'Compra'};
  const out = c.buildCashEventRequest(Object.assign({direction: 'out'}, base));
  assert.equal(out.path, '/api/companies/1/finance/cash-events');
  assert.equal(out.body.amount_cents, -123456);
  assert.equal(c.buildCashEventRequest(Object.assign({direction: 'in'}, base)).body.amount_cents, 123456);
  assert.ok(Object.keys(c.buildCashEventRequest(Object.assign({direction: ''}, base)).errors).includes('direction'));
  assert.ok(Object.keys(c.buildCashEventRequest(Object.assign({}, base, {direction: 'in', amount: '0'})).errors).includes('amount'));
  const source = read('web/assets/cashflow.js');
  assert.match(source, /name="direction"/);
  assert.doesNotMatch(source, /negativo para saída/);
});

test('transfers need two different accounts; the opening balance is its own dated movement', () => {
  const c = load([...FORMS, 'web/assets/finance.js', 'web/assets/cashflow.js']);
  const same = c.buildTransferRequest({from_account_id: '5', to_account_id: '5', amount: '10,00', occurred_at: '2026-09-13', description: ''});
  assert.ok(Object.keys(same.errors).includes('to_account_id'));
  const transfer = c.buildTransferRequest({from_account_id: '5', to_account_id: '6', amount: '10,00', occurred_at: '2026-09-13', description: ''});
  assert.equal(transfer.path, '/api/companies/1/finance/cash-transfers');
  assert.equal(json(transfer.body), json({from_account_id: '5', to_account_id: '6', amount_cents: 1000, occurred_at: '2026-09-13', description: 'Transferência entre contas'}));

  assert.equal(c.buildCashAccountRequest({name: 'Banco', kind: 'bank', opening_amount: '', opening_date: ''}).opening, null);
  assert.ok(Object.keys(c.buildCashAccountRequest({name: 'Banco', kind: 'bank', opening_amount: '2.500,00', opening_date: ''}).errors).includes('opening_date'));
  const account = c.buildCashAccountRequest({name: ' Banco ', kind: 'bank', opening_amount: '2.500,00', opening_date: '2026-09-01'});
  assert.equal(json(account.body), json({name: 'Banco', kind: 'bank'}));
  assert.equal(json(account.opening), json({amount_cents: 250000, occurred_at: '2026-09-01'}));
  assert.equal(json(c.openingBalanceRequest({id: '77'}, account.opening).body),
    json({cash_account_id: '77', amount_cents: 250000, occurred_at: '2026-09-01', description: 'Saldo inicial'}));
});

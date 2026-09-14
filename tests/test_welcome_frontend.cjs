/* Boas-vindas do Resumo: saudação pelo horário, primeiro nome do sócio e uma
 * mensagem motivadora que muda a cada login sem repetir a anterior.
 * Runs the real web/assets/app.js in a vm, like test_status_polling_frontend.cjs. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function loadApp(storage) {
  const source = fs.readFileSync(path.join(__dirname, '..', 'web/assets/app.js'), 'utf8');
  const element = () => ({
    style: {}, dataset: {}, textContent: '', innerHTML: '', value: '',
    classList: {add() {}, remove() {}, toggle() {}, contains: () => false},
    addEventListener() {}, setAttribute() {}, querySelector: () => null, querySelectorAll: () => [],
  });
  const context = vm.createContext({
    console, URLSearchParams, URL, JSON, Date, Math, Promise, Intl,
    document: {hidden: false, addEventListener() {}, getElementById: element, querySelector: element,
      querySelectorAll: () => [], createElement: element, body: element(), documentElement: element()},
    window: {addEventListener() {}, location: {hash: '', search: ''}, matchMedia: () => ({matches: false, addEventListener() {}})},
    localStorage: storage,
    location: {hash: '', search: ''},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: () => new Promise(() => {}),
  });
  vm.runInContext(source.replace(/\nboot\(\);\s*$/, ''), context);
  return context;
}

const memoryStorage = () => {
  const data = {};
  return {getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, removeItem: (k) => { delete data[k]; }};
};

test('the greeting follows the time of day', () => {
  const app = loadApp(memoryStorage());
  assert.equal(app.welcomeGreeting(7), 'Bom dia');
  assert.equal(app.welcomeGreeting(13), 'Boa tarde');
  assert.equal(app.welcomeGreeting(21), 'Boa noite');
  assert.equal(app.welcomeGreeting(2), 'Boa noite');
});

test('only the first name is used, and a missing name leaves no dangling comma', () => {
  const app = loadApp(memoryStorage());
  assert.equal(app.firstName('  Thiago Santos '), 'Thiago');
  assert.equal(app.firstName(''), '');
  assert.equal(app.firstName(null), '');
});

test('a new login never repeats the previous message', () => {
  const app = loadApp(memoryStorage());
  for (let last = 0; last < 5; last++) {
    for (const r of [0, 0.3, 0.6, 0.999]) {
      assert.notEqual(app.pickWelcomeIndex(last, 5, () => r), last);
    }
  }
  assert.equal(app.pickWelcomeIndex(null, 1, () => 0.5), 0);
});

test('consecutive logins show different messages, and a blocked storage still returns one', () => {
  const storage = memoryStorage();
  const app = loadApp(storage);
  const day = new Date(2026, 8, 16);  // a Wednesday mid-month: only the base messages
  let previous = app.chooseWelcomeMessage(day);
  for (let i = 0; i < 20; i++) {
    const next = app.chooseWelcomeMessage(day);
    assert.notEqual(next, previous);
    previous = next;
  }
  const blocked = loadApp({getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }});
  assert.equal(typeof blocked.chooseWelcomeMessage(day), 'string');
});

test('month-end and weekend days add a message that fits the day', () => {
  const app = loadApp(memoryStorage());
  assert.ok(app.contextualWelcomeMessages(new Date(2026, 8, 28)).some((m) => /Reta final/.test(m)));
  assert.ok(app.contextualWelcomeMessages(new Date(2026, 8, 19)).some((m) => /Fim de semana/.test(m)));
  assert.equal(app.contextualWelcomeMessages(new Date(2026, 8, 16)).length, 0);
});

test('alert messages split into a scannable count and a muted sample of names', () => {
  const app = loadApp(memoryStorage());
  const {head, detail} = app.splitAlertMessage('170 produto(s) da curva A com estoque zerado: COCA 2L, SUCO 900ML….');
  assert.equal(head, '170 produto(s) da curva A com estoque zerado');
  assert.equal(detail, 'Ex.: COCA 2L, SUCO 900ML…');
  assert.deepEqual({...app.splitAlertMessage('Estoque ainda não sincronizado.')}, {head: 'Estoque ainda não sincronizado.', detail: ''});
});

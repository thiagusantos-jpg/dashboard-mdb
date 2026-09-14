/* Modo escuro opcional (web/assets/theme.js): o claro é o padrão, a escolha do
 * sócio fica salva no navegador e a página aberta é redesenhada para os gráficos
 * (canvas) pegarem a nova paleta. Runs the real file in a vm with DOM stubs. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

function loadTheme(storage, extra) {
  const attributes = {};
  const button = {attrs: {}, title: '', listeners: [],
    setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(e, fn) { this.listeners.push(fn); }};
  const domReady = [];
  const context = vm.createContext(Object.assign({
    localStorage: storage,
    document: {
      documentElement: {setAttribute: (k, v) => { attributes[k] = v; }, getAttribute: (k) => attributes[k]},
      querySelectorAll: () => [button],
      addEventListener: (event, fn) => { if (event === 'DOMContentLoaded') domReady.push(fn); },
    },
  }, extra || {}));
  context.window = context;
  vm.runInContext(read('web/assets/theme.js'), context);
  domReady.forEach((fn) => fn());
  return {context, attributes, button};
}

const memoryStorage = (initial) => {
  const data = Object.assign({}, initial);
  return {data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }};
};

test('light is the default, and a saved dark choice is applied before first paint', () => {
  assert.equal(loadTheme(memoryStorage()).attributes['data-theme'], 'light');
  const {attributes, button} = loadTheme(memoryStorage({'mdb.theme': 'dark'}));
  assert.equal(attributes['data-theme'], 'dark');
  assert.equal(button.attrs['aria-pressed'], 'true');
});

test('the toggle switches, remembers the choice and repaints the open page', () => {
  const storage = memoryStorage();
  const renders = [];
  const {context, attributes, button} = loadTheme(storage, {renderPage: () => renders.push(1), APP: {company: 218}});
  assert.equal(button.listeners.length, 1);

  button.listeners[0]();
  assert.equal(attributes['data-theme'], 'dark');
  assert.equal(storage.data['mdb.theme'], 'dark');
  assert.equal(button.attrs['aria-pressed'], 'true');
  assert.equal(renders.length, 1);

  context.toggleTheme();
  assert.equal(attributes['data-theme'], 'light');
  assert.equal(storage.data['mdb.theme'], 'light');
});

test('a blocked storage still toggles for the current tab', () => {
  const blocked = {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }};
  const {context, attributes} = loadTheme(blocked);
  assert.equal(attributes['data-theme'], 'light');
  context.toggleTheme();
  assert.equal(attributes['data-theme'], 'dark');
});

test('the theme loads in the head, the toggle lives in the account row and dark styles exist', () => {
  const html = read('web/index.html');
  const css = read('web/assets/style.css');
  assert.ok(html.indexOf('assets/theme.js') < html.indexOf('</head>'));
  assert.match(html, /class="account-row"[\s\S]*data-theme-toggle[\s\S]*data-logout/);
  assert.match(css, /:root\[data-theme="dark"\] \{/);
  // Grid lines and point borders come from the theme; the light hexes survive only as brandTokens() fallbacks.
  assert.doesNotMatch(read('web/assets/echarts-charts.js'), /lineStyle: \{color: '#f0f0f0'\}|borderColor: '#fff'/);
});

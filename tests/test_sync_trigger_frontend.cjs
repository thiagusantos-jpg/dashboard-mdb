/* "Sincronizar agora" on Vercel Hobby, where every request stops at 300 s.
 *
 * A sync longer than that comes back paused (backend/sync.py run_serverless) and
 * the page must ask again until it is done; before, the one long request was cut
 * with a 504 and the panel said the sync could not even start.
 *
 * Runs the real web/assets/app.js in a vm with a scripted fetch. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');

function loadApp(fetch) {
  const source = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const element = () => ({
    style: {}, dataset: {}, textContent: '', innerHTML: '', value: '', className: '',
    classList: {add() {}, remove() {}, toggle() {}, contains: () => false},
    appendChild() {}, insertBefore() {}, removeChild() {}, addEventListener() {},
    setAttribute() {}, removeAttribute() {}, focus() {}, remove() {}, querySelector: () => null,
    querySelectorAll: () => [],
  });
  const statusLine = element();
  const document = {
    hidden: false, addEventListener() {},
    getElementById: (id) => (id === 'sync-action-status' ? statusLine : element()),
    querySelector: element, querySelectorAll: () => [],
    createElement: element, body: element(), documentElement: element(),
  };
  const context = {
    console, document, URLSearchParams, URL, JSON, Date, Math, Promise, Intl,
    window: {addEventListener() {}, location: {hash: '', search: ''},
             matchMedia: () => ({matches: false, addEventListener() {}})},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    location: {hash: '', search: ''},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: () => new Promise(() => {}),  // boot() stays parked; the tests swap fetch in below
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context);
  vm.runInContext('globalThis.APP = APP', context);
  context.APP.company = 218;
  context.fetch = fetch;
  return {context, statusLine};
}

const reply = (status, body) => ({
  ok: status < 400, status,
  json: () => (body === undefined ? Promise.reject(new SyntaxError('not json')) : Promise.resolve(body)),
});

test('a sync the server paused is continued by asking again until it is done', async () => {
  const answers = [{job_id: 7, already_running: false, paused: true},
                   {job_id: 7, already_running: false, paused: true},
                   {job_id: 7, already_running: false, paused: false}];
  const posts = [];
  const {context, statusLine} = loadApp((url, opts) => {
    if (opts && opts.method === 'POST') {
      posts.push(JSON.parse(opts.body).mode);
      return Promise.resolve(reply(202, answers.shift()));
    }
    return Promise.resolve(reply(200, {jobs: [], periods: []}));
  });

  await context.triggerSync('recent', null);

  assert.deepEqual(posts, ['recent', 'recent', 'recent']);
  assert.equal(statusLine.className, 'form-success');
});

test('a server time-out says what was kept, not that the sync never started', async () => {
  const {context, statusLine} = loadApp((url, opts) =>
    Promise.resolve(opts && opts.method === 'POST' ? reply(504) : reply(200, {jobs: [], periods: []})));

  await context.triggerSync('recent', null);

  assert.match(statusLine.textContent, /limite de tempo/);
  assert.doesNotMatch(statusLine.textContent, /Não foi possível iniciar/);
});

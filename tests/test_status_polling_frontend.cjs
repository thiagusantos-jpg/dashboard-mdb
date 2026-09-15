/* The background status poll is what keeps the database endpoint awake.
 *
 * /status is the only thing this app polls on a timer, and its answer changes at
 * most once a day (when a sync publishes a new dataset). A dashboard left open in
 * a background tab was polling every 60s regardless — all day, for a reader who
 * was not looking — and Neon only autosuspends after 5 idle minutes, so those
 * polls alone were enough to bill compute continuously.
 *
 * Runs the real web/assets/app.js in a vm with hand-made document/timer stubs,
 * so these assert behaviour, not the presence of source text. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');

function loadApp() {
  const source = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const listeners = {};
  const intervals = [];
  const cleared = [];
  const element = () => ({
    style: {}, dataset: {}, textContent: '', innerHTML: '', value: '',
    classList: {add() {}, remove() {}, toggle() {}, contains: () => false},
    appendChild() {}, insertBefore() {}, removeChild() {}, addEventListener() {},
    setAttribute() {}, removeAttribute() {}, focus() {}, remove() {}, querySelector: () => null,
    querySelectorAll: () => [],
  });
  const document = {
    hidden: false,
    addEventListener: (event, handler) => {(listeners[event] = listeners[event] || []).push(handler);},
    getElementById: element, querySelector: element, querySelectorAll: () => [],
    createElement: element, body: element(), documentElement: element(),
  };
  let nextTimer = 1;
  const context = {
    console, document, URLSearchParams, URL, JSON, Date, Math, Promise, Intl,
    window: {addEventListener() {}, location: {hash: '', search: ''},
             matchMedia: () => ({matches: false, addEventListener() {}})},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    location: {hash: '', search: ''},
    setInterval: (fn, ms) => {const id = nextTimer++; intervals.push({fn, ms, id}); return id;},
    clearInterval: (id) => {cleared.push(id);},
    setTimeout: () => 1,
    // Never resolves: keeps the boot sequence from running off into async rendering
    // that these tests do not care about.
    fetch: () => new Promise(() => {}),
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context);
  return {context, document, listeners, intervals, cleared};
}

test('the visible-tab poll matches data that changes once a day, not once a minute', () => {
  const {context, intervals} = loadApp();
  intervals.length = 0;

  context.startStatusPolling();

  assert.equal(intervals.length, 1);
  assert.equal(intervals[0].ms, 300000, 'status poll should be 5 minutes');
});

test('a hidden tab schedules no status poll at all', () => {
  const {context, document, intervals} = loadApp();
  document.hidden = true;
  intervals.length = 0;

  context.startStatusPolling();

  assert.equal(intervals.length, 0, 'nobody is looking: polling must not keep the endpoint awake');
});

test('hiding the tab stops the poll and showing it again resumes and catches up', () => {
  const {context, document, listeners, intervals, cleared} = loadApp();
  const onVisibilityChange = (listeners['visibilitychange'] || [])[0];
  assert.ok(onVisibilityChange, 'app.js must react to the tab being hidden');

  intervals.length = 0;
  context.startStatusPolling();
  assert.equal(intervals.length, 1, 'a visible tab polls');
  const timerWhileVisible = intervals[0].id;

  document.hidden = true;
  onVisibilityChange();
  assert.ok(cleared.includes(timerWhileVisible), 'the running poll must be cancelled');

  let refreshed = false;
  context.backgroundRefresh = () => {refreshed = true;};
  document.hidden = false;
  intervals.length = 0;
  onVisibilityChange();

  assert.equal(intervals.length, 1, 'polling resumes when the tab is looked at again');
  assert.equal(refreshed, true, 'returning to the tab shows current data, not a 5-minute-old view');
});

test('a sync in progress still reports live, on its own faster timer', () => {
  /* The slow poll above is for "did a sync land while I was away". Watching a sync
   * you just started is a different need, and must not be slowed down with it. */
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  assert.match(app, /APP\.pollTimer = setInterval\([\s\S]*?\}, 4000\)/,
               'the 4s sync-progress poll must stay as it is');
});

test('a slow status answer is waited for, not stacked with a new request every 4s', async () => {
  /* 2026-09-15: while a sync wrote to the database each /status took 7 to 37 s. The
   * 4s poll fired regardless, stacking up to nine requests per tab on the database,
   * each aborted at 30 s — so no answer was ever drawn and the sync panel froze. */
  const {context, intervals} = loadApp();
  vm.runInContext('globalThis.APP = APP', context);
  context.navRules = {mode: () => 'none'};
  context.APP.company = 218;
  const active = {jobs: [{id: 1, state: 'running'}], periods: []};
  context.fetch = () => Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(active)});
  intervals.length = 0;
  await context.refreshStatus();
  const poll = intervals.find((i) => i.ms === 4000);
  assert.ok(poll, 'an active sync starts the 4s progress poll');

  let requests = 0;
  context.fetch = () => { requests += 1; return new Promise(() => {}); };  // the database is slow
  poll.fn();
  poll.fn();
  poll.fn();

  assert.equal(requests, 1, 'the next tick must wait for the answer still on its way');
});

test('no status request is sent before a company is known', async () => {
  const {context} = loadApp();
  const requested = [];
  context.fetch = (url) => { requested.push(url); return new Promise(() => {}); };
  // Top-level const APP lives in the script scope, not on the vm global: bridge it.
  vm.runInContext('globalThis.APP = APP', context);
  context.APP.company = null;
  await context.refreshStatus();
  assert.deepEqual(requested, []);
});

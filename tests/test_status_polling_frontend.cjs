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

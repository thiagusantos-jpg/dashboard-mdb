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

const september = {period: '2026-09', end: '2026-09-13', as_of: '2026-09-13', partial_month: true};

test('comparison chips name the same elapsed days of a month in progress', () => {
  const app = loadApp(memoryStorage());
  assert.equal(app.compareRef(september, {period: '2026-08'}), '1–13/ago');
  assert.equal(app.compareRef(september, {period: '2025-09'}), '1–13/set/25');
  assert.equal(app.compareRef({period: '2026-08', end: '2026-08-31', partial_month: false}, {period: '2026-07'}), 'jul');
  assert.equal(app.compareRef(september, null), '');
});

test('delta chips carry an arrow and a spoken direction, not only a color', () => {
  const app = loadApp(memoryStorage());
  const down = app.deltaChip('vs 1–13/ago', -20.37);
  assert.match(down, /delta-negative/);
  assert.match(down, /aria-hidden="true">▼</);
  assert.match(down, /visually-hidden">Queda de</);
  assert.match(down, /vs 1–13\/ago/);
  const up = app.deltaChip('vs 1–13/set/25', 9.43);
  assert.match(up, /delta-positive/);
  assert.match(up, />▲</);
  assert.match(app.deltaChip('vs ago', 0.01), />=</);
});

test('the daily average skips the day still in progress and flags days under half of it', () => {
  const app = loadApp(memoryStorage());
  const daily = [['2026-09-11', 319000], ['2026-09-12', 160000], ['2026-09-06', 11000], ['2026-09-13', 15300]]
    .map(([date, revenue]) => ({date, revenue}));
  const stats = app.dailyStats(daily, september);
  assert.equal(Math.round(stats.avg), Math.round((319000 + 160000 + 11000) / 3));
  assert.deepEqual(stats.days.map((d) => d.weekday), ['sex', 'sáb', 'dom', 'dom']);
  assert.deepEqual(stats.days.filter((d) => d.weak).map((d) => d.date), ['2026-09-06']);
  assert.equal(stats.days[3].inProgress, true);
  assert.match(app.dailyInsight(stats), /06\/09 \(dom\)/);
  assert.match(app.dailyInsight(stats), /em andamento/);
});

test('goal figures show progress, where the calendar says it should be, and the pace projection', () => {
  const app = loadApp(memoryStorage());
  const f = app.goalFigures({target_cents: 8000000, achieved_cents: 2467349, remaining_days: 17, required_per_day: 325453}, september);
  assert.equal(Math.round(f.pctDone), 31);
  assert.equal(Math.round(f.expectedPct), 43);
  assert.equal(f.onTrack, false);
  assert.equal(Math.round(f.projection / 100), 56939);
  assert.equal(app.moneyShort(2467349), 'R$ 24,7 mil');
  assert.equal(app.goalFigures({target_cents: 100, achieved_cents: 150}, september).reached, true);
});

test('the Resumo loads the goal, mounts the weekday chart and has its own loading shape', () => {
  const app = fs.readFileSync(path.join(__dirname, '..', 'web/assets/app.js'), 'utf8');
  const charts = fs.readFileSync(path.join(__dirname, '..', 'web/assets/echarts-charts.js'), 'utf8');
  assert.match(app, /loadResumoGoal\(data\)/);
  assert.match(app, /\/goals\/progress/);
  assert.match(app, /mountEchartDaily\(document\.getElementById\('echart-daily'\), daily\)/);
  assert.match(app, /skeleton-welcome/);
  assert.match(charts, /function mountEchartDaily\(/);
});

test('top products read their margin against the store margin and name the thin-margin best seller', () => {
  const app = loadApp(memoryStorage());
  assert.equal(app.marginBand(58, 52.12), 'good');
  assert.equal(app.marginBand(41, 52.12), 'mid');
  assert.equal(app.marginBand(22, 52.12), 'low');
  assert.equal(app.marginBand(null, 52.12), 'mid');
  assert.equal(app.marginBand(30, null), 'mid');
  const top = [{name: 'BANANA PRATA', profit: 24695, margin: 58}, {name: 'CERVEJA ITAIPAVA', profit: 19352, margin: 22},
    {name: 'OVOS CAIPIRA', profit: 19123, margin: 41}];
  assert.match(app.topProfitInsight(top, 52.12), /CERVEJA ITAIPAVA é o 2º em lucro/);
  assert.equal(app.topProfitInsight(top.slice(0, 1), 52.12), '');
});

test('an alert counts only the actions still open for its type, whatever the alert text says now', () => {
  const app = loadApp(memoryStorage());
  const list = [
    {alert_key: 'estoque', alert_version: '170 produto(s)…', status: 'open'},
    {alert_key: 'estoque', alert_version: '168 produto(s)…', status: 'in_progress'},
    {alert_key: 'estoque', alert_version: '150 produto(s)…', status: 'resolved'},
    {alert_key: 'preco', alert_version: '44 produto(s)…', status: 'dismissed'},
  ];
  assert.equal(app.alertActionCount(list, 'estoque'), 2);
  assert.equal(app.alertActionCount(list, 'preco'), 0);
  assert.equal(app.alertActionCount(null, 'estoque'), 0);
});

test('the Resumo lists the top 10 with margin and loads action counts on its alerts', () => {
  const app = fs.readFileSync(path.join(__dirname, '..', 'web/assets/app.js'), 'utf8');
  assert.match(app, /topProfitHtml\(topProfit, t\.margin, data\.period\)/);
  assert.doesNotMatch(app, /mountEchartBarH\(document\.getElementById\('echart-top10'\)/);
  assert.match(app, /data-alert-key=/);
  assert.match(app, /data-action-count/);
  assert.match(app, /loadAlertActions\(\);/);
});

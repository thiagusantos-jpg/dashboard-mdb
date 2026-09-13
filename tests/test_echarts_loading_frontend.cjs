/* ECharts only where a chart is drawn (task C7). Measured: the vendored
 * echarts.min.js is 1.12 MB (368 KB gzip), about 77% of all JavaScript, and
 * was loaded on every screen including login and the Financeiro module. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

function loadCharts(windowObject) {
  const appended = [];
  const context = vm.createContext({
    console, Promise,
    matchMedia: () => ({matches: false}),
    window: windowObject || {},
    document: {
      head: {appendChild(el) { appended.push(el); }},
      createElement(tag) { return {tag, attributes: {}, setAttribute(k, v) { this.attributes[k] = v; }}; },
      querySelectorAll: () => [],
    },
  });
  vm.runInContext(read('web/assets/echarts-charts.js'), context);
  return {context, appended};
}

test('the page no longer loads ECharts up front', () => {
  const html = read('web/index.html');
  assert.doesNotMatch(html, /<script[^>]+echarts\.min\.js/);
  assert.match(html, /assets\/echarts-charts\.js/);
});

test('ensureEcharts injects the vendored script once and resolves when it loads', async () => {
  const {context, appended} = loadCharts();
  const first = context.ensureEcharts();
  const second = context.ensureEcharts();

  assert.equal(appended.length, 1);
  assert.equal(appended[0].tag, 'script');
  assert.match(appended[0].src, /^assets\/vendor\/echarts\/echarts\.min\.js\?v=6\.1\.0$/);
  appended[0].onload();
  await first;
  await second;
  assert.equal(first, second);
});

test('a failed download can be retried on the next chart page', async () => {
  const {context, appended} = loadCharts();
  const failed = context.ensureEcharts();
  appended[0].onerror();
  await assert.rejects(failed);

  context.ensureEcharts();
  assert.equal(appended.length, 2);
});

test('nothing is injected when ECharts is already present', async () => {
  const {context, appended} = loadCharts({echarts: {}});
  await context.ensureEcharts();
  assert.equal(appended.length, 0);
});

test('analytics pages wait for ECharts; finance pages never ask for it', () => {
  const app = read('web/assets/app.js');
  const renderPage = app.match(/async function renderPage\(\) \{[\s\S]*?\n\}\n/)[0];
  const financeReturn = renderPage.indexOf('renderFinancePage(token)');
  const waitIndex = renderPage.indexOf('await ensureEcharts()');
  assert.ok(financeReturn > 0 && waitIndex > financeReturn, 'finance pages return before the chart library is requested');
  assert.match(renderPage, /await ensureEcharts\(\)[\s\S]*isCurrent\(token\)/);
});

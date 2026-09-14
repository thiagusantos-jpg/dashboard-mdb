/* Preços e margens com linguagem de sócio e gráficos na paleta da marca. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const insights = read('web/assets/insights.js');

function extract(source, name) {
  const match = source.match(new RegExp(`function ${name}\\([\\s\\S]*?\\n}`));
  assert.ok(match, `${name} not found`);
  return new Function(`${match[0]}; return ${name};`)();
}

test('the price that keeps the margin divides the new cost by one minus today\'s margin', () => {
  const priceToKeepMargin = extract(insights, 'priceToKeepMargin');
  assert.equal(priceToKeepMargin(400, 50), 800);
  assert.equal(priceToKeepMargin(750, 25), 1000);
  assert.equal(priceToKeepMargin(401, 50), 802);  // rounded up to the next cent
  assert.equal(priceToKeepMargin(0, 50), null);
  assert.equal(priceToKeepMargin(400, 100), null);
  assert.equal(priceToKeepMargin(400, null), null);
});

test('category margins are written out, not only colored', () => {
  const band = extract(insights, 'categoryMarginBand');
  assert.deepEqual(band(61), {cls: 'good', word: 'saudável'});
  assert.deepEqual(band(48), {cls: 'mid', word: 'atenção'});
  assert.deepEqual(band(33), {cls: 'low', word: 'crítica'});
  assert.deepEqual(band(null), {cls: 'mid', word: 'sem custo'});
});

test('the page speaks of margin, leads with price review and drops the guessed opportunity', () => {
  const page = insights.slice(insights.indexOf('function renderPrecos'), insights.indexOf('/* ================================================================ PÁGINA: MAPA'));
  assert.doesNotMatch(page, /Markdown/);
  assert.doesNotMatch(page, /\* 0\.05/);
  assert.match(page, /Preço sugerido para manter a margem/);
  assert.match(page, /Revisar preço/);
  assert.match(page, /Margem por categoria/);
  assert.ok(page.indexOf('Revisar preço') < page.indexOf('chart-precos-scatter'));
  assert.equal((page.match(/explain\(/g) || []).length, 1);
});

test('charts draw with the brand tokens in both themes, not stray hex colors', () => {
  const charts = read('web/assets/echarts-charts.js');
  const css = read('web/assets/style.css');
  assert.match(insights, /get yellow\(\) \{ return chartVar\('--chart-1'/);
  for (const stray of ["'#2E86C1'", "'#E74C3C'", "'#999'", "'#888'", "'#CCCCCC'", "'#BBBBBB'"]) {
    assert.ok(!insights.includes(stray), `insights.js still uses ${stray}`);
    assert.ok(!charts.includes(stray), `echarts-charts.js still uses ${stray}`);
  }
  assert.doesNotMatch(charts, /color: t\.blue/);
  assert.match(css, /:root \{\s*--chart-1: #FFC107;/);
  assert.match(css, /:root\[data-theme="dark"\] \{\s*--chart-1:/);
});

test('suggested prices end like shelf prices and never drop below the exact one', () => {
  const psychologicalPrice = extract(insights, 'psychologicalPrice');
  assert.equal(psychologicalPrice(347), 349);
  assert.equal(psychologicalPrice(349), 349);
  assert.equal(psychologicalPrice(763), 769);
  assert.equal(psychologicalPrice(1314), 1349);
  assert.equal(psychologicalPrice(1350), 1399);
  assert.equal(psychologicalPrice(2029), 2049);
  assert.equal(psychologicalPrice(1995), 1999);
  assert.equal(psychologicalPrice(null), null);
  for (const cents of [101, 999, 1000, 1049, 1050, 9999]) assert.ok(psychologicalPrice(cents) >= cents);
});

test('each product to review can be simulated, turned into an action and filtered by category', () => {
  assert.match(insights, /data-price-review=/);
  assert.match(insights, /pricing\/simulate/);
  assert.match(insights, /products\/\$\{encodeURIComponent\(r\.id\)\}\?period=/);
  assert.match(insights, /alert_key: `preco:\$\{r\.id\}`/);
  assert.match(insights, /id="precos-category"/);
  assert.match(insights, /function costSparkline\(/);
});

test('Mapa de produtos and Desempenho de vendas keep one explanation and plain words', () => {
  const mapa = insights.slice(insights.indexOf('function renderMapa'), insights.indexOf('/* ================================================================ PÁGINA: DIAGNÓSTICO'));
  const diag = insights.slice(insights.indexOf('function renderDiagnostico'), insights.indexOf('/* ================================================================ PÁGINA: SAZONALIDADE'));
  assert.equal((mapa.match(/explain\(/g) || []).length, 1);
  assert.equal((diag.match(/explain\(/g) || []).length, 1);
  assert.doesNotMatch(insights, /Peso Morto|PESO MORTO/);
  // Visible jargon only: mountEchartHeatmap() is the chart function's own name.
  assert.doesNotMatch(diag, /FATURAMENTO =|'Fluxo'|Heatmap por Dia|Heatmap Semanal/);
  assert.match(mapa, /O que fazer com cada grupo/);
  assert.ok(mapa.indexOf('O que fazer com cada grupo') < mapa.indexOf('chart-mapa-matrix'));
  assert.match(diag, /worst && d\.nome === worst\.nome \? COR\.red/);
});

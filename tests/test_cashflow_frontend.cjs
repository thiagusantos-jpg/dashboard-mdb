const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('cash flow navigation and page exist', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const cashflow = fs.readFileSync(path.join(root, 'web/assets/cashflow.js'), 'utf8');

  assert.match(html, /data-page="fluxo-caixa"/);
  assert.match(html, /assets\/cashflow\.js/);
  assert.match(app, /['"]fluxo-caixa['"]/);
  assert.match(cashflow, /finance\/forecast/);
  assert.match(cashflow, /function renderFluxoCaixa/);
});

test('cash flow distinguishes realized, forecast and simulated styles', () => {
  const cashflow = fs.readFileSync(path.join(root, 'web/assets/cashflow.js'), 'utf8');
  assert.match(cashflow, /Realizado/);
  assert.match(cashflow, /Previsto/);
  assert.match(cashflow, /Simulado/);
});

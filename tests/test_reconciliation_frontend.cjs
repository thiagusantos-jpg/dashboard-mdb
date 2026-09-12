const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('reconciliation navigation and page exist', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const reconciliation = fs.readFileSync(path.join(root, 'web/assets/reconciliation.js'), 'utf8');

  assert.match(html, /data-page="conciliacao"/);
  assert.match(html, /assets\/reconciliation\.js/);
  assert.match(app, /['"]conciliacao['"]/);
  assert.match(reconciliation, /finance\/reconciliation/);
  assert.match(reconciliation, /function renderConciliacao/);
});

test('ambiguous suggestions surface as a pending choice, not an automatic pick', () => {
  const reconciliation = fs.readFileSync(path.join(root, 'web/assets/reconciliation.js'), 'utf8');
  assert.match(reconciliation, /Sugestão pendente/);
  assert.match(reconciliation, /data-confirm-form/);
});

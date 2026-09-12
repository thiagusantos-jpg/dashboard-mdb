const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('finance navigation contains result expenses and accounts payable pages', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');

  for (const page of ['financeiro', 'despesas', 'contas-pagar']) {
    assert.match(html, new RegExp(`data-page="${page}"`));
    assert.match(app, new RegExp(`['"]${page}['"]`));
  }
  assert.match(html, /assets\/finance\.js/);
});

test('management result labels do not mix owner compensation and distributions', () => {
  const source = fs.readFileSync(path.join(root, 'web/assets/finance.js'), 'utf8');

  assert.match(source, /Pró-labore/);
  assert.match(source, /Distribuição de lucros/);
  assert.match(source, /Resultado gerencial/);
  assert.match(source, /Orçado/);
  assert.match(source, /Realizado/);
});

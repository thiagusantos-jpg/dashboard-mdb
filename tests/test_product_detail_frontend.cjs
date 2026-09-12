const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('reposicao and produto navigation exist', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const productDetail = fs.readFileSync(path.join(root, 'web/assets/product-detail.js'), 'utf8');

  assert.match(html, /data-page="reposicao"/);
  assert.match(html, /assets\/product-detail\.js/);
  assert.match(app, /['"]reposicao['"]/);
  assert.match(app, /['"]produto['"]/);
  assert.match(productDetail, /function renderReposicao/);
  assert.match(productDetail, /function renderProdutoDetalhe/);
  assert.match(productDetail, /companies\/\$\{APP\.company\}\/replenishment/);
});

test('estoque table links each product to its detail page', () => {
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  assert.match(app, /#\/produto\/\$\{esc\(APP\.period\)\}\?id=\$\{esc\(p\.id\)\}/);
});

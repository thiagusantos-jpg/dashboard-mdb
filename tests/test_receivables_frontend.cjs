const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('receivables navigation and page exist', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const receivables = fs.readFileSync(path.join(root, 'web/assets/receivables.js'), 'utf8');

  assert.match(html, /data-page="recebiveis"/);
  assert.match(html, /assets\/receivables\.js/);
  assert.match(app, /['"]recebiveis['"]/);
  assert.match(receivables, /receivables-import/);
  assert.match(receivables, /function renderRecebiveis/);
});

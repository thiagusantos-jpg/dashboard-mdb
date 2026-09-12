const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('loans navigation and page exist', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const loans = fs.readFileSync(path.join(root, 'web/assets/loans.js'), 'utf8');

  assert.match(html, /data-page="emprestimos"/);
  assert.match(html, /assets\/loans\.js/);
  assert.match(app, /['"]emprestimos['"]/);
  assert.match(loans, /finance\/loans/);
  assert.match(loans, /function renderEmprestimos/);
});

test('equal installments split the principal and fold the rounding remainder into the last one', () => {
  const source = fs.readFileSync(path.join(root, 'web/assets/loans.js'), 'utf8');
  const module = {exports: {}};
  // eslint-disable-next-line no-new-func
  new Function('module', source + '\nmodule.exports = {buildEqualInstallments};')(module);
  const installments = module.exports.buildEqualInstallments(10_000, 50, 3, '2026-01-10');
  const totalPrincipal = installments.reduce((s, i) => s + i.principal_cents, 0);
  assert.equal(totalPrincipal, 10_000);
  assert.equal(installments[0].due_date, '2026-01-10');
  assert.equal(installments[1].due_date, '2026-02-10');
  assert.equal(installments[2].due_date, '2026-03-10');
});

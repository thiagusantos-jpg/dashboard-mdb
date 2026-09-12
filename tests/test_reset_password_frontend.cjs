const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('login screen exposes a forgot-password flow wired to /api/reset-password', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');

  assert.match(html, /id="forgot-password-link"/);
  assert.match(html, /id="reset-password-form"/);
  assert.match(html, /id="reset-email"/);
  assert.match(html, /id="reset-master-password"/);
  assert.match(html, /id="reset-new-password"/);

  assert.match(app, /forgot-password-link['"]\)\.addEventListener\('click', showResetPassword\)/);
  assert.match(app, /reset-password-form['"]\)\.addEventListener\('submit', onResetPasswordSubmit\)/);
  assert.match(app, /\/api\/reset-password/);
  assert.match(app, /master_password/);
});

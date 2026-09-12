const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('login collects and sends the individual user email', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');

  assert.match(html, /id="login-email"[^>]+autocomplete="username"[^>]+required/);
  assert.match(app, /getElementById\('login-email'\)\.value\.trim\(\)/);
  assert.match(app, /JSON\.stringify\(\{email, password: pass\}\)/);
});

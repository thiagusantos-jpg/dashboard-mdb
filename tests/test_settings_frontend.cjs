const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('settings navigation exposes the planned administration sections', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');

  assert.match(html, /data-page="configuracoes"/);
  assert.match(html, /assets\/settings\.js/);
  for (const section of ['empresa', 'usuarios', 'calendario', 'metas', 'alertas', 'integracoes']) {
    assert.match(app, new RegExp(`configuracoes\\/${section}`));
  }
});

test('company settings saves the record version and has labelled fields', () => {
  const source = fs.readFileSync(path.join(root, 'web/assets/settings.js'), 'utf8');

  assert.match(source, /expected_version/);
  assert.match(source, /for="settings-legal-name"/);
  assert.match(source, /for="settings-cnpj"/);
  assert.doesNotMatch(source, /onclick=/);
});

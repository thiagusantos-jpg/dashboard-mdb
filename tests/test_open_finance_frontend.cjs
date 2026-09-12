const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('integrations settings renders the Stone Open Finance panel', () => {
  const settings = fs.readFileSync(path.join(root, 'web/assets/settings.js'), 'utf8');

  assert.match(settings, /function renderIntegrationsSettings/);
  assert.match(settings, /open-finance\/consent/);
  assert.match(settings, /data-of-sync/);
  assert.match(settings, /data-of-revoke/);
});

test('settings routing dispatches integracoes to the real panel, not the placeholder', () => {
  const settings = fs.readFileSync(path.join(root, 'web/assets/settings.js'), 'utf8');
  assert.match(settings, /section === 'integracoes'\) return await renderIntegrationsSettings/);
});

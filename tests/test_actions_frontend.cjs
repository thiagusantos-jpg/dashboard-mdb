const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('central de acoes page is wired up', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  const actions = fs.readFileSync(path.join(root, 'web/assets/actions.js'), 'utf8');

  assert.match(html, /data-page="acoes"/);
  assert.match(html, /assets\/actions\.js/);
  assert.match(app, /['"]acoes['"]/);
  assert.match(app, /acoes:\s*renderAcoes/);
  assert.match(actions, /function renderAcoes/);
  assert.match(actions, /companies\/\$\{APP\.company\}\/actions/);
});

test('alerts can be turned into actions', () => {
  const app = fs.readFileSync(path.join(root, 'web/assets/app.js'), 'utf8');
  assert.match(app, /data-create-action/);
  assert.match(app, /function onCreateActionFromAlert/);
  assert.match(app, /method:\s*'POST'/);
});

test('action rows expose status transitions', () => {
  const actions = fs.readFileSync(path.join(root, 'web/assets/actions.js'), 'utf8');
  assert.match(actions, /data-action-transition/);
  assert.match(actions, /function onTransitionAction/);
  assert.match(actions, /actions\/\$\{actionId\}\/transition/);
});

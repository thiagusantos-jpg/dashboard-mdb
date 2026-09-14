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

test('action cards take, conclude and dismiss through the transition endpoint', () => {
  const actions = fs.readFileSync(path.join(root, 'web/assets/actions.js'), 'utf8');
  assert.match(actions, /data-action-take=/);
  assert.match(actions, /data-action-conclude=/);
  assert.match(actions, /data-action-dismiss=/);
  assert.match(actions, /data-actions-tab=/);
  assert.match(actions, /actions\/\$\{a\.id\}\/transition/);
  assert.match(actions, /id="actions-stats"/);
  assert.match(actions, /id="actions-origin"/);
  assert.doesNotMatch(actions, /📣/);
  // app.js's global click handler owns these attributes (web/assets/app.js, document click).
  assert.doesNotMatch(actions, /data-tab=|data-period=|data-nav=/);
  assert.doesNotMatch(actions, /style="/);
});

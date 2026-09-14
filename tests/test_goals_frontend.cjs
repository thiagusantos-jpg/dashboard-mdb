const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

test('goals settings panel replaces the placeholder', () => {
  const settings = fs.readFileSync(path.join(root, 'web/assets/settings.js'), 'utf8');

  assert.match(settings, /section === 'metas'\) return await renderGoalsSettings/);
  assert.match(settings, /function renderGoalsSettings/);
  assert.match(settings, /goals\/progress/);
});

test('insights uses the configured margin goal instead of a hardcoded constant', () => {
  const insights = fs.readFileSync(path.join(root, 'web/assets/insights.js'), 'utf8');
  assert.match(insights, /data\.margin_goal_pct/);
});

test('goal values are typed in Brazilian format, previewed, and saved goals can be edited or removed', () => {
  const settings = fs.readFileSync(path.join(root, 'web/assets/settings.js'), 'utf8');
  assert.doesNotMatch(settings, /id="goal-revenue"[^>]*type="number"/);
  assert.match(settings, /id="goal-revenue"[^>]*inputmode="decimal"/);
  assert.match(settings, /parseDecimalBR\(revenueText\) \* 100/);
  assert.match(settings, /Será salvo como/);
  assert.match(settings, /data-goal-edit=/);
  assert.match(settings, /data-goal-delete=/);
  assert.match(settings, /method: 'DELETE'/);
  assert.match(settings, /\/goals`\)\]\)/);
});

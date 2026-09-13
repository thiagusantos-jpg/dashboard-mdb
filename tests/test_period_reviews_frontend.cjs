/* Monthly review panel in Resultado gerencial (task C6). The panel is a
 * managerial sign-off: explicit acknowledgement to review, a reason to reopen,
 * and never a "trustworthy" label on its own. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const json = (value) => JSON.stringify(value);

function loadFinance() {
  const context = vm.createContext({
    console, APP: {company: '1', financePeriod: '2026-09'},
    MONTHS: ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'],
    esc: (s) => String(s == null ? '' : s), icon: () => '', money: (c) => `R$ ${c / 100}`, dt: (iso) => iso,
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'web/assets/finance.js'), 'utf8'), context);
  return context;
}

test('review requests carry the revision the user looked at', () => {
  const f = loadFinance();
  assert.equal(f.periodReviewUrl('2026-09'), '/api/companies/1/finance/period-reviews/2026-09');

  const open = {status: 'in_progress', revision: 'abc'};
  assert.deepEqual(Object.keys(f.buildReviewRequest(open, {acknowledged: false}).errors), ['acknowledged']);
  const review = f.buildReviewRequest(open, {acknowledged: true, reason: ''});
  assert.equal(review.path, '/api/companies/1/finance/period-reviews/2026-09');
  assert.equal(json(review.body), json({action: 'review', expected_revision: 'abc', reason: '', acknowledged: true}));

  const reviewed = {status: 'reviewed', revision: 'def'};
  assert.deepEqual(Object.keys(f.buildReviewRequest(reviewed, {reason: '  '}).errors), ['reason']);
  const reopen = f.buildReviewRequest(reviewed, {reason: ' Faltou a conta de água '});
  assert.equal(json(reopen.body), json({action: 'reopen', expected_revision: 'def', reason: 'Faltou a conta de água', acknowledged: false}));
});

test('the panel lists every check, asks for acknowledgement and never certifies on its own', () => {
  const f = loadFinance();
  const open = f.reviewPanelHtml({
    status: 'in_progress', revision: 'abc', changed_since_review: true, reason: '',
    checks: [
      {key: 'sales_available', label: 'Vendas do mês sincronizadas do Mobne', ok: true, count: null, detail: ''},
      {key: 'forecasts_pending', label: 'Recorrências previstas por confirmar', ok: false, count: 2, detail: ''},
    ],
  });
  assert.match(open, /Vendas do mês sincronizadas do Mobne/);
  assert.match(open, /Recorrências previstas por confirmar/);
  assert.match(open, /\b2\b/);
  assert.match(open, /type="checkbox"[^>]*name="acknowledged"|name="acknowledged"[^>]*type="checkbox"/);
  assert.match(open, /alterados depois da última revisão/);
  assert.doesNotMatch(open, /confiável|seguro/i);

  const reviewed = f.reviewPanelHtml({status: 'reviewed', revision: 'def', reviewed_at: '2026-09-13T12:00:00', checks: []});
  assert.match(reviewed, /Reabrir apuração/);
  assert.match(reviewed, /name="reason"/);
});

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');

// settings.js is a browser script: load it with the few globals the panel needs.
function loadSettings() {
  const sandbox = {
    APP: {}, console, Date, Math, Number, String, RegExp, URLSearchParams,
    esc: (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`),
    dt: (iso) => (iso ? `DT(${iso})` : '—'),
    num: (n) => String(n),
    document: {getElementById: () => null, querySelectorAll: () => [], addEventListener() {}},
    window: {},
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(root, 'web/assets/settings.js'), 'utf8'), sandbox);
  return sandbox;
}

const job = (fields) => Object.assign({id: 1, mode: 'recent', state: 'running', created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(), detail: '', completed: 0, total: 0}, fields);

test('the stage follows the run: validation, sales months, catalogs', () => {
  const s = loadSettings();
  assert.equal(s.syncStage(job({total: 0})), 0);
  assert.equal(s.syncStage(job({total: 6, completed: 1})), 1);  // 2 months + 4 catalogs
  assert.equal(s.syncStage(job({total: 6, completed: 2})), 2);
  assert.equal(s.syncStage(job({total: 6, completed: 6})), 3);
});

test('the bar moves inside a step as its pages download', () => {
  const s = loadSettings();
  assert.equal(s.syncFraction(job({total: 6, completed: 0, detail: 'Cupons 2026-09: página 3/6 · 300/600'})), 0.5 / 6);
  assert.equal(s.syncFraction(job({total: 6, completed: 3})), 0.5);
  assert.equal(s.syncFraction(job({total: 0})), 0);
});

test('a running sync shows its stage, percentage, detail and live clocks', () => {
  const s = loadSettings();
  const html = s.syncPanelHtml({jobs: [job({total: 6, completed: 3, detail: 'products: página 1/2 · 50/100'})]});
  assert.match(html, /aria-current="step">.*Catálogos/);
  assert.match(html, /aria-valuenow="58"/);
  assert.match(html, /products: página 1\/2/);
  assert.match(html, /3 de 6 etapas/);
  assert.match(html, /data-sync-since=/);
  assert.match(html, /data-sync="recent" disabled>Sincronizando…/);
});

test('a click shows progress at once, before the server creates the job', () => {
  const s = loadSettings();
  const html = s.syncPanelHtml({jobs: []}, 'reconcile');
  assert.match(html, /Iniciando sincronização… · Reconciliação completa/);
  assert.match(html, /sync-bar is-indeterminate/);
  assert.match(html, /data-sync="reconcile" disabled/);
});

test('a sync paused between server calls still reads as syncing, not as waiting in line', () => {
  const s = loadSettings();
  const html = s.syncPanelHtml({jobs: [job({state: 'queued', total: 6, completed: 2})]});
  assert.match(html, /Sincronizando · Recente/);
  assert.doesNotMatch(html, /Na fila/);
});

test('an idle panel leads with the last result and its error', () => {
  const s = loadSettings();
  const html = s.syncPanelHtml({jobs: [job({state: 'failed', detail: 'Execução interrompida', error: 'Falha de comunicação'})]});
  assert.match(html, /sync-card is-error/);
  assert.match(html, /Última sincronização falhou/);
  assert.match(html, /sync-error">Falha de comunicação/);
  assert.match(html, /data-sync="recent">Sincronizar agora/);
});

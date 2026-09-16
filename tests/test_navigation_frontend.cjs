/* Two modules, Análises and Financeiro (task C3). navigation.js holds the
 * pure route rules; app.js only applies them. Same vm style as the other
 * frontend tests. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

function load(storage) {
  const context = vm.createContext({console, URLSearchParams, sessionStorage: storage});
  vm.runInContext(read('web/assets/navigation.js'), context);
  return context;
}

function memoryStorage() {
  const data = {};
  return {getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, data};
}

test('every page belongs to one module; settings and account belong to none', () => {
  const nav = load();
  assert.equal(nav.moduleForPage('precos'), 'analises');
  assert.equal(nav.moduleForPage('resumo'), 'analises');
  assert.equal(nav.moduleForPage('produto'), 'analises');
  assert.equal(nav.moduleForPage('contas-pagar'), 'financeiro');
  assert.equal(nav.moduleForPage('financeiro'), 'financeiro');
  assert.equal(nav.moduleForPage('configuracoes'), null);
});

test('old links land on their new home without losing the rest of the route', () => {
  const nav = load();
  assert.equal(nav.canonicalRoute('#/sync/2026-09'), '#/configuracoes/integracoes');
  assert.equal(nav.canonicalRoute('#/sync'), '#/configuracoes/integracoes');
  assert.equal(nav.canonicalRoute('#/emprestimos/2026-09'), '#/contas-pagar?tipo=emprestimo');
  assert.equal(nav.canonicalRoute('#/emprestimos'), '#/contas-pagar?tipo=emprestimo');
  assert.equal(nav.canonicalRoute('#/precos/2026-08'), '#/precos/2026-08');
  assert.equal(nav.canonicalRoute('#/estoque/2026-08?filtro=ruptura'), '#/estoque/2026-08?filtro=ruptura');
  assert.equal(nav.canonicalRoute(''), '');
});

test('each page declares which period filter applies to it', () => {
  const nav = load();
  assert.equal(nav.periodModeForPage('resumo'), 'sales');
  assert.equal(nav.periodModeForPage('estoque'), 'sales');
  assert.equal(nav.periodModeForPage('financeiro'), 'competence');
  assert.equal(nav.periodModeForPage('despesas'), 'competence');
  assert.equal(nav.periodModeForPage('contas-pagar'), 'none');
  assert.equal(nav.periodModeForPage('fluxo-caixa'), 'none');
  assert.equal(nav.periodModeForPage('conciliacao'), 'none');
  assert.equal(nav.periodModeForPage('configuracoes'), 'none');
});

test('switching modules restores each module route without overwriting the other', () => {
  const storage = memoryStorage();
  const nav = load(storage);
  const routes = nav.createModuleRoutes(storage);
  assert.equal(routes.routeFor('analises'), '#/resumo');
  assert.equal(routes.routeFor('financeiro'), '#/financeiro');

  routes.remember('#/precos/2026-08');
  routes.remember('#/contas-pagar?tipo=emprestimo');
  routes.remember('#/configuracoes/usuarios');  // settings never replaces a module's last route

  assert.equal(routes.routeFor('analises'), '#/precos/2026-08');
  assert.equal(routes.routeFor('financeiro'), '#/contas-pagar?tipo=emprestimo');

  // A new visit in the same tab restores the same context.
  const again = load(storage).createModuleRoutes(storage);
  assert.equal(again.routeFor('analises'), '#/precos/2026-08');
});

test('storage that throws never breaks navigation', () => {
  const broken = {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }};
  const nav = load(broken);
  const routes = nav.createModuleRoutes(broken);
  routes.remember('#/mapa/2026-08');
  assert.equal(routes.routeFor('analises'), '#/mapa/2026-08');
});

test('competence steps by calendar month, independent of synced sales months', () => {
  const nav = load();
  assert.equal(nav.shiftMonth('2026-12', 1), '2027-01');
  assert.equal(nav.shiftMonth('2026-01', -1), '2025-12');
});

test('the menu shows a textual module switcher and only real links', () => {
  const html = read('web/index.html');
  const app = read('web/assets/app.js');
  assert.match(html, /assets\/navigation\.js/);
  assert.ok(html.indexOf('assets/navigation.js') < html.indexOf('assets/app.js'));
  assert.match(html, /<nav[^>]+class="module-switch"[^>]+aria-label="Módulo"/);
  assert.match(html, /data-module-link="analises"[^>]*>Análises</);
  assert.match(html, /data-module-link="financeiro"[^>]*>Financeiro</);
  assert.match(html, /data-module-nav="analises"/);
  assert.match(html, /data-module-nav="financeiro"/);
  for (const [page, label] of [
    ['resumo', 'Resumo executivo'], ['precos', 'Preços e margens'], ['mapa', 'Mapa de produtos'],
    ['diagnostico', 'Desempenho de vendas'], ['sazonalidade', 'Sazonalidade e tendências'],
    ['visao', 'Projeções de vendas'], ['estoque', 'Produtos e estoque'],
    ['financeiro', 'Resultado gerencial'], ['contas-pagar', 'Contas a pagar'], ['despesas', 'Despesas do mês'],
    ['fluxo-caixa', 'Fluxo de caixa'], ['conciliacao', 'Conciliação bancária'],
  ]) {
    assert.match(html, new RegExp(`data-page="${page}"[^>]*>[\\s\\S]{0,1200}?${label}</a>`), `${page} → ${label}`);
  }
  assert.doesNotMatch(html, /data-page="sync"/);
  assert.doesNotMatch(html, /data-page="emprestimos"/);
  assert.match(html, /class="sidebar-footer"[\s\S]*href="#\/configuracoes\/empresa"/);
  assert.match(app, /canonicalRoute\(/);
  assert.doesNotMatch(app, /\balert\(/);
});

test('sync lives under Configurações → Integrações', () => {
  const settings = read('web/assets/settings.js');
  const app = read('web/assets/app.js');
  assert.match(settings, /Integrações e sincronização/);
  assert.match(settings, /function syncPanelHtml/);
  assert.match(settings, /data-sync="recent"/);
  assert.doesNotMatch(app, /function renderSyncPage/);
});

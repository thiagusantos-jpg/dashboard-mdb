/* Central de Ações: regras puras — título curto, situação de hoje, atalhos, contadores.
 * Runs the real web/assets/actions.js in a vm with the few app.js globals it reads. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

function loadActions() {
  const context = vm.createContext({
    console, URLSearchParams, Date, Math, Intl,
    APP: {company: 1, period: '2026-09'},
    ALERT_FILTERS: {estoque: 'ruptura', preco: 'abaixo-custo', custo: 'sem-custo'},
    routeHash: (page, period, params) => `#/${page}${period ? '/' + period : ''}${params && params.toString() ? '?' + params.toString() : ''}`,
    esc: (s) => String(s == null ? '' : s),
    num: (v) => String(v),
    money: (cents) => `R$ ${(cents / 100).toFixed(2)}`,
    pct: (v) => `${v.toFixed(2)}%`,
    icon: () => '',
  });
  vm.runInContext(read('web/assets/insights.js').match(/function sentenceCase\([\s\S]*?\n}/)[0], context);
  vm.runInContext(read('web/assets/actions.js'), context);
  return context;
}

const STOCK_TITLE = '170 produto(s) da curva A com estoque zerado ou negativo no Mobne: REFRIGERANTE COCA-COLA ORIGINAL RETORNAVEL PET 2L, RODO PLASTICO 40CM BRUBALAR, SUCO DE LARANJA INTEGRAL PRATS 900ML, UVA THOMPSON (BANDEJA), TAPIOCA DA TERRINHA 500G….';

test('a Resumo alert becomes a short title with a few product names', () => {
  const c = loadActions();
  const view = c.actionDisplay({alert_key: 'estoque', title: STOCK_TITLE, baseline_count: null});
  assert.equal(view.title, '170 produtos da curva A sem estoque');
  assert.deepEqual(Array.from(view.products), [
    'Refrigerante coca-cola original retornavel pet 2l', 'Rodo plastico 40cm brubalar', 'Suco de laranja integral prats 900ml']);
  assert.equal(view.more, 167);
  assert.equal(c.actionDisplay({alert_key: 'estoque', title: '1 produto(s) da curva A com estoque zerado ou negativo no Mobne: UVA.'}).title,
    '1 produto da curva A sem estoque');
  const manual = c.actionDisplay({alert_key: 'manual:abc', title: 'Negociar prazo com a distribuidora'});
  assert.equal(manual.title, 'Negociar prazo com a distribuidora');
  assert.equal(manual.products.length, 0);
});

test('the origin comes from the alert key', () => {
  const c = loadActions();
  assert.equal(c.actionOrigin('estoque'), 'estoque');
  assert.equal(c.actionOrigin('preco:8123'), 'preco');
  assert.equal(c.actionOrigin('mapa:baixo-giro'), 'mapa');
  assert.equal(c.actionOrigin('manual:k2'), 'manual');
});

test("today's state compares the newest month with the count at creation", () => {
  const c = loadActions();
  const data = {
    alerts: [{type: 'preco', count: 38, message: '38 produto(s) vendendo abaixo do custo atual do Mobne: X.'}],
    product_map: {products: [
      {classification: 'Baixo giro', cost: 10, revenue: 20},
      {classification: 'Baixo giro', cost: 0, revenue: 20},
      {classification: 'Estrela', cost: 5, revenue: 20}]},
    inventory: [{id: '8123', current_price: 349, current_cost: 259}],
  };
  let live = c.actionLiveState({alert_key: 'preco', title: '44 produto(s) vendendo…', baseline_count: 44}, data);
  assert.deepEqual([live.kind, live.now, live.was, live.gone], ['count', 38, 44, false]);
  live = c.actionLiveState({alert_key: 'estoque', title: STOCK_TITLE}, data);
  assert.deepEqual([live.now, live.was, live.gone], [0, 170, true]);
  live = c.actionLiveState({alert_key: 'mapa:baixo-giro', title: 'Avaliar retirada', baseline_count: 3}, data);
  assert.deepEqual([live.now, live.was], [1, 3]);
  live = c.actionLiveState({alert_key: 'preco:8123', title: 'Reajustar'}, data);
  assert.equal(live.kind, 'price');
  assert.equal(Math.round(live.margin), 26);
  assert.equal(c.actionLiveState({alert_key: 'manual:x', title: 'Ligar'}, data), null);
  assert.equal(c.actionLiveState({alert_key: 'estoque', title: STOCK_TITLE}, null), null);
});

test("today's state reads as a sentence, and a closed action is not told to conclude", () => {
  const c = loadActions();
  const unit = ['produto', 'produtos'];
  assert.match(c.actionLiveHtml({kind: 'count', now: 38, was: 44, gone: false}, unit, false), /No painel hoje: <b>38 produtos<\/b>.*−6 desde a criação/);
  assert.match(c.actionLiveHtml({kind: 'count', now: 0, was: 44, gone: true}, unit, false), /pode concluir/);
  assert.doesNotMatch(c.actionLiveHtml({kind: 'count', now: 0, was: 44, gone: true}, unit, true), /pode concluir/);
  assert.match(c.actionLiveHtml({kind: 'count', now: 1, was: null, gone: false}, unit, false), /<b>1 produto<\/b>/);
  assert.equal(c.actionLiveHtml(null, unit, false), '');
});

test('each action links back to where its alert came from', () => {
  const c = loadActions();
  assert.equal(c.actionSource({alert_key: 'estoque'}, '2026-09').href, '#/estoque/2026-09?filtro=ruptura');
  assert.equal(c.actionSource({alert_key: 'custo'}, '2026-09').href, '#/estoque/2026-09?filtro=sem-custo');
  assert.equal(c.actionSource({alert_key: 'mapa:gerador'}, '2026-09').href, '#/mapa/2026-09');
  assert.equal(c.actionSource({alert_key: 'preco:77'}, '2026-09').href, '#/produto/2026-09?id=77');
  assert.equal(c.actionSource({alert_key: 'integracao'}, '2026-09').href, '#/configuracoes/integracoes');
  assert.equal(c.actionSource({alert_key: 'manual:x'}, '2026-09'), null);
});

test('a repeated alert points to the last time it was handled', () => {
  const c = loadActions();
  const list = [
    {id: '3', alert_key: 'estoque', status: 'open', created_at: '2026-09-14T03:47:00+00:00'},
    {id: '2', alert_key: 'estoque', status: 'resolved', created_at: '2026-09-12T23:08:00+00:00', resolved_at: '2026-09-14T03:40:00+00:00'},
    {id: '1', alert_key: 'estoque', status: 'dismissed', created_at: '2026-09-01T10:00:00+00:00', resolved_at: '2026-09-02T10:00:00+00:00'},
    {id: '4', alert_key: 'preco', status: 'resolved', created_at: '2026-09-10T10:00:00+00:00', resolved_at: '2026-09-11T10:00:00+00:00'},
  ];
  assert.equal(c.previousHandled(list[0], list).id, '2');
  assert.equal(c.previousHandled(list[3], list), null);
});

test('counters, filters, order and the Resumo digest', () => {
  const c = loadActions();
  const today = '2026-09-14';
  const list = [
    {id: '1', alert_key: 'estoque', status: 'open', priority: 'high', due_date: '2026-09-13', created_at: '2026-09-12T15:00:00Z', updated_at: '2026-09-12T15:00:00Z'},
    {id: '2', alert_key: 'mapa:gerador', status: 'in_progress', priority: 'medium', due_date: '2026-09-20', created_at: '2026-09-01T15:00:00Z', updated_at: '2026-09-02T15:00:00Z'},
    {id: '3', alert_key: 'preco:7', status: 'resolved', priority: 'medium', created_at: '2026-09-01T15:00:00Z', updated_at: '2026-09-10T15:00:00Z', resolved_at: '2026-09-10T15:00:00Z'},
    {id: '4', alert_key: 'custo', status: 'resolved', priority: 'low', created_at: '2026-06-01T15:00:00Z', updated_at: '2026-07-01T15:00:00Z', resolved_at: '2026-07-01T15:00:00Z'},
  ];
  const s = c.actionStats(list, today);
  assert.deepEqual([s.open, s.inProgress, s.overdue, s.resolved30], [1, 1, 1, 1]);
  // Array.from: arrays built inside the vm have another realm's prototype, which deepEqual rejects.
  assert.deepEqual(Array.from(c.filterActions(list, 'atrasadas', '', today), (a) => a.id), ['1']);
  assert.deepEqual(Array.from(c.filterActions(list, 'concluidas', 'preco', today), (a) => a.id), ['3']);
  assert.deepEqual(Array.from(c.filterActions(list, 'todas', 'mapa', today), (a) => a.id), ['2']);
  assert.equal(c.pendingActionCount(list), 2);

  const d = c.actionDigest(list, today);
  assert.deepEqual([d.pending, d.overdue, d.stale, d.oldestDays], [2, 1, 1, 13]);
  assert.equal(c.actionsNoticeText(d), '1 ação atrasada · 1 parada há mais de 7 dias');
  assert.equal(c.actionsNoticeText(c.actionDigest([], today)), '');

  const order = Array.from(c.sortActions([
    {id: 'a', status: 'open', priority: 'high', created_at: '2026-09-10T15:00:00Z'},
    {id: 'b', status: 'open', priority: 'medium', due_date: '2026-09-01', created_at: '2026-09-05T15:00:00Z'},
    {id: 'c', status: 'open', priority: 'high', created_at: '2026-09-12T15:00:00Z'},
  ], today), (a) => a.id);
  assert.deepEqual(order, ['b', 'c', 'a']);
  assert.equal(c.actionAge('2026-09-14T15:00:00Z', today), 'hoje');
  assert.equal(c.actionAge('2026-09-13T15:00:00Z', today), 'ontem');
  assert.equal(c.actionAge('2026-09-12T15:00:00Z', today), 'há 2 dias');
});

test('forms send what the backend expects', () => {
  const c = loadActions();
  const field = (value) => ({value});
  assert.deepEqual({...c.actionEditPayload({assignee: field(''), due_date: field('2026-09-20'), priority: field('high')})},
    {assignee: null, due_date: '2026-09-20', priority: 'high'});
  const manual = c.manualActionPayload({title: field('  Negociar prazo '), priority: field('medium'),
    assignee: field('4611686018427387905'), due_date: field('')}, 1700000000000);
  assert.equal(manual.alert_key, 'manual:' + (1700000000000).toString(36));
  assert.equal(manual.title, 'Negociar prazo');
  assert.equal(manual.assignee, '4611686018427387905');  // big ids travel as text, never Number()
  assert.equal(manual.due_date, null);
  assert.throws(() => c.manualActionPayload({title: field(' '), priority: field('low'), assignee: field(''), due_date: field('')}, 1));
  assert.equal(c.dismissNote('Não vale o esforço agora', '  volta em outubro '), 'Não vale o esforço agora. volta em outubro');
  assert.equal(c.dismissNote('Outro motivo', ''), 'Outro motivo');
});

test('history events read as sentences', () => {
  const c = loadActions();
  assert.equal(c.actionEventText({event_type: 'created'}), 'Ação criada');
  assert.equal(c.actionEventText({event_type: 'status_changed', to_status: 'in_progress'}), 'Assumida');
  assert.equal(c.actionEventText({event_type: 'status_changed', to_status: 'resolved'}), 'Concluída');
  assert.equal(c.actionEventText({event_type: 'updated', note: 'Prazo: 20/09/2026'}), 'Prazo: 20/09/2026');
});

test('a price action shows its measured result', () => {
  const c = loadActions();
  const before = {margin: 20, revenue: 2000}, after = {margin: 33.33, revenue: 2400};
  assert.match(c.actionResultHtml({kind: 'measured', days: 30, total: 30, ready_on: '2026-09-20', before, after}),
    /margem de <b>20\.00%<\/b> para <b>33\.33%<\/b>/);
  assert.match(c.actionResultHtml({kind: 'measuring', days: 5, total: 30, ready_on: '2026-09-20', before, after}),
    /parcial: 5 de 30 dias/);
  assert.match(c.actionResultHtml({kind: 'measuring', days: 0, total: 30, ready_on: '2026-09-20', before, after: null}),
    /pronto em 20\/09/);
  assert.equal(c.actionResultHtml({kind: 'none'}), '');
  assert.equal(c.actionResultHtml({kind: 'pending'}), '');
});

test('the Central de Ações has no month selector and reads the newest month', () => {
  const nav = vm.createContext({console, URLSearchParams, window: {}, sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}}});
  // If navigation.js ever needs more globals at load, copy the stubs from tests/test_navigation_frontend.cjs.
  vm.runInContext(read('web/assets/navigation.js'), nav);
  assert.equal(nav.periodModeForPage('acoes'), 'none');
  assert.equal(nav.periodModeForPage('resumo'), 'sales');
  const app = read('web/assets/app.js');
  assert.match(app, /const period = page === 'acoes' \? latestSalesPeriod\(\) : APP\.period;/);
});

test('the menu counts pending actions and the Resumo points to the overdue ones', () => {
  const html = read('web/index.html');
  assert.match(html, /data-page="acoes"[^\n]*data-actions-badge/);
  const app = read('web/assets/app.js');
  assert.match(app, /<p class="actions-notice" id="resumo-actions" hidden><\/p>/);
  assert.match(app, /loadResumoActionsNotice\(\);/);
  assert.match(app, /refreshActionsBadge\(\);/);
  const actions = read('web/assets/actions.js');
  assert.match(actions, /function updateActionsBadge\(/);
  assert.match(actions, /situacao: 'atrasadas'/);
});

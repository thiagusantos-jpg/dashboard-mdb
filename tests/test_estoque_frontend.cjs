/* Produtos e estoque: cobertura, valor parado, filtros de ação e exportação. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const app = read('web/assets/app.js');

function extract(source, name, prelude) {
  const match = source.match(new RegExp(`function ${name}\\([\\s\\S]*?\\n}`));
  assert.ok(match, `${name} not found`);
  return new Function(`${prelude || ''}; ${match[0]}; return ${name};`)();
}

const COVER_DAYS = 'const ESTOQUE_COVER_DAYS = 7;';
const CSV_CELL = app.match(/const csvCell = [\s\S]*?\n};/)[0];

function constant(source, name, prelude) {
  const match = source.match(new RegExp(`const ${name} = [\\s\\S]*?\\n\\];`));
  assert.ok(match, `${name} not found`);
  return new Function(`${prelude || ''}; ${match[0]}; return ${name};`)();
}

const filters = constant(app, 'ESTOQUE_FILTERS', COVER_DAYS);
const by = (key) => filters.find((f) => f.key === key);
const product = (extra) => Object.assign(
  {id: '1', name: 'X', category: 'C', stock: 10, quantity_sold: 10, revenue: 1000, cost: 500,
   unknown: 0, margin: 50, abc: 'B', current_price: 100, current_cost: 50}, extra);

test('cobertura é o estoque dividido pelo ritmo de venda do período', () => {
  const enrich = extract(app, 'enrichInventory');
  const [p] = enrich([product({stock: 30, quantity_sold: 60})], 30);  // 2/dia
  assert.equal(p.daily_demand, 2);
  assert.equal(p.coverage_days, 15);
  assert.equal(p.stock_value, 1500);  // 30 × R$ 0,50
});

test('sem venda, sem estoque positivo ou sem custo, os derivados ficam vazios', () => {
  const enrich = extract(app, 'enrichInventory');
  const [parado] = enrich([product({stock: 10, quantity_sold: 0, revenue: 0})], 30);
  assert.equal(parado.coverage_days, null);          // não "acaba": está parado
  assert.equal(parado.stock_value, 500);
  const [negativo] = enrich([product({stock: -8})], 30);
  assert.equal(negativo.coverage_days, null);
  assert.equal(negativo.stock_value, null);          // estoque negativo não é dinheiro parado
  const [semCusto] = enrich([product({current_cost: null})], 30);
  assert.equal(semCusto.stock_value, null);
  const [semDias] = enrich([product()], 0);          // período ainda sem nenhum dia de venda
  assert.equal(semDias.coverage_days, null);
});

test('a faixa de cobertura escreve a palavra, não só a cor', () => {
  const band = extract(app, 'coverBand', COVER_DAYS);
  assert.deepEqual(band(null), {cls: '', text: '—', word: ''});
  assert.equal(band(2.5).cls, 'cover-critical');
  assert.equal(band(2.5).text, '2,5 d');
  assert.equal(band(7).cls, 'cover-low');
  assert.equal(band(30).cls, '');
  assert.equal(band(120).cls, 'cover-high');
  assert.equal(band(45).text, '45 d');
  assert.equal(band(5000).text, '999+ d');
  for (const days of [2, 7, 30, 120]) assert.ok(band(days).word, 'toda faixa tem palavra');
});

test('estoque zerado e estoque negativo são filtros diferentes', () => {
  // Juntos, enchiam um só filtro com dois terços do catálogo e a ruptura ficava sem uso.
  assert.equal(by('estoque-zerado').test(product({stock: 0})), true);
  assert.equal(by('estoque-zerado').test(product({stock: -3})), false);
  assert.equal(by('estoque-negativo').test(product({stock: -3})), true);
  assert.equal(by('estoque-negativo').test(product({stock: 0})), false);
});

test('ruptura, preço abaixo do custo e vendido sem custo seguem os alertas do Resumo', () => {
  // Mesmos predicados de backend/api.py dashboard_alerts(): "Ver produtos" tem de listar
  // exatamente o que o alerta contou.
  assert.equal(by('ruptura').test(product({abc: 'A', stock: 0})), true);
  assert.equal(by('ruptura').test(product({abc: 'A', stock: -5})), true);
  assert.equal(by('ruptura').test(product({abc: 'B', stock: 0})), false);
  assert.equal(by('abaixo-custo').test(product({current_price: 40, current_cost: 50})), true);
  assert.equal(by('abaixo-custo').test(product({current_price: null, current_cost: 50})), false);
  assert.equal(by('sem-custo').test(product({unknown: 2})), true);
  assert.equal(by('custo-zero').test(product({cost: 0, revenue: 100})), true);
  assert.equal(by('custo-zero').test(product({cost: 0, revenue: 0})), false);
});

test('acabando e parado separam o que falta comprar do que não sai da prateleira', () => {
  const enrich = extract(app, 'enrichInventory');
  const [acabando] = enrich([product({stock: 6, quantity_sold: 30})], 30);   // 1/dia → 6 dias
  const [confortavel] = enrich([product({stock: 90, quantity_sold: 30})], 30);
  assert.equal(by('acabando').test(acabando), true);
  assert.equal(by('acabando').test(confortavel), false);
  assert.equal(by('parado').test(product({stock: 4, revenue: 0})), true);
  assert.equal(by('parado').test(product({stock: 0, revenue: 0})), false);   // sem estoque não está parado
  assert.equal(by('parado').test(product({stock: 4, revenue: 900})), false);
});

test('cada filtro pertence a um grupo declarado', () => {
  const groups = constant(app, 'ESTOQUE_GROUPS').map((g) => g.key);
  for (const f of filters) assert.ok(groups.includes(f.group), `${f.key} sem grupo`);
});

test('o CSV sai no formato que o Excel pt-BR abre', () => {
  const csv = extract(app, 'estoqueCsv', CSV_CELL);
  const enrich = extract(app, 'enrichInventory');
  const rows = enrich([product({name: 'ARROZ "TIPO 1"; 5KG', stock: 30, quantity_sold: 60})], 30);
  const out = csv(rows);
  const [header, line] = out.split('\r\n');
  assert.ok(out.startsWith('﻿'), 'BOM mantém os acentos ao abrir direto do Downloads');
  assert.match(header, /^﻿Produto;Categoria;Codigo;Estoque;Cobertura \(dias\)/);
  assert.match(line, /^"ARROZ ""TIPO 1""; 5KG";C;1;/);  // aspas e ';' escapados
  assert.ok(line.includes(';15,0;'), 'cobertura com decimal em vírgula');
  assert.ok(line.includes(';0,50;'), 'centavos viram reais com vírgula');
  assert.equal(csv([]).split('\r\n').length, 1);       // só o cabeçalho
});

test('a página mostra uma janela da lista e oferece a lista inteira no CSV', () => {
  const page = app.slice(app.indexOf('function renderEstoque('), app.indexOf('function showMoreEstoque'));
  assert.match(page, /rows\.slice\(0, limit\)/);              // nunca 11 mil linhas no DOM
  assert.match(page, /data-estoque-export/);
  assert.match(page, /data-estoque-more/);
  assert.match(page, /Mostrando \$\{num\(page\.length\)\} de \$\{num\(rows\.length\)\}/);
  assert.match(page, /#\/produto\/\$\{esc\(APP\.period\)\}\?id=\$\{esc\(p\.id\)\}/);
  assert.match(app, /\[data-estoque-more\]'\)\) return showMoreEstoque/);
  assert.match(app, /\[data-estoque-export\]'\)\) return exportEstoqueCsv/);
});

test('a rota carrega filtro, busca e categoria para a visão poder ser reaberta', () => {
  assert.match(app, /params\.set\('busca', q\)/);
  assert.match(app, /params\.set\('categoria', APP\.estoque\.cat\)/);
  assert.match(app, /APP\.estoque\.q = APP\.routeParams\.get\('busca'\)/);
  assert.match(app, /APP\.estoque\.cat = APP\.routeParams\.get\('categoria'\)/);
});

test('a busca também encontra pelo código do produto', () => {
  const view = app.slice(app.indexOf('function estoqueView'), app.indexOf('function renderEstoque('));
  assert.match(view, /fold\(p\.id\)\.includes\(q\)/);
});

test('o estoque fracionado é impresso com uma casa, não com três', () => {
  // num() imprimia 456,799 para 456,8 kg — ao lado de 1.049 unidades, lê-se quatrocentos mil.
  const qty = new Function(`${app.match(/const qty = [\s\S]*?;\n/)[0]} return qty;`)();
  assert.equal(qty(456.799), '456,8');
  assert.equal(qty(1049), '1.049');
  assert.equal(qty(null), '—');
});

/* Mercado duBairro — páginas de inteligência: Preços, Mapa de Produtos,
 * Diagnóstico, Sazonalidade e Visão Futurista. Port of the legacy Plotly
 * pages (legacy/public/js/app.js) onto the reconciled /dashboard payload:
 * every money value is in cents and comes from Mobne PDV sales; stock,
 * price and cost are the current Mobne snapshot. Charts: charts.js. */
'use strict';

const MESES_NOMES = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];
const DIAS_SEMANA = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'];
const DIAS_CURTOS = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'];
// Brand yellow is 1.6:1 on white — fine as a fill, invisible as a line or a bar edge.
// amber (3.6:1) is used for yellow lines and as the outline of yellow bars (WCAG 1.4.11).
const COR = {yellow: '#FFC107', amber: '#B7791F', green: '#27AE60', greenDark: '#1E8449', red: '#E74C3C',
  blue: '#2E86C1', orange: '#F39C12', gray: '#AAAAAA', lightGray: '#BDBDBD'};
const EROSAO_LIMIAR = 3;       // pontos de margem, mesmo corte da versão anterior
const META_MARGEM_REAL = 15;   // % após custo fixo
// Same thresholds as backend/models.py summarize(): giro ≥ 60% dos dias, margem ≥ 35%.
const GIRO_CORTE = 0.6, MARGEM_CORTE = 35;
const CLASSES = [
  {key: 'Estrela', label: '⭐ Estrela', color: COR.green},
  {key: 'Gerador de caixa', label: '💰 Gerador de Caixa', color: COR.yellow},
  {key: 'Oportunidade', label: '🔍 Oportunidade', color: COR.blue},
  {key: 'Baixo giro', label: '⚠️ Peso Morto', color: COR.red},
];

/* ---------------------------------------------------------------- format */

const intBR = new Intl.NumberFormat('pt-BR', {maximumFractionDigits: 0});
const brl = (c) => c == null || !isFinite(c) ? '—' : (c < 0 ? '−' : '') + 'R$ ' + intBR.format(Math.abs(Math.round(c / 100)));
const brlShort = (c) => 'R$' + compactNum(c / 100);
const brlSigned = (c) => (c >= 0 ? '+' : '−') + 'R$ ' + intBR.format(Math.abs(Math.round(c / 100)));
const pct1 = (v) => v == null || !isFinite(v) ? '—' : v.toFixed(1).replace('.', ',') + '%';
const signedPct = (v) => v == null || !isFinite(v) ? '—' : (v >= 0 ? '+' : '') + pct1(v);
const dec2 = (v) => v == null || !isFinite(v) ? '—' : v.toFixed(2).replace('.', ',');
const deltaClass = (v, tol) => v == null ? '' : Math.abs(v) <= (tol == null ? 2 : tol) ? 'kpi-neutral' : v > 0 ? 'kpi-positive' : 'kpi-negative';
const deltaArrow = (v, tol) => v == null ? '' : Math.abs(v) <= (tol == null ? 2 : tol) ? '●' : v > 0 ? '▲' : '▼';
const growth = (a, b) => b ? (a / b - 1) * 100 : null;
const sumBy = (arr, fn) => arr.reduce((s, x) => s + (fn(x) || 0), 0);
const pkey = (y, m) => `${y}-${String(m).padStart(2, '0')}`;
const monthLabel = (y, m) => `${MONTHS[m - 1]}/${String(y).slice(2)}`;
const classInfo = (key) => CLASSES.find((c) => c.key === key) || CLASSES[3];

/* ---------------------------------------------------------------- building blocks */

function periodInfo(data) {
  const [y, m] = data.period.split('-').map(Number);
  return {y, m, yy: String(y).slice(2), nome: MESES_NOMES[m - 1], curto: MONTHS[m - 1], label: monthLabel(y, m),
    partial: !!data.partial_month, endDay: parseInt(data.end.slice(8), 10), daysInMonth: new Date(y, m, 0).getDate()};
}

function insightHeader(icon, title, subtitle, P) {
  const partial = P.partial ? ` · parcial até ${String(P.endDay).padStart(2, '0')}/${String(P.m).padStart(2, '0')}` : '';
  return `<div class="page-title">${icon} ${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <span class="periodo-badge">📅 Analisando: ${P.nome}/${P.y}${partial}</span>
    <hr class="divider">`;
}

function kpi(title, value, subtitle, subCls, valueCls) {
  return `<div class="kpi-card"><div class="kpi-title">${title}</div><div class="kpi-value ${valueCls || ''}">${value}</div>${
    subtitle ? `<div class="kpi-subtitle ${subCls || ''}">${subtitle}</div>` : ''}</div>`;
}

function story(text) { return `<div class="story-box">💡 ${text}</div>`; }
function section(text) { return `<div class="section-header">${text}</div>`; }
function chartBox(inner) { return `<div class="chart-container">${inner}</div>`; }

function explain(title, what, how, why, example) {
  return `<div class="tooltip-box">
    <button type="button" class="tooltip-toggle" data-explain>💡 Como interpretar: ${title}</button>
    <div class="tooltip-content">
      <p><strong>O que mostra:</strong> ${what}</p>
      <p><strong>Como ler:</strong> ${how}</p>
      <p><strong>Por que importa:</strong> ${why}</p>${example ? `<p><strong>Exemplo prático:</strong> ${example}</p>` : ''}
    </div></div>`;
}

function table(headers, rows) {
  const body = rows.length
    ? rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('')
    : `<tr><td colspan="${headers.length}">Nenhum item.</td></tr>`;
  return `<div class="data-table-container"><table class="data-table"><thead><tr>${headers.map((h) => `<th>${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/* Charts are drawn at the pixel width they will occupy so text stays legible. */
function chartWidth(frac) {
  const el = document.getElementById('content');
  const inner = (el && el.clientWidth ? el.clientWidth : 1100) - 64;
  if (window.innerWidth <= 900) frac = 1;
  return Math.max(340, Math.round(inner * frac - (frac < 1 ? 20 : 0) - 16));
}

function zeroCostBanner(data) {
  const zero = (data.products || []).filter((p) => p.cost === 0 && p.revenue > 0);
  if (!zero.length) return '';
  return `<div class="disclosure-banner warn">⚠️ ${num(zero.length)} produto${zero.length === 1 ? '' : 's'} vendido${zero.length === 1 ? '' : 's'}
    com custo zero no Mobne (${money(sumBy(zero, (p) => p.revenue))} de receita). A margem desses itens aparece como 100% e infla
    os indicadores — revise o custo no cadastro do Mobne.</div>`;
}

/* Current Mobne cost snapshot: average cost vs last-entry cost, at today's price. */
function erosionRows(data) {
  return (data.inventory || [])
    .filter((p) => p.abc === 'A' && p.current_price > 0 && p.current_cost > 0 && p.last_cost > 0)
    .map((p) => {
      const md = (p.current_price - p.current_cost) / p.current_price * 100;
      const mdu = (p.current_price - p.last_cost) / p.current_price * 100;
      return Object.assign({}, p, {md, mdu, erosao: md - mdu});
    });
}

function timelineMap(data) {
  const map = new Map();
  (data.timeline || []).forEach((t) => map.set(t.period, t));
  return map;
}

/* Seasonality of the previous year applied to the selected year. */
function seasonalModel(data) {
  const P = periodInfo(data), tl = timelineMap(data), R = P.y - 1;
  const get = (y, m) => { const t = tl.get(pkey(y, m)); return t && t.revenue > 0 ? t : null; };
  const ref = [], cur = [];
  for (let m = 1; m <= 12; m++) { ref.push(get(R, m)); cur.push(get(P.y, m)); }
  const refVals = ref.filter((t) => t && !t.partial);
  const refAvg = refVals.length ? sumBy(refVals, (t) => t.revenue) / refVals.length : null;
  const index = ref.map((t) => t && !t.partial && refAvg ? t.revenue / refAvg : null);
  let curSum = 0, refSum = 0, pairs = 0;
  cur.forEach((t, i) => {
    if (t && !t.partial && ref[i] && !ref[i].partial) { curSum += t.revenue; refSum += ref[i].revenue; pairs++; }
  });
  const factor = refSum ? curSum / refSum : null;
  const projection = ref.map((t) => t && !t.partial && factor != null ? t.revenue * factor : null);
  return {P, R, ref, cur, refVals, refAvg, index, factor, pairs, projection};
}

/* Month after the selected one: seasonal projection, else current pace. */
function nextMonthProjection(data, S) {
  const P = S.P;
  const m = P.m < 12 ? P.m + 1 : 1, y = P.m < 12 ? P.y : P.y + 1;
  let value = null, basis = 'sazonal';
  if (y === P.y) value = S.projection[m - 1];
  else if (S.cur[0] && S.factor != null) value = S.cur[0].revenue * S.factor;
  if (value == null) {
    value = P.partial ? data.totals.revenue / P.endDay * P.daysInMonth : data.totals.revenue;
    basis = 'ritmo';
  }
  return {m, y, nome: MESES_NOMES[m - 1], curto: MONTHS[m - 1], label: monthLabel(y, m), value, basis};
}

/* ================================================================ PÁGINA: INTELIGÊNCIA DE PREÇOS */

function renderPrecos(data) {
  const P = periodInfo(data), t = data.totals;
  const curvaA = (data.inventory || []).filter((p) => p.abc === 'A');
  const eros = erosionRows(data);
  const subiu = eros.filter((r) => r.erosao > EROSAO_LIMIAR).sort((a, b) => b.erosao - a.erosao);
  const caiu = eros.filter((r) => r.erosao < -EROSAO_LIMIAR).sort((a, b) => a.erosao - b.erosao);
  const low = curvaA.filter((p) => p.margin != null && p.margin < MARGEM_CORTE);
  const oport = sumBy(low, (p) => p.revenue) * 0.05;
  const mdm = t.margin;

  const cap = curvaA.filter((p) => p.revenue > 5000 && p.margin != null);
  const rOf = bubbleRadius(cap.map((p) => p.profit), 4, 18);
  const avgRev = cap.length ? sumBy(cap, (p) => p.revenue) / cap.length : 0;
  const maxRev = Math.max(0, ...cap.map((p) => p.revenue));
  const scatterOpts = {
    points: cap.map((p) => ({x: p.revenue, y: p.margin, r: rOf(p.profit), g: CLASSES.indexOf(classInfo(p.classification)),
      tip: `${p.name}\nReceita: ${money(p.revenue)}\nMargem: ${pct1(p.margin)}\nLucro: ${money(p.profit)}\n${classInfo(p.classification).label}`})),
    groups: CLASSES.map((c) => ({name: c.label, color: c.color})),
    xScale: niceScale(0, maxRev * 1.05, 6),
    xTitle: 'Faturamento no período (R$)', yTitle: 'Margem (%)', xFmt: brlShort, yFmt: (v) => v + '%',
    vlines: [{x: avgRev, label: `Receita média: ${brlShort(avgRev)}`}],
    hlines: mdm == null ? [] : [{y: mdm, label: `Margem média: ${pct1(mdm)}`}],
    empty: 'Nenhum produto Curva A com custo conhecido no período.',
  };

  const cats = (data.categories || []).filter((c) => c.revenue > 0)
    .sort((a, b) => (b.margin == null ? -1e9 : b.margin) - (a.margin == null ? -1e9 : a.margin));
  const catRows = cats.map((c) => [c.margin == null ? '⚪' : c.margin > 55 ? '🟢' : c.margin > 40 ? '🟡' : '🔴',
    esc(c.name), brl(c.revenue), pct1(c.margin)]);

  const erosTable = (rows) => table(['Produto', 'Faturamento', 'Margem %', 'Preço atual', 'Custo médio', 'Custo últ. entrada',
    'Markdown Atual', 'Markdown Últ. Entrada', 'Erosão (pts)'],
  rows.map((r) => [esc(r.name), money(r.revenue), pct1(r.margin), money(r.current_price), money(r.current_cost),
    money(r.last_cost), pct1(r.md), pct1(r.mdu), `${r.erosao >= 0 ? '+' : ''}${dec2(r.erosao)} pts`]));

  document.getElementById('content').innerHTML = `
    ${insightHeader('💰', 'Inteligência de Preços', 'Onde estou deixando dinheiro na mesa?', P)}
    ${zeroCostBanner(data)}
    <div class="kpi-grid kpi-grid-3">
      ${kpi('Markdown Médio Ponderado', pct1(mdm), mdm == null ? 'Custo incompleto no período'
        : `De cada R$ 1 vendido, R$ ${dec2(mdm / 100)} é margem`)}
      ${kpi('Produtos com Custo Subindo', num(subiu.length), 'Curva A com erosão detectada', 'kpi-negative')}
      ${kpi('Oportunidade Estimada', `${brl(oport)}/mês`, `${low.length} produtos Curva A com margem < ${MARGEM_CORTE}%`, 'kpi-neutral')}
    </div>
    ${explain('KPIs de Preços', 'Markdown = margem bruta (lucro ÷ venda). Erosão = custo da última entrada acima do custo médio, sem reajuste de preço.',
      'Markdown alto = saudável. Custo subindo = alerta de margem futura.', 'Proteger a margem é proteger o lucro.',
      `${subiu.length} produtos Curva A precisam de revisão de preço. A oportunidade estimada é 5% da receita dos produtos Curva A com margem abaixo de ${MARGEM_CORTE}%.`)}
    ${story(`Margem média: ${pct1(mdm)}. ${subiu.length} produtos Curva A com custo subindo — reajustar para evitar erosão.`)}
    <hr class="divider">

    <div class="row">
      <div class="col-60">
        ${section(`Duelo de Produtos (${P.label})`)}
        <div class="chart-container chart-h-460" id="chart-precos-scatter"></div>
        ${explain('Scatter Plot de Preços', 'Cada bolha = produto Curva A. X = faturamento. Y = margem. Tamanho = lucro. Clique na legenda para filtrar.',
          'Superior direito = melhor. Inferior direito = vende mas não lucra. Passe o mouse para ver detalhes.', 'Identifica onde reajustar preço.')}
      </div>
      <div class="col-40">
        ${section(`Ranking Margem por Categoria (${P.label})`)}
        ${table(['Status', 'Categoria', 'Fat.', 'Markdown'], catRows)}
        ${explain('Ranking por Categoria', `${cats.length} categorias ordenadas por margem. 🟢 > 55% · 🟡 40–55% · 🔴 < 40%.`,
          'Categorias 🔴 com alto faturamento são as mais urgentes.', 'Renegociar fornecedores ou reajustar preços.')}
      </div>
    </div>

    ${section(`🚨 Alerta de Erosão — Curva A (${P.label})`)}
    <p class="section-note">Produtos em que o custo da última entrada difere do custo médio em mais de ${EROSAO_LIMIAR} pontos de margem,
      ao preço atual. Custos e preços: retrato atual do Mobne (${dt(data.stock_updated_at)}).</p>
    <div class="tabs">
      <button type="button" class="tab-btn active" data-tab="tab-subiu">🔴 Custo Subiu (${subiu.length})</button>
      <button type="button" class="tab-btn" data-tab="tab-caiu">🟢 Custo Caiu (${caiu.length})</button>
    </div>
    <div id="tab-subiu" class="tab-content active">${subiu.length
      ? erosTable(subiu) + story(`${subiu.length} produtos com custo subindo. Reajustar preço para proteger margem futura.`)
      : '<div class="badge-success">Nenhum produto com custo subindo!</div>'}</div>
    <div id="tab-caiu" class="tab-content">${caiu.length
      ? erosTable(caiu) + story(`${caiu.length} produtos com custo caindo. Mantenha o preço para aumentar a margem!`)
      : '<div class="badge-info">Nenhum produto com custo caindo.</div>'}</div>
    ${explain('Erosão de Margem', 'Compara o markdown calculado com o custo médio vs com o custo da última entrada.',
      'Positivo = custo subiu (ruim). Negativo = custo caiu (bom).', 'Alerta antecipado do que VAI acontecer com a margem.')}
  `;
  mountEchartScatter(document.getElementById('chart-precos-scatter'), scatterOpts);
}

/* ================================================================ PÁGINA: MAPA DE PRODUTOS */

function renderMapa(data) {
  const P = periodInfo(data), prods = data.products || [];
  const [est, ger, opo, pm] = CLASSES.map((c) => prods.filter((p) => p.classification === c.key));
  const lucro = (arr) => sumBy(arr, (p) => p.profit);
  const lt = lucro(prods);
  let acc = 0, n80 = 0;
  if (lt > 0) {
    for (const p of prods.slice().sort((a, b) => (b.profit || 0) - (a.profit || 0))) {
      acc += p.profit || 0; n80++;
      if (acc >= lt * 0.8) break;
    }
  }

  const pp = prods.filter((p) => p.revenue > 2000 && p.margin != null);
  const rOf = bubbleRadius(pp.map((p) => p.revenue), 3, 17);
  const margins = pp.map((p) => p.margin);
  const ys = niceScale(Math.min(0, ...margins), Math.max(MARGEM_CORTE + 10, ...margins), 6);
  const span = ys.max - ys.min;
  const groups = CLASSES.map((c) => ({name: c.label, color: c.key === 'Baixo giro' ? COR.lightGray : c.color}));
  const matrixOpts = {
    points: pp.map((p) => ({x: p.turnover, y: p.margin, r: rOf(p.revenue), g: CLASSES.indexOf(classInfo(p.classification)),
      tip: `${p.name}\nGiro: ${Math.round(p.turnover * 100)}% dos dias (${p.days_sold} dias)\nMargem: ${pct1(p.margin)}\nReceita: ${money(p.revenue)}\nLucro: ${money(p.profit)}`})),
    groups,
    xScale: {min: -0.05, max: 1.05}, yScale: {min: ys.min, max: ys.max},
    xTitle: 'Giro (% dos dias com venda)', yTitle: 'Margem (%)', xFmt: (v) => Math.round(v * 100) + '%', yFmt: (v) => v + '%',
    vlines: [{x: GIRO_CORTE}], hlines: [{y: MARGEM_CORTE}],
    annotations: [
      {x: 0.83, y: ys.max - span * 0.05, text: '⭐ ESTRELAS', color: COR.green},
      {x: 0.83, y: ys.min + span * 0.05, text: '💰 GERADORES', color: COR.orange},
      {x: 0.27, y: ys.max - span * 0.05, text: '🔍 OPORTUNIDADES', color: COR.blue},
      {x: 0.27, y: ys.min + span * 0.05, text: '⚠️ PESO MORTO', color: COR.red},
    ],
    empty: 'Nenhum produto com custo conhecido no período.',
  };

  const rowsOf = (arr) => arr.map((p) => [esc(p.name), p.days_sold, pct1(p.margin), money(p.revenue), money(p.profit)]);
  const heads = ['Produto', 'Dias', 'Margem %', 'Receita', 'Lucro'];
  const byProfit = (arr) => arr.slice().sort((a, b) => (b.profit || 0) - (a.profit || 0));
  const byRevenue = (arr) => arr.slice().sort((a, b) => b.revenue - a.revenue);

  document.getElementById('content').innerHTML = `
    ${insightHeader('🗺️', 'Mapa de Produtos — Matriz de Rentabilidade', 'Quais produtos são estrelas e quais são peso morto?', P)}
    ${zeroCostBanner(data)}
    <div class="kpi-grid kpi-grid-4">
      ${kpi('⭐ Estrelas', num(est.length), `${brl(lucro(est))} lucro`)}
      ${kpi('💰 Geradores', num(ger.length), `${brl(lucro(ger))} lucro`)}
      ${kpi('🔍 Oportunidades', num(opo.length), `${brl(lucro(opo))} lucro`)}
      ${kpi('⚠️ Peso Morto', num(pm.length), `${brl(lucro(pm))} lucro`)}
    </div>
    ${explain('Matriz 2×2', `Giro × Margem. ⭐ giro alto e margem alta · 💰 giro alto e margem baixa · 🔍 giro baixo e margem alta · ⚠️ giro baixo e margem baixa. Cortes: giro ≥ ${GIRO_CORTE * 100}% dos dias com venda e margem ≥ ${MARGEM_CORTE}%.`,
      '⭐ Proteger · 💰 Renegociar custo · 🔍 Dar visibilidade · ⚠️ Avaliar remoção.', 'Permite priorizar decisões sobre cada grupo.',
      `Apenas ${n80} de ${num(prods.length)} produtos geram 80% do lucro.`)}
    ${story(`Apenas ${n80} produtos (de ${num(prods.length)}) geram 80% do lucro. As ${est.length} Estrelas são intocáveis.`)}
    <hr class="divider">

    ${section(`Matriz de Rentabilidade (${P.label})`)}
    <div class="chart-container chart-h-520" id="chart-mapa-matrix"></div>
    ${explain('Scatter Plot Giro vs Margem', 'Cada bolha = produto. X = giro. Y = margem. Tamanho = faturamento.',
      'Superior direito = ⭐. Inferior direito = 💰. Passe o mouse para ver detalhes; clique na legenda para filtrar grupos.',
      'Ferramenta principal para decisões de mix.')}

    <div class="row">
      <div class="col-50">
        ${section('⭐ Estrelas')}
        ${table(heads, rowsOf(byProfit(est)))}
        ${section('🔍 Oportunidades (Top 15)')}
        ${table(heads, rowsOf(byProfit(opo).slice(0, 15)))}
      </div>
      <div class="col-50">
        ${section('💰 Geradores de Caixa')}
        ${table(heads, rowsOf(byRevenue(ger)))}
        ${section('⚠️ Peso Morto (Top 15)')}
        ${table(heads, rowsOf(byRevenue(pm).slice(0, 15)))}
      </div>
    </div>
  `;
  mountEchartScatter(document.getElementById('chart-mapa-matrix'), matrixOpts);
}

/* ================================================================ PÁGINA: DIAGNÓSTICO DE FATURAMENTO */

function renderDiagnostico(data) {
  const P = periodInfo(data), t = data.totals, cmp = data.comparison;
  const fat = t.revenue, cup = t.receipts, tk = t.ticket;
  const prevLabel = cmp ? monthLabel(Number(cmp.period.slice(0, 4)), P.m) : null;
  let vc = null, vt = null;
  let diag = kpi('Diagnóstico', '—', 'Sem dados do mesmo mês do ano anterior');
  if (cmp && cmp.totals.receipts) {
    const c0 = cmp.totals.receipts, t0 = cmp.totals.ticket;
    vc = growth(cup, c0); vt = growth(tk, t0);
    const ic = (cup - c0) * t0, it = (tk - t0) * cup;
    const [who, effect] = Math.abs(ic) >= Math.abs(it) ? ['Fluxo', ic] : ['Ticket', it];
    diag = kpi('Diagnóstico', `${who} ${effect >= 0 ? '▲' : '▼'}`, `Cupons: ${brlSigned(ic)} | Ticket: ${brlSigned(it)}`,
      '', effect >= 0 ? 'kpi-positive' : 'kpi-negative');
  }
  const vsTxt = prevLabel ? ` vs ${prevLabel}` : '';

  const topCats = (data.categories || []).slice().sort((a, b) => b.revenue - a.revenue).slice(0, 12);
  const contribOpts = {
    rotate: true,
    labels: topCats.map((c) => c.name.length > 22 ? c.name.slice(0, 21) + '…' : c.name),
    tipLabels: topCats.map((c) => c.name),
    series: [{name: 'Faturamento', type: 'bar', values: topCats.map((c) => c.revenue), fmt: money, labels: true, labelFmt: brlShort,
      colors: topCats.map((c) => c.profit == null ? COR.gray : c.profit > 0 ? COR.green : COR.red),
      tips: topCats.map((c) => `Lucro: ${money(c.profit)} · Margem: ${pct1(c.margin)}`)}],
    yFmt: brlShort, yTitle: 'Faturamento (R$)', empty: 'Sem categorias no período.',
  };

  // Weekday × week-of-month heatmap (Monday-first weeks)
  const daily = data.daily || [];
  const firstDow = (new Date(P.y, P.m - 1, 1).getDay() + 6) % 7;
  const dayInfo = daily.map((d) => {
    const day = parseInt(d.date.slice(8), 10);
    const dow = (new Date(P.y, P.m - 1, day).getDay() + 6) % 7;
    return Object.assign({}, d, {day, dow, week: Math.floor((day - 1 + firstDow) / 7)});
  });
  const weeks = [...new Set(dayInfo.map((d) => d.week))].sort((a, b) => a - b);
  const dowPresent = DIAS_CURTOS.map((_, i) => i).filter((i) => dayInfo.some((d) => d.dow === i));
  const heatOpts = {
    rows: weeks.map((w) => `Sem ${w + 1}`), cols: dowPresent.map((i) => DIAS_CURTOS[i]),
    cells: dayInfo.map((d) => ({r: weeks.indexOf(d.week), c: dowPresent.indexOf(d.dow), value: d.revenue,
      text: brlShort(d.revenue),
      tip: `${String(d.day).padStart(2, '0')}/${String(P.m).padStart(2, '0')} (${DIAS_SEMANA[d.dow]})\nFaturamento: ${money(d.revenue)}\nCupons: ${num(d.receipts)}\nMargem: ${pct1(d.margin)}`})),
    empty: 'Sem vendas diárias no período.',
  };

  const byDow = DIAS_SEMANA.map((nome, i) => {
    const days = dayInfo.filter((d) => d.dow === i);
    return {nome, curto: DIAS_CURTOS[i], dias: days.length, media: days.length ? sumBy(days, (d) => d.revenue) / days.length : null};
  }).filter((d) => d.dias);
  const best = byDow.length ? byDow.reduce((a, b) => (a.media > b.media ? a : b)) : null;
  const worst = byDow.length ? byDow.reduce((a, b) => (a.media < b.media ? a : b)) : null;
  const dowChartOpts = {
    labels: byDow.map((d) => d.nome),
    series: [{name: 'Faturamento médio', type: 'bar', values: byDow.map((d) => d.media), fmt: money, labels: true, labelFmt: brl,
      colors: byDow.map((d) => d.nome === 'Domingo' ? COR.red : COR.yellow),
      strokes: byDow.map((d) => d.nome === 'Domingo' ? null : COR.amber), tips: byDow.map((d) => `${d.dias} dia(s) com venda`)}],
    yFmt: brlShort, yTitle: 'Fat. médio (R$)',
  };

  document.getElementById('content').innerHTML = `
    ${insightHeader('🔍', 'Diagnóstico de Faturamento', 'Menos clientes, menos gasto, ou mix mudou?', P)}
    <div class="kpi-grid kpi-grid-4">
      ${kpi('FATURAMENTO =', brl(fat), 'Cupons × Ticket Médio')}
      ${kpi('Nº Cupons', num(cup), vc == null ? 'Sem comparação' : `${deltaArrow(vc)} ${signedPct(vc)}${vsTxt}`, deltaClass(vc))}
      ${kpi('× Ticket Médio', money(tk), vt == null ? 'Sem comparação' : `${deltaArrow(vt)} ${signedPct(vt)}${vsTxt}`, deltaClass(vt))}
      ${diag}
    </div>
    ${explain('Decomposição do Faturamento', 'FAT = Cupons × Ticket. Se caiu, ou veio menos gente ou cada cliente gastou menos.',
      "'Fluxo' = efeito do número de clientes. 'Ticket' = efeito do gasto por cliente. A seta indica se ajudou (▲) ou atrapalhou (▼).",
      'Fluxo → marketing/fachada. Ticket → cross-selling/mix.',
      cmp ? `A comparação usa os mesmos ${P.partial ? P.endDay : P.daysInMonth} primeiros dias de ${prevLabel}.` : undefined)}
    ${story(vc == null ? `Faturamento = ${num(cup)} cupons × ${money(tk)}. Não há vendas sincronizadas do mesmo mês do ano anterior para comparar.`
      : `Faturamento = ${num(cup)} cupons × ${money(tk)}. Fluxo variou ${signedPct(vc)} e ticket variou ${signedPct(vt)}${vsTxt}.`)}
    <hr class="divider">

    <div class="row">
      <div class="col-50">
        ${section(`Contribuição por Categoria (${P.label})`)}
        <div class="chart-container chart-h-380" id="chart-diag-contrib"></div>
        ${explain('Contribuição por Categoria', 'Top 12 categorias por faturamento. Verde = lucro positivo, vermelho = prejuízo.',
          'Barras mais altas = mais faturamento. Passe o mouse para ver lucro e margem.', 'Identifica os motores do faturamento.')}
      </div>
      <div class="col-50">
        ${section(`Heatmap por Dia (${P.label})`)}
        <div class="chart-container chart-h-380" id="chart-diag-heat"></div>
        ${explain('Heatmap Semanal', `Faturamento de cada dia de ${P.nome}/${P.y}, por semana do mês.`,
          'Cores quentes = dias fortes. Frias = fracos. Cinza = sem venda.', 'Identifica padrões semanais e dias atípicos.')}
      </div>
    </div>

    ${section(`Faturamento Médio por Dia da Semana (${P.label})`)}
    <div class="chart-container chart-h-290" id="chart-diag-dow"></div>
    ${explain('Faturamento por Dia da Semana', `Média diária em ${P.nome}/${P.y}. Domingo em vermelho.`,
      'Barras altas = dias fortes. Use para planejar estoque e escala.', 'Promoções nos dias fracos, reforço nos fortes.')}
    ${best ? story(`${best.nome} é o dia mais forte (${brl(best.media)} em média), ${worst.nome} o mais fraco (${brl(worst.media)}). Promoções para ${worst.nome}, reforço de estoque para ${best.nome}.`) : ''}
  `;
  mountEchartCombo(document.getElementById('chart-diag-contrib'), contribOpts);
  mountEchartHeatmap(document.getElementById('chart-diag-heat'), heatOpts);
  mountEchartCombo(document.getElementById('chart-diag-dow'), dowChartOpts);
}

/* ================================================================ PÁGINA: SAZONALIDADE E TENDÊNCIAS */

function rolling12(data) {
  const list = (data.timeline || []).filter((t) => !t.partial);
  const start = list.findIndex((t) => t.revenue > 0);
  if (start < 0) return [];
  const seq = list.slice(start), out = [];
  for (let i = 11; i < seq.length; i++) {
    let s = 0;
    for (let j = i - 11; j <= i; j++) s += seq[j].revenue;
    const [y, m] = seq[i].period.split('-').map(Number);
    out.push({label: monthLabel(y, m), value: s});
  }
  return out;
}

function renderSazonalidade(data) {
  const S = seasonalModel(data), P = S.P, R = S.R, ry = String(R).slice(2);
  const refRev = sumBy(S.refVals, (t) => t.revenue);
  const refProfit = sumBy(S.refVals, (t) => t.profit);
  const curMonths = S.cur.filter(Boolean);
  const curRev = sumBy(curMonths, (t) => t.revenue);
  const partialIdx = S.cur.findIndex((t) => t && t.partial);
  const closed = S.cur.map((t, i) => (t && !t.partial ? i : -1)).filter((i) => i >= 0);
  const last = closed.length ? closed[closed.length - 1] : -1;
  let proj = null;
  if (last >= 0 && last < 11 && S.ref[last] && S.ref[last + 1]) {
    const sf = S.ref[last + 1].revenue / S.ref[last].revenue;
    proj = {value: S.cur[last].revenue * sf, sf, next: last + 1, last};
  }
  const refTag = S.refVals.length === 12 ? '(Completo)' : S.refVals.length ? `(${S.refVals.length} meses)` : '(sem dados)';
  const refMonths = S.ref.map((t, i) => (t ? i : -1)).filter((i) => i >= 0);
  const refNote = S.refVals.length && S.refVals.length < 12
    ? `<div class="disclosure-banner">ℹ️ ${R} tem vendas sincronizadas só de ${MONTHS[refMonths[0]]} a ${MONTHS[refMonths[refMonths.length - 1]]}
       (${S.refVals.length} meses). Médias e índices de sazonalidade usam apenas esses meses.</div>` : '';

  const sazOpts = {
    labels: MONTHS, tipLabels: MESES_NOMES,
    series: [
      {name: `${R}`, type: 'line', values: S.ref.map((t) => (t ? t.revenue : null)), color: COR.gray, fmt: money, labels: true, labelFmt: brlShort},
      {name: `${P.y} (real)`, type: 'line', values: S.cur.map((t) => (t && !t.partial ? t.revenue : null)), color: COR.amber,
        marker: 'diamond', markerSize: 7, width: 3, fmt: money, labels: true, labelPos: 'bottom', labelFmt: brlShort},
      partialIdx >= 0 ? {name: `${MONTHS[partialIdx]}/${P.yy} (parcial)`, type: 'line', values: S.cur.map((t) => (t && t.partial ? t.revenue : null)),
        color: COR.orange, marker: 'diamond', markerSize: 7, fmt: money, tips: S.cur.map((t) => (t && t.partial ? `Dados até ${t.end.slice(8)}/${t.end.slice(5, 7)}` : ''))} : null,
    ],
    hlines: S.refAvg ? [{value: S.refAvg, label: `Média ${R}: ${brlShort(S.refAvg)}`, color: '#CCCCCC', dash: '2 4'}] : [],
    yFmt: brlShort, yTitle: 'Faturamento (R$)',
  };
  const idxOpts = {
    labels: MONTHS, tipLabels: MESES_NOMES, yMin: 0,
    series: [{name: 'Índice', type: 'bar', values: S.index, fmt: dec2, labels: true,
      colors: S.index.map((v) => (v != null && v > 1 ? COR.green : COR.red)),
      tips: S.ref.map((t) => (t ? `Faturamento: ${money(t.revenue)}` : ''))}],
    hlines: [{value: 1, color: '#999', dash: '1 0', width: 2}],
    yFmt: dec2, yTitle: 'Índice (1,00 = média)', empty: `Sem vendas de ${R} para calcular o índice.`,
  };

  const skuRef = S.ref.map((t) => (t ? t.products : null)), skuCur = S.cur.map((t) => (t && !t.partial ? t.products : null));
  const mixOpts = {
    labels: MONTHS, tipLabels: MESES_NOMES,
    series: [
      {name: `SKUs ${R}`, type: 'line', values: skuRef, color: COR.gray, fmt: num, labels: true},
      {name: `SKUs ${P.y}`, type: 'line', values: skuCur, color: COR.blue, fmt: num, labels: true, labelPos: 'bottom'},
    ],
    yFmt: num, yTitle: 'Nº SKUs vendidos',
  };
  const mixBase = skuCur.filter((v) => v != null).length >= 2 ? {vals: skuCur, year: P.y} : {vals: skuRef, year: R};
  const mixIdx = mixBase.vals.map((v, i) => (v != null ? i : -1)).filter((i) => i >= 0);
  const mixStory = mixIdx.length >= 2 ? (() => {
    const a = mixBase.vals[mixIdx[0]], b = mixBase.vals[mixIdx[mixIdx.length - 1]];
    return {a, b, from: MONTHS[mixIdx[0]], to: MONTHS[mixIdx[mixIdx.length - 1]], year: mixBase.year};
  })() : null;

  const rol = rolling12(data);
  const trend = rol.length >= 2 ? growth(rol[rol.length - 1].value, rol[0].value) : null;
  const rollOpts = {
    labels: rol.map((r) => r.label),
    series: [{name: 'Faturamento 12 meses', type: 'line', values: rol.map((r) => r.value), color: COR.blue, fill: true, width: 3, fmt: money}],
    yFmt: brlShort, yTitle: 'Fat. acumulado 12m (R$)',
    empty: 'São necessários 12 meses completos de vendas sincronizadas para a média móvel.',
  };

  document.getElementById('content').innerHTML = `
    <div class="page-title">📈 Sazonalidade e Tendências</div>
    <div class="page-subtitle">Padrão de ${R} para planejar ${P.y}</div>
    <hr class="divider">
    ${refNote}
    <div class="kpi-grid kpi-grid-4">
      ${kpi(`Faturamento ${R} ${refTag}`, brl(refRev), S.refAvg ? `Média: ${brl(S.refAvg)}/mês` : 'Sem vendas sincronizadas')}
      ${kpi(`Lucro ${R} ${refTag}`, brl(refProfit), `Margem: ${pct1(refRev ? refProfit / refRev * 100 : null)}`)}
      ${kpi(`Acumulado ${P.y}`, brl(curRev), `${curMonths.length} mês(es)${partialIdx >= 0 ? ' · inclui mês em andamento' : ''}`)}
      ${proj ? kpi(`Projeção ${MONTHS[proj.next]}/${P.yy}`, brl(proj.value), `${MONTHS[proj.next]}/${ry} foi ${signedPct((proj.sf - 1) * 100)} vs ${MONTHS[proj.last]}/${ry}`)
        : kpi('Projeção próximo mês', '—', 'Dados insuficientes')}
    </div>
    ${explain('KPIs Sazonalidade', `${R} (referência) + ${P.y} (realizado) + projeção.`,
      `A projeção usa o padrão sazonal: se ${proj ? MONTHS[proj.next] : 'o próximo mês'}/${ry} variou X% vs ${proj ? MONTHS[proj.last] : 'o mês anterior'}/${ry}, aplica a mesma variação sobre ${proj ? MONTHS[proj.last] : 'o último mês fechado'}/${P.yy}.`,
      'Planejar compras, estoque e caixa.')}
    <hr class="divider">

    <div class="row">
      <div class="col-60">
        ${section(`Sazonalidade — ${R} vs ${P.y}`)}
        <div class="chart-container chart-h-400" id="chart-saz-saz"></div>
        ${explain(`Sazonalidade ${R} vs ${P.y}`, `Cinza = ${R}. Losangos amarelos = ${P.y} (meses fechados). Laranja = mês em andamento. Linha pontilhada = média de ${R}.`,
          `Compare o losango de ${P.y} com o ponto do MESMO mês de ${R}.`, `${R} mostra o padrão do ano.`)}
      </div>
      <div class="col-40">
        ${section(`Índice de Sazonalidade — ${R}`)}
        <div class="chart-container chart-h-400" id="chart-saz-idx"></div>
        ${explain('Índice de Sazonalidade', `Cada barra = faturamento do mês ÷ média mensal de ${R}. 1,00 = exatamente na média.`,
          'Verde (> 1,00) = mês forte. Vermelho (< 1,00) = mês fraco.', `Prever meses fortes e fracos de ${P.y}.`)}
      </div>
    </div>

    ${section('Mix de Produtos — SKUs vendidos por mês')}
    <div class="chart-container chart-h-290" id="chart-saz-mix"></div>
    ${explain('Evolução do Mix', 'Quantidade de produtos diferentes vendidos em cada mês (o mês em andamento fica de fora).',
      'Linha descendo = menos variedade na prateleira.', 'Menos produtos = menos motivos para o cliente voltar.')}
    ${mixStory ? story(`Mix ${mixStory.b >= mixStory.a ? 'cresceu' : 'encolheu'} de ${num(mixStory.a)} para ${num(mixStory.b)} SKUs entre ${mixStory.from} e ${mixStory.to}/${String(mixStory.year).slice(2)} (${mixStory.b - mixStory.a >= 0 ? '+' : ''}${num(mixStory.b - mixStory.a)}).`) : ''}

    ${section('Tendência — 12 Meses Móveis')}
    <div class="chart-container chart-h-290" id="chart-saz-roll"></div>
    ${explain('12 Meses Móveis', 'Soma dos últimos 12 meses fechados em cada ponto. Elimina a sazonalidade.',
      'Subindo = negócio crescendo. Descendo = encolhendo.', 'Melhor indicador de tendência real.')}
    ${rol.length ? story(`Faturamento 12m: ${brl(rol[rol.length - 1].value)}.${trend == null ? '' : ` Tendência ${trend > 0 ? 'subindo' : 'caindo'} (${signedPct(trend)} desde ${rol[0].label}).`}`) : ''}
  `;
  mountEchartCombo(document.getElementById('chart-saz-saz'), sazOpts);
  mountEchartCombo(document.getElementById('chart-saz-idx'), idxOpts);
  mountEchartCombo(document.getElementById('chart-saz-mix'), mixOpts);
  mountEchartCombo(document.getElementById('chart-saz-roll'), rollOpts);
}

/* ================================================================ PÁGINA: VISÃO FUTURISTA */

function renderVisao(data) {
  const S = seasonalModel(data), P = S.P, t = data.totals;
  const fixed = data.fixed_cost_cents;
  const fat = t.revenue, mg = t.margin;
  const nx = nextMonthProjection(data, S);
  const cp = nx.value * 0.85, cr = nx.value, co = nx.value * 1.15;
  const lucroDe = (v) => (mg == null ? null : v * mg / 100 - fixed);
  const [lp, lr, lo] = [cp, cr, co].map(lucroDe);
  const pace = P.partial ? fat / P.endDay * P.daysInMonth : fat;

  const closedReal = S.cur.map((c) => (c && !c.partial ? c.revenue : null));
  const projVals = S.projection.map((v, i) => (closedReal[i] == null ? v : null));
  const partialVals = S.cur.map((c) => (c && c.partial ? c.revenue : null));
  const projOpts = {
    barMode: 'overlay', labels: MONTHS, tipLabels: MESES_NOMES,
    series: [
      {name: `${P.y} (real)`, type: 'bar', values: closedReal, color: COR.yellow, stroke: COR.amber, fmt: money, labels: true, labelFmt: brlShort},
      {name: `${P.y} (projeção)`, type: 'bar', values: projVals, color: COR.yellow, opacity: 0.35, stroke: COR.amber, fmt: money, labels: true, labelFmt: brlShort},
      partialVals.some((v) => v != null) ? {name: `${P.y} (em andamento)`, type: 'bar', values: partialVals, color: COR.orange, opacity: 0.85, fmt: money} : null,
      {name: `${S.R} (referência)`, type: 'line', values: S.ref.map((c) => (c ? c.revenue : null)), color: '#BBBBBB', dash: '4 4', width: 2, fmt: money},
    ],
    yFmt: brlShort, yTitle: 'Faturamento (R$)',
  };

  const be = mg > 0 ? fixed / (mg / 100) : null, ideal = be ? be * 1.5 : null;
  const gauge = be ? gaugeChart({
    width: Math.min(chartWidth(1), 640), value: pace, max: ideal * 1.5, fmt: brl,
    steps: [{to: be, color: '#FADBD8'}, {to: ideal, color: '#F9E79F'}, {to: ideal * 1.5, color: '#D5F5E3'}],
    threshold: be, thresholdTip: `Ponto de equilíbrio: ${brl(be)}`,
    tip: P.partial ? `Ritmo projetado: ${brl(pace)}\nRealizado até ${String(P.endDay).padStart(2, '0')}/${String(P.m).padStart(2, '0')}: ${brl(fat)}` : `Faturamento: ${brl(fat)}`,
    ticks: [0, be, ideal, ideal * 1.5],
    title: P.partial ? `Faturamento projetado ${P.label} (ritmo de ${P.endDay} dias)` : `Faturamento ${P.label}`,
    delta: {value: pace - ideal, text: `${pace >= ideal ? '▲' : '▼'} ${brl(Math.abs(pace - ideal))} vs meta ideal`},
  }) : chartEmpty('Margem indisponível no período — não é possível calcular o ponto de equilíbrio.');

  const idxNext = S.index[nx.m - 1];
  const top5 = (data.categories || []).slice().sort((a, b) => b.revenue - a.revenue).slice(0, 5);
  const emoji = idxNext == null ? '➡️' : idxNext > 1.1 ? '🔥' : idxNext < 0.9 ? '❄️' : '➡️';

  const eros = erosionRows(data).filter((r) => r.erosao > EROSAO_LIMIAR);
  const prods = data.products || [];
  const estrelas = prods.filter((p) => p.classification === 'Estrela').sort((a, b) => (b.profit || 0) - (a.profit || 0));
  const pesoMorto = prods.filter((p) => p.classification === 'Baixo giro');
  const cmp = data.comparison;
  const vc = cmp && cmp.totals.receipts ? growth(t.receipts, cmp.totals.receipts) : null;
  const netMargin = mg == null || !pace ? null : (pace * mg / 100 - fixed) / pace * 100;

  const doList = [];
  if (eros.length) doList.push(`🔴 <strong>Reajustar preços</strong> de ${eros.length} produtos Curva A com custo subindo`);
  if (estrelas.length) doList.push(`⭐ <strong>Garantir estoque</strong> dos top Estrelas: ${estrelas.slice(0, 3).map((p) => esc(p.name)).join(', ')}`);
  if (idxNext != null && idxNext > 1.05) doList.push(`📈 <strong>Reforçar compras</strong> — ${nx.nome} é forte (índice ${dec2(idxNext)})`);
  else if (idxNext != null && idxNext < 0.95) doList.push(`📢 <strong>Planejar promoções</strong> — ${nx.nome} é fraco (índice ${dec2(idxNext)})`);
  doList.push(`🎯 <strong>Meta de faturamento</strong>: ${brl(cr)}`);
  doList.push(`💰 <strong>Meta de lucro líquido</strong>: ${brl(lr)}`);
  const watchList = [];
  if (vc != null) watchList.push(`👥 <strong>Fluxo de clientes</strong>: variou ${signedPct(vc)} vs o mesmo período do ano anterior`);
  watchList.push(`📊 <strong>Margem real</strong> (após custo fixo): manter acima de ${META_MARGEM_REAL}% (atual: ${pct1(netMargin)})`);
  watchList.push(`🏷️ <strong>Erosão</strong>: ${eros.length} produtos precisam de reajuste`);
  if (pesoMorto.length > 50) watchList.push(`🗑️ <strong>Peso Morto</strong>: ${num(pesoMorto.length)} produtos a avaliar`);

  const basisTxt = nx.basis === 'sazonal'
    ? `Projeção sazonal: ${nx.curto}/${String(S.R).slice(2)} × fator ${dec2(S.factor)}.`
    : `Sem ${nx.curto}/${String(nx.y - 1).slice(2)} para projetar pela sazonalidade — base = ritmo atual de ${P.label}.`;

  document.getElementById('content').innerHTML = `
    <div class="page-title">🔮 Visão Futurista — Cenários e Projeções</div>
    <div class="page-subtitle">Baseado nos dados, o que esperar e como se preparar?</div>
    <span class="periodo-badge">📅 Base: ${P.nome}/${P.y}${P.partial ? ` · parcial até ${String(P.endDay).padStart(2, '0')}/${String(P.m).padStart(2, '0')}` : ''}</span>
    <hr class="divider">

    ${section(`📊 Projeção de Faturamento — ${P.y} Completo`)}
    <div class="chart-container chart-h-400" id="chart-visao-proj"></div>
    ${explain(`Projeção ${P.y}`, `Amarelo sólido = real. Amarelo transparente = projeção sazonal. Laranja = mês em andamento. Linha cinza = ${S.R}.`,
      S.factor != null ? `Fator de ajuste: ${dec2(S.factor)} (${P.y} está ${signedPct((S.factor - 1) * 100)} vs ${S.R} nos ${S.pairs} meses comparáveis). Projeção = mês de ${S.R} × fator.`
        : `Não há meses fechados comparáveis entre ${P.y} e ${S.R} — sem projeção sazonal.`,
      'Antecipar faturamento para planejar compras e caixa.')}
    <hr class="divider">

    ${section(`🎯 Cenários para ${nx.nome}/${nx.y}`)}
    <div class="kpi-grid kpi-grid-3">
      ${kpi('😟 Pessimista (−15%)', brl(cp), `Lucro: ${brl(lp)}`, lp != null && lp < 0 ? 'kpi-negative' : 'kpi-neutral')}
      ${kpi('📊 Realista', brl(cr), `Lucro: ${brl(lr)}`, lr != null && lr < 0 ? 'kpi-negative' : 'kpi-positive')}
      ${kpi('🚀 Otimista (+15%)', brl(co), `Lucro: ${brl(lo)}`, lo != null && lo < 0 ? 'kpi-negative' : 'kpi-positive')}
    </div>
    ${explain(`Cenários ${nx.nome}`, '3 cenários sobre a projeção: pessimista (−15%), realista e otimista (+15%). Lucro = faturamento × margem bruta atual − custo fixo mensal.',
      'Se o pessimista já dá lucro, o negócio está seguro.', 'Planejar caixa e definir metas realistas.', basisTxt)}
    <hr class="divider">

    ${section(`🏎️ Velocímetro — ${P.label} vs Metas`)}
    ${chartBox(gauge)}
    ${be ? explain('Velocímetro', `Vermelho = abaixo do ponto de equilíbrio (${brl(be)}). Amarelo = acima do equilíbrio. Verde = acima da meta ideal (${brl(ideal)} = 1,5× equilíbrio).`,
      'A linha vermelha é o ponto de equilíbrio. Quanto mais para a direita (verde), mais saudável.', 'Mostra se o mês cobre o custo fixo com folga.',
      P.partial ? `Realizado até ${String(P.endDay).padStart(2, '0')}/${String(P.m).padStart(2, '0')}: ${brl(fat)}. O ponteiro usa o ritmo diário projetado para o mês inteiro.` : undefined) : ''}
    <hr class="divider">

    ${section('📦 Top 5 Categorias — Performance e Tendência')}
    <div class="kpi-grid kpi-grid-5">
      ${top5.map((c) => `<div class="kpi-card"><div class="kpi-title">${esc(c.name)}</div><div class="kpi-value kpi-value-sm">${brl(c.revenue)}</div>
        <div class="kpi-subtitle">Margem: ${pct1(c.margin)} | ${emoji} ${nx.curto}: ${dec2(idxNext)}</div></div>`).join('')}
    </div>
    ${explain('Top 5 Categorias', `As 5 maiores categorias de ${P.label} + tendência sazonal de ${nx.nome} (índice de ${S.R}).`,
      '🔥 = mês forte (> 1,10). ❄️ = fraco (< 0,90). ➡️ = normal ou sem histórico.', 'Reforçar estoque das 🔥 e promover as ❄️.')}
    <hr class="divider">

    ${section(`📋 Direcionamento Estratégico — ${nx.nome}/${nx.y}`)}
    <div class="row">
      <div class="col-50">
        <h3 class="action-title">✅ O que FAZER em ${nx.nome}</h3>
        <ul class="action-list">${doList.map((li) => `<li>${li}</li>`).join('')}</ul>
      </div>
      <div class="col-50">
        <h3 class="action-title">⚠️ O que MONITORAR</h3>
        <ul class="action-list">${watchList.map((li) => `<li>${li}</li>`).join('')}</ul>
      </div>
    </div>
    ${explain('Plano de Ação', 'Gerado automaticamente com base nos dados e projeções.',
      'Ações priorizadas por impacto: margem → estoque → sazonalidade.', 'Revise com os sócios no início de cada mês.')}
  `;
  mountEchartCombo(document.getElementById('chart-visao-proj'), projOpts);
}

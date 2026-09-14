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
// Chart colors are the brand tokens in style.css (--chart-*, light and dark), read when a
// chart is drawn — so every page shares one palette and a theme switch repaints correctly.
// Yellow/amber carry the data, graphite a second series, warm gray the references;
// green and red are kept for good and bad only.
const chartVar = (name, fallback) => {
  if (typeof getComputedStyle !== 'function' || typeof document === 'undefined' || !document.documentElement) return fallback;
  return (getComputedStyle(document.documentElement).getPropertyValue(name) || '').trim() || fallback;
};
const COR = {
  get yellow() { return chartVar('--chart-1', '#FFC107'); },
  get amber() { return chartVar('--chart-2', '#B7791F'); },
  get green() { return chartVar('--chart-good', '#1E8449'); },
  get greenDark() { return chartVar('--chart-good', '#1E8449'); },
  get red() { return chartVar('--chart-bad', '#C0392B'); },
  get blue() { return chartVar('--chart-3', '#2D2D2D'); },  // neutral graphite series (was a blue outside the brand)
  get orange() { return chartVar('--chart-warn', '#D9820B'); },
  get gray() { return chartVar('--chart-ref', '#A8A49A'); },
  get lightGray() { return chartVar('--chart-ref-light', '#CFCBC2'); },
  get goodSoft() { return chartVar('--chart-good-soft', '#D5F5E3'); },
  get warnSoft() { return chartVar('--chart-warn-soft', '#FFF1C2'); },
  get badSoft() { return chartVar('--chart-bad-soft', '#FADBD8'); },
};
const EROSAO_LIMIAR = 3;       // pontos de margem, mesmo corte da versão anterior
// Same thresholds as backend/models.py summarize(): giro ≥ 60% dos dias, margem ≥ 35%.
const GIRO_CORTE = 0.6, MARGEM_CORTE = 35;
// label has no emoji: it also feeds ECharts canvas legends/tooltips, where a glyph's
// look depends on the OS's emoji font — icon carries the matching Lucide icon for DOM use.
const CLASSES = [
  {key: 'Estrela', label: 'Estrela', icon: 'star', get color() { return COR.green; }},
  {key: 'Gerador de caixa', label: 'Gerador de Caixa', icon: 'dollar-sign', get color() { return COR.yellow; }},
  {key: 'Oportunidade', label: 'Oportunidade', icon: 'search', get color() { return COR.blue; }},
  {key: 'Baixo giro', label: 'Baixo giro', icon: 'triangle-alert', get color() { return COR.red; }},
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

function insightHeader(iconName, title, subtitle, P) {
  const partial = P.partial ? ` · parcial até ${String(P.endDay).padStart(2, '0')}/${String(P.m).padStart(2, '0')}` : '';
  return `<div class="page-title">${icon(iconName)} ${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <span class="periodo-badge">${icon('calendar')} Analisando: ${P.nome}/${P.y}${partial}</span>
    <hr class="divider">`;
}

function kpi(title, value, subtitle, subCls, valueCls) {
  return `<div class="kpi-card"><div class="kpi-title">${title}</div><div class="kpi-value ${valueCls || ''}">${value}</div>${
    subtitle ? `<div class="kpi-subtitle ${subCls || ''}">${subtitle}</div>` : ''}</div>`;
}

function story(text) { return `<div class="story-box">${icon('lightbulb')} ${text}</div>`; }
function section(text) { return `<div class="section-header">${text}</div>`; }
function chartBox(inner) { return `<div class="chart-container">${inner}</div>`; }

function explain(title, what, how, why, example) {
  return `<div class="tooltip-box">
    <button type="button" class="tooltip-toggle" data-explain>${icon('lightbulb')} Como interpretar: ${title}</button>
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

function zeroCostBanner(data) {
  const zero = (data.products || []).filter((p) => p.cost === 0 && p.revenue > 0);
  if (!zero.length) return '';
  return `<div class="disclosure-banner warn">${icon('triangle-alert')} ${num(zero.length)} produto${zero.length === 1 ? '' : 's'} vendido${zero.length === 1 ? '' : 's'}
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

// The price that keeps today's margin once the last purchase cost is the cost: cost ÷ (1 − margin).
function priceToKeepMargin(lastCost, marginPct) {
  if (!(lastCost > 0) || marginPct == null || !isFinite(marginPct) || marginPct >= 100) return null;
  return Math.ceil(lastCost / (1 - marginPct / 100));
}

// Healthy above 55%, attention 40–55%, critical below 40% — written out, not color only.
function categoryMarginBand(margin) {
  if (margin == null) return {cls: 'mid', word: 'sem custo'};
  if (margin > 55) return {cls: 'good', word: 'saudável'};
  if (margin > 40) return {cls: 'mid', word: 'atenção'};
  return {cls: 'low', word: 'crítica'};
}

function categoryMarginList(cats) {
  if (!cats.length) return '<div class="chart-empty">Sem categorias com venda no período.</div>';
  return `<ol class="cat-list">${cats.map((c) => {
    const band = categoryMarginBand(c.margin);
    return `<li>
      <div class="cat-row"><span class="cat-name">${esc(c.name)}</span>
        <span class="top-margin cat-margin ${band.cls}">${c.margin == null ? '—' : pct1(c.margin)} · ${band.word}</span></div>
      <progress class="cat-bar" max="100" value="${Math.max(0, Math.min(100, c.margin || 0))}" aria-hidden="true"></progress>
      <div class="cat-meta">Faturamento ${brl(c.revenue)}</div>
    </li>`;
  }).join('')}</ol>`;
}

/* ---------------------------------------------------------------- Preços: revisão por produto */

// What the page's buttons, filter and side panel need after the table is drawn.
let PRECOS_STATE = null;

// Ends a suggested price the way shelf prices end — under R$ 10 on ,x9, from R$ 10 on
// ,49 or ,99 — and never below the exact price that keeps today's margin.
function psychologicalPrice(cents) {
  if (!(cents > 0)) return null;
  if (cents < 1000) return Math.ceil((cents + 1) / 10) * 10 - 1;
  const reais = Math.floor(cents / 100);
  for (const ending of [49, 99]) {
    if (reais * 100 + ending >= cents) return reais * 100 + ending;
  }
  return (reais + 1) * 100 + 49;
}

const shortDateBR = (iso) => (iso ? `${String(iso).slice(8, 10)}/${String(iso).slice(5, 7)}` : '');

// Cost per daily snapshot (newest first from the API), drawn oldest to newest.
function costSparkline(history) {
  const points = (history || []).filter((h) => h.cost_cents > 0).slice(0, 60).reverse();
  if (points.length < 2) {
    return '<p class="field-help">Ainda não há histórico suficiente: o custo passa a ser registrado a cada sincronização diária.</p>';
  }
  const vals = points.map((h) => h.cost_cents);
  const min = Math.min(...vals), max = Math.max(...vals), span = max - min || 1;
  const W = 280, H = 56, pad = 5;
  const xy = vals.map((v, i) => [pad + i * (W - 2 * pad) / (vals.length - 1), H - pad - (v - min) / span * (H - 2 * pad)]);
  const first = points[0], latest = points[points.length - 1], end = xy[xy.length - 1];
  const change = first.cost_cents ? (latest.cost_cents / first.cost_cents - 1) * 100 : null;
  return `<svg class="cost-spark" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Custo de ${money(first.cost_cents)} em ${shortDateBR(first.observed_at)} para ${money(latest.cost_cents)} em ${shortDateBR(latest.observed_at)}">
      <polyline class="cost-spark-line" points="${xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')}"></polyline>
      <circle class="cost-spark-end" cx="${end[0].toFixed(1)}" cy="${end[1].toFixed(1)}" r="3.5"></circle>
    </svg>
    <p class="field-help">De ${money(first.cost_cents)} (${shortDateBR(first.observed_at)}) para <strong>${money(latest.cost_cents)}</strong>
      (${shortDateBR(latest.observed_at)})${change == null ? '' : ` · ${change >= 0 ? '+' : ''}${pct1(change)}`}.
      Mínimo ${money(min)}, máximo ${money(max)} em ${num(points.length)} registros.</p>`;
}

function onPrecosCategory(category) {
  const s = PRECOS_STATE;
  const el = document.getElementById('chart-precos-scatter');
  if (!s || !el) return;
  if (typeof echarts !== 'undefined') {
    const chart = echarts.getInstanceByDom(el);
    if (chart) chart.dispose();
  }
  el.innerHTML = '';
  mountEchartScatter(el, s.scatterFor(category ? s.cap.filter((p) => s.categoryOf(p) === category) : s.cap));
}

const signedMoney = (cents) => `${cents >= 0 ? '+' : '−'}${money(Math.abs(cents))}`;

async function runPriceSimulation(dialog, r) {
  const status = dialog.querySelector('#price-sim-status');
  const out = dialog.querySelector('#price-sim-result');
  const price = parseDecimalBR(dialog.querySelector('#price-sim-price').value);
  const qty = parseDecimalBR(dialog.querySelector('#price-sim-qty').value);
  status.textContent = '';
  if (!(price > 0)) { status.textContent = 'Informe o novo preço, por exemplo 3,49.'; return; }
  if (!(qty > 0)) { status.textContent = 'Informe a quantidade esperada, maior que zero.'; return; }
  out.innerHTML = '<p class="field-help">Simulando…</p>';
  const simulate = (priceCents) => api(`/api/companies/${APP.company}/pricing/simulate`, {method: 'POST', body: JSON.stringify({
    period: PRECOS_STATE.data.period, product_id: Number(r.id), new_price_cents: priceCents, expected_quantity: qty, cost_cents: r.last_cost})});
  try {
    const [now, next] = await Promise.all([simulate(r.current_price), simulate(Math.round(price * 100))]);
    if (!dialog.isConnected) return;
    const row = (label, a, b) => `<tr><td>${label}</td><td class="num">${a}</td><td class="num"><strong>${b}</strong></td></tr>`;
    out.innerHTML = `<div class="data-table-container"><table class="data-table price-sim-table">
        <thead><tr><th><span class="visually-hidden">Indicador</span></th><th class="num">Preço atual</th><th class="num">Novo preço</th></tr></thead>
        <tbody>
          ${row('Preço', money(now.simulated_price_cents), money(next.simulated_price_cents))}
          ${row('Faturamento', money(now.simulated_revenue_cents), money(next.simulated_revenue_cents))}
          ${row('Lucro bruto', money(now.simulated_profit_cents), money(next.simulated_profit_cents))}
          ${row('Margem', pct1(now.simulated_margin_pct), pct1(next.simulated_margin_pct))}
        </tbody></table></div>
      <p class="field-help">Com o custo da última compra (${money(r.last_cost)}) e ${formatDecimalBR(qty, qty % 1 ? 2 : 0)} unidades.
        Diferença de lucro: <strong>${signedMoney(next.simulated_profit_cents - now.simulated_profit_cents)}</strong>.</p>`;
  } catch (e) {
    out.innerHTML = '';
    status.textContent = 'Não foi possível simular: ' + e.message;
  }
}

async function onPriceReview(event) {
  const s = PRECOS_STATE;
  const r = s && s.subiu.find((x) => String(x.id) === event.currentTarget.dataset.priceReview);
  if (!r) return;
  const keep = priceToKeepMargin(r.last_cost, r.md);
  const suggested = psychologicalPrice(keep);
  const sold = Number((s.prodById.get(r.id) || {}).quantity) || 0;
  const soldText = formatDecimalBR(sold, sold % 1 ? 2 : 0);
  const drawer = openDrawer({title: 'Simular novo preço', trigger: event.currentTarget, body: `
    <p class="price-review-name">${esc(r.name)}</p>
    <dl class="summary-list">
      <div><dt>Preço atual</dt><dd>${money(r.current_price)}</dd></div>
      <div><dt>Margem hoje</dt><dd>${pct1(r.md)}</dd></div>
      <div><dt>Custo médio</dt><dd>${money(r.current_cost)}</dd></div>
      <div><dt>Custo da última compra</dt><dd>${money(r.last_cost)}</dd></div>
    </dl>
    <h3 class="drawer-subtitle">Custo registrado</h3>
    <div id="price-history"><p class="field-help">Carregando histórico…</p></div>
    <h3 class="drawer-subtitle">Simulação</h3>
    <form id="price-sim-form" class="drawer-form" novalidate>
      <div class="form-grid">
        <div class="form-field"><label class="field-label" for="price-sim-price">Novo preço (R$)</label>
          <input id="price-sim-price" class="login-input" type="text" inputmode="decimal" autocomplete="off"
            value="${suggested == null ? '' : formatDecimalBR(suggested / 100, 2)}" aria-describedby="price-sim-price-help">
          <p id="price-sim-price-help" class="field-help">${suggested == null ? 'Digite o preço que deseja testar.'
            : `Sugerido ${money(suggested)}; o exato para manter ${pct1(r.md)} é ${money(keep)}.`}</p></div>
        <div class="form-field"><label class="field-label" for="price-sim-qty">Quantidade esperada</label>
          <input id="price-sim-qty" class="login-input" type="text" inputmode="decimal" autocomplete="off" value="${soldText}" aria-describedby="price-sim-qty-help">
          <p id="price-sim-qty-help" class="field-help">Vendida em ${esc(s.periodLabel)}: ${soldText}.</p></div>
      </div>
      <button type="submit" class="btn-primary btn-wide">Simular</button>
      <p id="price-sim-status" class="form-error" role="alert"></p>
    </form>
    <div id="price-sim-result" aria-live="polite"></div>
    <div class="drawer-actions"><div class="row-actions" data-price-key="preco:${esc(r.id)}">
      <span class="action-count" data-action-count hidden></span>
      <button type="button" class="btn-secondary" data-price-action="${esc(r.id)}">Criar ação de reajuste</button>
    </div></div>`});
  const dialog = drawer.dialog;
  dialog.querySelector('[data-price-action]').addEventListener('click', onPriceAction);
  dialog.querySelector('#price-sim-form').addEventListener('submit', (ev) => { ev.preventDefault(); runPriceSimulation(dialog, r); });
  loadPriceActions(dialog);
  const box = dialog.querySelector('#price-history');
  try {
    const detail = await api(`/api/companies/${APP.company}/products/${encodeURIComponent(r.id)}?period=${encodeURIComponent(s.data.period)}`);
    if (box.isConnected) box.innerHTML = costSparkline(detail.history);
  } catch (e) {
    if (box.isConnected) box.innerHTML = `<p class="field-help">Não foi possível carregar o histórico: ${esc(e.message)}</p>`;
  }
}

// Each product's price review counts as its own alert type, so the same product is
// never turned into a second open action by accident.
async function loadPriceActions(scope) {
  const wraps = (scope || document).querySelectorAll('[data-price-key]');
  if (!wraps.length) return;
  let list;
  try {
    list = await api(`/api/companies/${APP.company}/actions`);
  } catch (e) {
    return;
  }
  wraps.forEach((wrap) => {
    if (!wrap.isConnected) return;
    const count = alertActionCount(list, wrap.dataset.priceKey);
    const slot = wrap.querySelector('[data-action-count]');
    const btn = wrap.querySelector('[data-price-action]');
    if (slot) {
      slot.innerHTML = count ? `<a class="action-state has" href="${routeHash('acoes', APP.period)}">${count} ${count === 1 ? 'ação aberta' : 'ações abertas'} →</a>` : '';
      slot.hidden = !count;
    }
    if (btn && !btn.disabled && count) btn.textContent = 'Criar outra ação';
  });
}

async function onPriceAction(event) {
  const btn = event.currentTarget;
  const s = PRECOS_STATE;
  const r = s && s.subiu.find((x) => String(x.id) === btn.dataset.priceAction);
  if (!r) return;
  const suggested = psychologicalPrice(priceToKeepMargin(r.last_cost, r.md));
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Criando…';
  try {
    await api(`/api/companies/${APP.company}/actions`, {method: 'POST', body: JSON.stringify({
      alert_key: `preco:${r.id}`, alert_version: String(r.last_cost),
      title: `Reajustar preço: ${r.name}${suggested == null ? '' : ` para ${money(suggested)}`} (custo foi de ${money(r.current_cost)} para ${money(r.last_cost)})`.slice(0, 240),
      priority: r.erosao > 10 ? 'high' : 'medium'})});
    btn.textContent = 'Ação criada ✓';
    loadPriceActions();
    const dialog = btn.closest('dialog');
    if (dialog) loadPriceActions(dialog);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = label;
    btn.insertAdjacentHTML('afterend', `<span class="form-error" role="alert">Não foi possível criar a ação: ${esc(e.message)}</span>`);
  }
}

function renderPrecos(data) {
  const P = periodInfo(data), t = data.totals;
  const curvaA = (data.inventory || []).filter((p) => p.abc === 'A');
  const eros = erosionRows(data);
  const subiu = eros.filter((r) => r.erosao > EROSAO_LIMIAR).sort((a, b) => b.erosao - a.erosao);
  const caiu = eros.filter((r) => r.erosao < -EROSAO_LIMIAR).sort((a, b) => a.erosao - b.erosao);
  const low = curvaA.filter((p) => p.margin != null && p.margin < MARGEM_CORTE);
  // Extra gross profit if each low-margin Curva A product reached MARGEM_CORTE at the same sales.
  const lowGap = sumBy(low, (p) => p.revenue * (MARGEM_CORTE - p.margin) / 100);
  const mdm = t.margin;

  const prodById = new Map((data.products || []).map((p) => [p.id, p]));
  const categoryOf = (p) => p.category || (prodById.get(p.id) || {}).category || 'Sem categoria';
  const cap = curvaA.filter((p) => p.revenue > 5000 && p.margin != null);
  const capCategories = [...new Set(cap.map(categoryOf))].sort((a, b) => a.localeCompare(b, 'pt-BR'));
  // Built per list so the category filter redraws the same chart for a subset.
  const scatterFor = (list) => {
    const rOf = bubbleRadius(list.map((p) => p.profit), 4, 18);
    const avgRev = list.length ? sumBy(list, (p) => p.revenue) / list.length : 0;
    const maxRev = Math.max(0, ...list.map((p) => p.revenue));
    return {
      points: list.map((p) => ({x: p.revenue, y: p.margin, r: rOf(p.profit), g: CLASSES.indexOf(classInfo(p.classification)),
        tip: `${p.name}\nFaturamento: ${money(p.revenue)}\nMargem: ${pct1(p.margin)}\nLucro: ${money(p.profit)}\n${classInfo(p.classification).label}`})),
      groups: CLASSES.map((c) => ({name: c.label, color: c.color})),
      xScale: niceScale(0, maxRev * 1.05, 6),
      xTitle: 'Faturamento no período (R$)', yTitle: 'Margem (%)', xFmt: brlShort, yFmt: (v) => v + '%',
      vlines: list.length ? [{x: avgRev, label: `Faturamento médio: ${brlShort(avgRev)}`}] : [],
      hlines: mdm == null ? [] : [{y: mdm, label: `Margem da loja: ${pct1(mdm)}`}],
      empty: list === cap ? 'Nenhum produto da curva A com custo conhecido no período.' : 'Nenhum produto da curva A nesta categoria.',
    };
  };
  PRECOS_STATE = {data, cap, categoryOf, scatterFor, subiu, prodById, periodLabel: P.label};

  // The twelve categories that sell the most: a thin margin matters where the money is.
  const cats = (data.categories || []).filter((c) => c.revenue > 0).sort((a, b) => b.revenue - a.revenue).slice(0, 12);

  const productLink = (r) => `<a href="#/produto/${esc(data.period)}?id=${esc(r.id)}">${esc(r.name)}</a>`;
  const reviewTable = (rows, rising) => table(
    ['Produto', 'Preço atual', 'Custo: médio → última compra', 'Margem: hoje → com custo novo']
      .concat(rising ? ['Preço sugerido para manter a margem', '<span class="visually-hidden">Ações</span>'] : []),
    rows.map((r) => {
      const cells = [productLink(r), money(r.current_price),
        `${money(r.current_cost)} → <strong>${money(r.last_cost)}</strong>`,
        `${pct1(r.md)} → <strong class="${rising ? 'kpi-negative' : 'kpi-positive'}">${pct1(r.mdu)}</strong>`];
      if (rising) {
        const keep = priceToKeepMargin(r.last_cost, r.md);
        const suggested = psychologicalPrice(keep);
        cells.push(suggested == null ? '—'
          : `<strong title="O exato para manter ${pct1(r.md)} é ${money(keep)}">${money(suggested)}</strong> <span class="muted">(+${money(suggested - r.current_price)})</span>`);
        cells.push(`<div class="row-actions price-row-actions" data-price-key="preco:${esc(r.id)}">
          <button type="button" class="btn-secondary" data-price-review="${esc(r.id)}">Simular</button>
          <span class="action-count" data-action-count hidden></span>
          <button type="button" class="btn-secondary" data-price-action="${esc(r.id)}">Criar ação</button></div>`);
      }
      return cells;
    }));

  document.getElementById('content').innerHTML = `
    ${insightHeader('dollar-sign', 'Preços e margens', 'Onde estou deixando dinheiro na mesa?', P)}
    ${zeroCostBanner(data)}
    <div class="kpi-grid kpi-grid-3">
      ${kpi('Margem bruta', pct1(mdm), mdm == null ? 'Há itens vendidos sem custo no período'
        : `De cada R$ 100 vendidos, R$ ${dec2(mdm)} ficam depois do custo do produto`)}
      ${kpi('Custo subiu', num(subiu.length), subiu.length
        ? `produto${subiu.length === 1 ? '' : 's'} da curva A para revisar o preço` : 'Nenhum produto da curva A', subiu.length ? 'kpi-negative' : '')}
      ${kpi(`Margem abaixo de ${MARGEM_CORTE}%`, num(low.length), low.length
        ? `Até ${brl(lowGap)} a mais de lucro no período se chegassem a ${MARGEM_CORTE}%` : 'Nenhum produto da curva A', low.length ? 'kpi-neutral' : '')}
    </div>
    ${story(`${mdm == null ? 'Margem indisponível: há itens vendidos sem custo.' : `Margem bruta de ${pct1(mdm)}.`} ${subiu.length
      ? `${subiu.length} produto${subiu.length === 1 ? ' da curva A ficou mais caro' : 's da curva A ficaram mais caros'} na última compra: veja abaixo o preço que mantém a margem.`
      : 'Nenhum produto da curva A ficou mais caro na última compra.'}`)}
    ${explain('esta página',
      'Margem bruta é quanto sobra de cada venda depois do custo do produto. Curva A são os produtos que somam a maior parte do faturamento.',
      'Comece por "Revisar preço": são produtos importantes que ficaram mais caros na última compra. O preço sugerido mantém a margem que eles têm hoje.',
      'Um aumento de custo sem reajuste de preço reduz o lucro de cada venda, sem aparecer no faturamento.',
      `Os ${num(low.length)} produtos da curva A com margem abaixo de ${MARGEM_CORTE}% dariam até ${brl(lowGap)} a mais de lucro no período se chegassem a ${MARGEM_CORTE}%, vendendo o mesmo.`)}

    ${section(`${icon('triangle-alert')} Revisar preço — curva A`)}
    <p class="section-note">Produtos cuja última compra mudou a margem em mais de ${EROSAO_LIMIAR} pontos, ao preço atual.
      Preços e custos do Mobne em ${dt(data.stock_updated_at)}.</p>
    <div class="tabs" role="group" aria-label="Direção do custo">
      <button type="button" class="tab-btn active" data-tab="tab-subiu">Custo subiu (${subiu.length})</button>
      <button type="button" class="tab-btn" data-tab="tab-caiu">Custo caiu (${caiu.length})</button>
    </div>
    <div id="tab-subiu" class="tab-content active">${subiu.length
      ? reviewTable(subiu, true)
      : '<div class="badge-success">Nenhum produto da curva A ficou mais caro na última compra.</div>'}</div>
    <div id="tab-caiu" class="tab-content">${caiu.length
      ? reviewTable(caiu, false) + '<p class="section-note">Mantendo o preço atual, a margem destes produtos sobe quando o estoque novo entrar.</p>'
      : '<div class="badge-info">Nenhum produto da curva A ficou mais barato na última compra.</div>'}</div>

    <div class="row">
      <div class="col-60">
        ${section(`Faturamento × margem — curva A (${P.label})`)}
        ${capCategories.length > 1 ? `<div class="chart-filter">
          <label class="field-label" for="precos-category">Categoria</label>
          <select id="precos-category" class="login-input">
            <option value="">Todas (${num(cap.length)} produtos)</option>
            ${capCategories.map((c) => `<option value="${esc(c)}">${esc(c)} (${num(cap.filter((p) => categoryOf(p) === c).length)})</option>`).join('')}
          </select></div>` : ''}
        <div class="chart-container chart-h-460" id="chart-precos-scatter"></div>
        <p class="section-note">Cada bolha é um produto; quanto maior, mais lucro. À direita e no alto: vende muito com boa margem.
          À direita e embaixo: vende muito e ganha pouco. Clique na legenda para filtrar.</p>
      </div>
      <div class="col-40">
        ${section(`Margem por categoria (${P.label})`)}
        <div class="cat-card">${categoryMarginList(cats)}</div>
        <p class="section-note">As 12 categorias que mais faturam. Saudável acima de 55%, atenção de 40% a 55%, crítica abaixo de 40%.</p>
      </div>
    </div>
  `;
  mountEchartScatter(document.getElementById('chart-precos-scatter'), scatterFor(cap));
  const categorySelect = document.getElementById('precos-category');
  if (categorySelect) categorySelect.addEventListener('change', () => onPrecosCategory(categorySelect.value));
  document.querySelectorAll('[data-price-review]').forEach((b) => b.addEventListener('click', onPriceReview));
  document.querySelectorAll('[data-price-action]').forEach((b) => b.addEventListener('click', onPriceAction));
  loadPriceActions();
}

/* ================================================================ PÁGINA: MAPA DE PRODUTOS */

// Lowest margin drawn on the product map; anything below sits on the edge (tooltip keeps the real value).
const MARGEM_PISO = -20;

// Giro is days sold ÷ days in the period, so products pile up in a few vertical columns.
// A small offset fixed per product (same place on every visit, never crossing the giro
// cut) spreads each column so the bubbles can be told apart.
function spreadTurnover(p) {
  const seed = Math.abs(Math.sin(Number(p.id) * 12.9898) * 43758.5453) % 1;
  const x = p.turnover + (seed - 0.5) * 0.05;
  return p.turnover >= GIRO_CORTE ? Math.max(GIRO_CORTE, x) : Math.min(GIRO_CORTE - 0.001, x);
}

function renderMapa(data) {
  const P = periodInfo(data);
  const pm = data.product_map;
  // Groups come from the last 30 days (backend product_map); an older payload falls back to the month.
  const all = pm ? pm.products : (data.products || []);
  const noCost = all.filter(isZeroCost);
  const prods = all.filter((p) => !isZeroCost(p));
  const windowLabel = pm ? `${shortDateBR(pm.start)} a ${shortDateBR(pm.end)}` : P.label;
  const [est, ger, opo, low] = CLASSES.map((c) => prods.filter((p) => p.classification === c.key));
  const lucro = (arr) => sumBy(arr, (p) => p.profit);
  const lt = lucro(prods);
  let acc = 0, n80 = 0;
  if (lt > 0) {
    for (const p of prods.slice().sort((a, b) => (b.profit || 0) - (a.profit || 0))) {
      acc += p.profit || 0; n80++;
      if (acc >= lt * 0.8) break;
    }
  }
  const idle = idleStockCents(low, data.inventory);
  const categories = [...new Set(prods.map((p) => p.category || 'Sem categoria'))].sort((a, b) => a.localeCompare(b, 'pt-BR'));
  MAPA_STATE = {data, P, pm, prods, windowLabel, q: '', category: '',
    // Actions are tied to this 30-day window: a later window can raise the same group again.
    version: pm ? pm.end : data.period,
    actions: {
      'mapa:gerador': {count: ger.length, label: 'Criar ação: renegociar custo',
        title: `Renegociar custo ou ajustar preço dos ${ger.length} geradores de caixa (vendas de ${windowLabel})`},
      'mapa:baixo-giro': {count: low.length, label: 'Criar ação: avaliar retirada',
        title: `Avaliar retirada ou liquidação dos ${low.length} produtos de baixo giro (${brl(idle)} parados em estoque)`},
    }};

  document.getElementById('content').innerHTML = `
    ${insightHeader('map', 'Mapa de produtos', 'Quais produtos proteger, ajustar, divulgar ou rever?', P)}
    <p class="section-note">Grupos calculados com as vendas de ${windowLabel}${pm ? ` (últimos ${pm.days} dias, ${num(pm.sales_days)} com venda)` : ''},
      para que não mudem só porque o mês ainda está no começo.</p>
    <div class="kpi-grid kpi-grid-4">
      ${kpi(`${icon('star')} Estrelas`, num(est.length), `Vendem sempre, com boa margem · ${brl(lucro(est))} de lucro`)}
      ${kpi(`${icon('dollar-sign')} Geradores de caixa`, num(ger.length), `Vendem sempre, com margem baixa · ${brl(lucro(ger))} de lucro`)}
      ${kpi(`${icon('search')} Oportunidades`, num(opo.length), `Boa margem, vendem pouco · ${brl(lucro(opo))} de lucro`)}
      ${kpi(`${icon('triangle-alert')} Baixo giro`, num(low.length), `Vendem pouco e ganham pouco · ${brl(idle)} parados em estoque`)}
    </div>
    ${story(`Apenas ${num(n80)} de ${num(prods.length)} produtos geram 80% do lucro. Antes de qualquer outra decisão, não deixe faltar as ${num(est.length)} estrelas.`)}
    ${noCost.length ? `<details class="nocost-box">
      <summary>${icon('triangle-alert')} ${num(noCost.length)} produto${noCost.length === 1 ? '' : 's'} vendido${noCost.length === 1 ? '' : 's'} com custo zero no Mobne
        (${brl(sumBy(noCost, (p) => p.revenue))} de faturamento) ficaram fora dos grupos — ver quais</summary>
      ${table(['Produto', 'Faturamento', 'Última venda'], noCost.slice().sort((a, b) => b.revenue - a.revenue).slice(0, 20)
        .map((p) => [mapaProductLink(p, data.period), money(p.revenue), p.last_sold ? shortDateBR(p.last_sold) : '—']))}
      <p class="section-note">Sem o custo, a margem aparece como 100% e o produto cairia no grupo errado.
        ${noCost.length > 20 ? 'Mostrando os 20 que mais faturam. ' : ''}Cadastre o custo no Mobne para que entrem no mapa.
        <a href="${routeHash('estoque', data.period, new URLSearchParams({filtro: 'custo-zero'}))}">Abrir a lista completa em Produtos e estoque →</a></p>
    </details>` : ''}
    ${explain('esta página',
      `Cada produto entra em um grupo pelo giro (em quantos dias com venda da loja ele vendeu, nos últimos 30 dias) e pela margem. Os cortes são vender em ${GIRO_CORTE * 100}% desses dias ou mais e ter margem de ${MARGEM_CORTE}% ou mais.`,
      'Estrelas: proteger o estoque. Geradores de caixa: renegociar custo ou ajustar preço. Oportunidades: dar visibilidade na loja. Baixo giro: avaliar se vale manter.',
      'O mix certo libera espaço na prateleira e dinheiro parado em estoque para o que realmente dá lucro.')}

    ${mapaChangesHtml(pm, data.period)}

    <div class="mapa-filters" role="search" aria-label="Filtrar produtos do mapa">
      <div class="mapa-filter"><label class="field-label" for="mapa-search">Buscar produto</label>
        <input type="search" id="mapa-search" class="search-input" placeholder="Ex.: banana, cerveja…" autocomplete="off"></div>
      ${categories.length > 1 ? `<div class="mapa-filter"><label class="field-label" for="mapa-category">Categoria</label>
        <select id="mapa-category" class="login-input"><option value="">Todas</option>
          ${categories.map((c) => `<option value="${esc(c)}">${esc(sentenceCase(c))}</option>`).join('')}</select></div>` : ''}
      <span id="mapa-count" class="result-count" role="status" aria-live="polite"></span>
    </div>
    <div id="mapa-body"></div>
  `;
  renderMapaBody();
  const search = document.getElementById('mapa-search');
  let timer = null;
  search.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { MAPA_STATE.q = fold(search.value).trim(); renderMapaBody(); }, 150);
  });
  const select = document.getElementById('mapa-category');
  if (select) select.addEventListener('change', () => { MAPA_STATE.category = select.value; renderMapaBody(); });
}

/* ---------------------------------------------------------------- Mapa: helpers */

let MAPA_STATE = null;
const isZeroCost = (p) => p.cost === 0 && p.revenue > 0;
const GROUP_TITLES = {'Estrela': 'Estrelas', 'Gerador de caixa': 'Geradores de caixa', 'Oportunidade': 'Oportunidades', 'Baixo giro': 'Baixo giro'};
const GROUP_WORDS = {'Estrela': 'estrela', 'Gerador de caixa': 'gerador de caixa', 'Oportunidade': 'oportunidade', 'Baixo giro': 'baixo giro', 'Sem vendas': 'sem vendas'};

// Mobne names come in capitals; tables read easier with only the first letter capitalized.
function sentenceCase(name) {
  const s = String(name || '').toLocaleLowerCase('pt-BR');
  return s.charAt(0).toLocaleUpperCase('pt-BR') + s.slice(1);
}

const mapaProductLink = (p, period) =>
  `<a href="#/produto/${esc(period)}?id=${esc(p.id)}" title="${esc(p.name)}">${esc(sentenceCase(p.name))}</a>`;

// Money sitting on the shelf: stock × current cost; negative or unknown stock counts as zero.
function idleStockCents(products, inventory) {
  const inv = new Map((inventory || []).map((p) => [p.id, p]));
  return sumBy(products, (p) => {
    const i = inv.get(p.id);
    return i && i.stock > 0 && i.current_cost > 0 ? Math.round(i.stock * i.current_cost) : 0;
  });
}

function mapaChangesHtml(pm, period) {
  if (!pm) return '';
  const title = section('O que mudou de grupo');
  if (!pm.previous || !pm.previous.available) {
    return `${title}<p class="section-note">Ainda não há vendas dos 30 dias anteriores para comparar os grupos.</p>`;
  }
  const lost = (pm.changes && pm.changes.lost_star) || [];
  const low = (pm.changes && pm.changes.became_low) || [];
  const range = `Comparado com ${shortDateBR(pm.previous.start)} a ${shortDateBR(pm.previous.end)}.`;
  if (!lost.length && !low.length) {
    return `${title}<p class="section-note">${range}</p><div class="badge-success">Nenhum produto saiu das estrelas nem virou baixo giro.</div>`;
  }
  const card = (iconName, heading, list, textOf) => `<div class="change-card">
      <h3 class="class-title">${icon(iconName)} ${heading} <span class="class-count">${num(list.length)}</span></h3>
      ${list.length ? `<ul class="change-list">${list.slice(0, 10).map((c) =>
        `<li>${mapaProductLink(c, period)} <span class="change-to">${textOf(c)}</span></li>`).join('')}</ul>` : '<p class="class-todo">Nenhum.</p>'}
      ${list.length > 10 ? `<p class="section-note">Mostrando 10 de ${num(list.length)}, dos que mais vendiam antes.</p>` : ''}
    </div>`;
  return `${title}
    <p class="section-note">${range} Mudanças de grupo mostram cedo o que está perdendo espaço no mix.</p>
    <div class="row">
      <div class="col-50">${card('star', 'Saíram das estrelas', lost, (c) => `agora ${GROUP_WORDS[c.to] || c.to}`)}</div>
      <div class="col-50">${card('triangle-alert', 'Viraram baixo giro', low, (c) => `antes ${GROUP_WORDS[c.from] || c.from}`)}</div>
    </div>`;
}

function renderMapaBody() {
  const s = MAPA_STATE;
  const body = document.getElementById('mapa-body');
  if (!s || !body) return;
  const oldChart = document.getElementById('chart-mapa-matrix');
  if (oldChart && typeof echarts !== 'undefined') {
    const chart = echarts.getInstanceByDom(oldChart);
    if (chart) chart.dispose();
  }
  const filtered = !!(s.q || s.category);
  const list = s.prods.filter((p) => (!s.category || (p.category || 'Sem categoria') === s.category) && (!s.q || fold(p.name).includes(s.q)));
  const [est, ger, opo, low] = CLASSES.map((c) => list.filter((p) => p.classification === c.key));
  const [cEst, cGer, cOpo, cLow] = CLASSES;
  const byProfit = (arr) => arr.slice().sort((a, b) => (b.profit || 0) - (a.profit || 0));
  const byRevenue = (arr) => arr.slice().sort((a, b) => b.revenue - a.revenue);
  const group = (cls, arr, todo, order, actionKey) => {
    const lowTurn = cls.key === 'Baixo giro';
    const action = actionKey && s.actions[actionKey] && s.actions[actionKey].count
      ? `<div class="row-actions group-actions" data-group-key="${actionKey}">
          <span class="action-count" data-action-count hidden></span>
          <button type="button" class="btn-secondary" data-group-action="${actionKey}">${s.actions[actionKey].label}</button></div>` : '';
    return `<section class="class-group" aria-label="${esc(GROUP_TITLES[cls.key])}">
      <div class="class-head"><h3 class="class-title">${icon(cls.icon)} ${esc(GROUP_TITLES[cls.key])} <span class="class-count">${num(arr.length)}</span></h3>${action}</div>
      <p class="class-todo">${todo}</p>
      ${table(['Produto', lowTurn ? 'Última venda' : 'Dias com venda', 'Margem', 'Faturamento', 'Lucro'],
        arr.slice(0, 10).map((p) => [mapaProductLink(p, s.data.period),
          lowTurn ? (p.last_sold ? shortDateBR(p.last_sold) : '—') : num(p.days_sold), pct1(p.margin), money(p.revenue), money(p.profit)]))}
      <p class="section-note">${arr.length > 10 ? `Mostrando 10 de ${num(arr.length)}, ` : ''}${order}.</p>
    </section>`;
  };

  const pp = list.filter((p) => p.revenue > 2000 && p.margin != null);
  const rOf = bubbleRadius(pp.map((p) => p.revenue), 3, 17);
  // One product at −80% used to stretch the whole axis and squeeze everyone else into the top.
  const clipped = pp.filter((p) => p.margin < MARGEM_PISO).length;
  const margins = pp.map((p) => Math.max(p.margin, MARGEM_PISO));
  const ys = niceScale(Math.min(0, ...margins), Math.max(MARGEM_CORTE + 10, ...margins), 6);
  const span = ys.max - ys.min;
  const matrixOpts = {
    points: pp.map((p) => ({x: spreadTurnover(p), y: Math.max(p.margin, ys.min), r: rOf(p.revenue), g: CLASSES.indexOf(classInfo(p.classification)),
      tip: `${sentenceCase(p.name)}\nGiro: ${Math.round(p.turnover * 100)}% dos dias (${p.days_sold} dias)\nMargem: ${pct1(p.margin)}\nFaturamento: ${money(p.revenue)}\nLucro: ${money(p.profit)}`})),
    groups: CLASSES.map((c) => ({name: GROUP_TITLES[c.key], color: c.key === 'Baixo giro' ? COR.lightGray : c.color})),
    xScale: {min: -0.05, max: 1.05}, yScale: {min: ys.min, max: ys.max},
    xTitle: 'Giro (% dos dias com venda)', yTitle: 'Margem (%)', xFmt: (v) => Math.round(v * 100) + '%', yFmt: (v) => v + '%',
    vlines: [{x: GIRO_CORTE}], hlines: [{y: MARGEM_CORTE}],
    annotations: [
      // Canvas-drawn text (ECharts annotation) — no icon element renders here, so plain text.
      {x: 0.83, y: ys.max - span * 0.05, text: 'ESTRELAS', color: COR.green},
      {x: 0.83, y: ys.min + span * 0.05, text: 'GERADORES', color: COR.orange},
      {x: 0.27, y: ys.max - span * 0.05, text: 'OPORTUNIDADES', color: COR.blue},
      {x: 0.27, y: ys.min + span * 0.05, text: 'BAIXO GIRO', color: COR.red},
    ],
    empty: filtered ? 'Nenhum produto com essa busca e categoria.' : 'Nenhum produto com custo conhecido no período.',
  };

  body.innerHTML = `
    ${section('O que fazer com cada grupo')}
    ${filtered && !list.length ? '<div class="empty-state">Nenhum produto encontrado com essa busca e categoria.</div>' : ''}
    <div class="row">
      <div class="col-50">
        ${group(cEst, byProfit(est), 'Proteger: nunca deixar faltar na prateleira.', 'ordenados por lucro')}
        ${group(cOpo, byProfit(opo), 'Dar visibilidade: ponta de gôndola, degustação ou combo.', 'ordenados por lucro')}
      </div>
      <div class="col-50">
        ${group(cGer, byRevenue(ger), 'Renegociar custo ou ajustar preço: vendem muito e ganham pouco.', 'ordenados por faturamento', 'mapa:gerador')}
        ${group(cLow, byRevenue(low), `Avaliar retirada ou liquidação: ${brl(idleStockCents(low, s.data.inventory))} parados em estoque.`, 'ordenados por faturamento', 'mapa:baixo-giro')}
      </div>
    </div>

    ${section(`Giro × margem (${s.windowLabel})`)}
    <div class="chart-container chart-h-520" id="chart-mapa-matrix"></div>
    <p class="section-note">Cada bolha é um produto; quanto maior, mais faturamento. Mais à direita, vende em mais dias; mais no alto, margem maior.
      Clique na legenda para filtrar os grupos.${clipped ? ` ${num(clipped)} produto${clipped === 1 ? '' : 's'} com margem abaixo de ${MARGEM_PISO}% aparece${clipped === 1 ? '' : 'm'} na borda de baixo; passe o mouse para ver a margem real.` : ''}</p>
  `;
  mountEchartScatter(document.getElementById('chart-mapa-matrix'), matrixOpts);
  const count = document.getElementById('mapa-count');
  if (count) count.textContent = filtered ? `Mostrando ${num(list.length)} de ${num(s.prods.length)} produtos` : '';
  body.querySelectorAll('[data-group-action]').forEach((b) => b.addEventListener('click', onGroupAction));
  loadGroupActions();
}

async function loadGroupActions() {
  const wraps = document.querySelectorAll('[data-group-key]');
  if (!wraps.length) return;
  let list;
  try {
    list = await api(`/api/companies/${APP.company}/actions`);
  } catch (e) {
    return;
  }
  wraps.forEach((wrap) => {
    if (!wrap.isConnected) return;
    const count = alertActionCount(list, wrap.dataset.groupKey);
    const slot = wrap.querySelector('[data-action-count]');
    const btn = wrap.querySelector('[data-group-action]');
    if (slot) {
      slot.innerHTML = count ? `<a class="action-state has" href="${routeHash('acoes', APP.period)}">${count} ${count === 1 ? 'ação aberta' : 'ações abertas'} →</a>` : '';
      slot.hidden = !count;
    }
    if (btn && !btn.disabled && count) btn.textContent = 'Criar outra ação';
  });
}

async function onGroupAction(event) {
  const btn = event.currentTarget;
  const s = MAPA_STATE;
  const info = s && s.actions[btn.dataset.groupAction];
  if (!info) return;
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Criando…';
  try {
    await api(`/api/companies/${APP.company}/actions`, {method: 'POST', body: JSON.stringify({
      alert_key: btn.dataset.groupAction, alert_version: String(s.version), title: info.title.slice(0, 240), priority: 'medium'})});
    btn.textContent = 'Ação criada ✓';
    loadGroupActions();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = label;
    btn.insertAdjacentHTML('afterend', `<span class="form-error" role="alert">Não foi possível criar a ação: ${esc(e.message)}</span>`);
  }
}

/* ================================================================ PÁGINA: DIAGNÓSTICO DE FATURAMENTO */

function renderDiagnostico(data) {
  const P = periodInfo(data), t = data.totals, cmp = data.comparison;
  const fat = t.revenue, cup = t.receipts, tk = t.ticket;
  const prevLabel = cmp ? monthLabel(Number(cmp.period.slice(0, 4)), P.m) : null;
  let vc = null, vt = null;
  let diag = kpi('O que mais pesou', '—', 'Sem vendas do mesmo mês do ano anterior');
  if (cmp && cmp.totals.receipts) {
    const c0 = cmp.totals.receipts, t0 = cmp.totals.ticket;
    vc = growth(cup, c0); vt = growth(tk, t0);
    const ic = (cup - c0) * t0, it = (tk - t0) * cup;
    const [who, effect] = Math.abs(ic) >= Math.abs(it) ? ['Número de clientes', ic] : ['Gasto por cliente', it];
    diag = kpi('O que mais pesou', `${effect >= 0 ? '▲' : '▼'} ${who}`, `Clientes: ${brlSigned(ic)} · Gasto por cliente: ${brlSigned(it)}`,
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
      // The weakest weekday of this period in red, whichever it is — not always Sunday.
      colors: byDow.map((d) => worst && d.nome === worst.nome ? COR.red : COR.yellow),
      strokes: byDow.map((d) => worst && d.nome === worst.nome ? null : COR.amber), tips: byDow.map((d) => `${d.dias} dia(s) com venda`)}],
    yFmt: brlShort, yTitle: 'Fat. médio (R$)',
  };

  document.getElementById('content').innerHTML = `
    ${insightHeader('search', 'Desempenho de vendas', 'Vieram menos clientes, cada um gastou menos, ou o mix mudou?', P)}
    <div class="kpi-grid kpi-grid-4">
      ${kpi('Faturamento', brl(fat), 'Cupons × ticket médio')}
      ${kpi('Cupons', num(cup), vc == null ? 'Sem comparação' : `${deltaArrow(vc)} ${signedPct(vc)}${vsTxt}`, deltaClass(vc))}
      ${kpi('Ticket médio', money(tk), vt == null ? 'Sem comparação' : `${deltaArrow(vt)} ${signedPct(vt)}${vsTxt}`, deltaClass(vt))}
      ${diag}
    </div>
    ${story(vc == null ? `Faturamento de ${brl(fat)}: ${num(cup)} cupons com ticket médio de ${money(tk)}. Não há vendas do mesmo mês do ano anterior para comparar.`
      : `Faturamento de ${brl(fat)}: ${num(cup)} cupons (${signedPct(vc)}) com ticket médio de ${money(tk)} (${signedPct(vt)})${vsTxt}.`)}
    ${explain('esta página', 'Faturamento é o número de cupons (clientes atendidos) vezes o ticket médio (quanto cada um gastou).',
      '"O que mais pesou" mostra qual dos dois explica a maior parte da variação: ▲ ajudou, ▼ atrapalhou.',
      'Menos clientes pede atração (fachada, divulgação); gasto menor por cliente pede mix e venda casada.',
      cmp ? `A comparação usa os mesmos ${P.partial ? P.endDay : P.daysInMonth} primeiros dias de ${prevLabel}.` : undefined)}

    <div class="row">
      <div class="col-50">
        ${section(`Categorias que mais faturam (${P.label})`)}
        <div class="chart-container chart-h-380" id="chart-diag-contrib"></div>
        <p class="section-note">As 12 maiores. Verde: deram lucro; vermelho: prejuízo; cinza: sem custo. Passe o mouse para ver lucro e margem.</p>
      </div>
      <div class="col-50">
        ${section(`Faturamento por dia do mês (${P.label})`)}
        <div class="chart-container chart-h-380" id="chart-diag-heat"></div>
        <p class="section-note">Cada quadrado é um dia, organizado por semana. Quanto mais intensa a cor, maior a venda.</p>
      </div>
    </div>

    ${section(`Média por dia da semana (${P.label})`)}
    <div class="chart-container chart-h-290" id="chart-diag-dow"></div>
    ${best ? `<p class="section-note">${best.nome} é o dia mais forte (${brl(best.media)} em média) e ${worst.nome}, em vermelho, o mais fraco (${brl(worst.media)}):
      reforce o estoque para ${best.nome.toLowerCase()} e concentre promoções em ${worst.nome.toLowerCase()}.</p>` : ''}
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
      // No static labels on the reference line: with the current-year diamonds also labeled
      // (labelPos 'bottom' below), close months collided. Its value is still one hover away
      // (comboAxisTooltip in echarts-charts.js shows every series at that x-position).
      {name: `${R}`, type: 'line', values: S.ref.map((t) => (t ? t.revenue : null)), color: COR.gray, fmt: money},
      {name: `${P.y} (real)`, type: 'line', values: S.cur.map((t) => (t && !t.partial ? t.revenue : null)), color: COR.amber,
        marker: 'diamond', markerSize: 7, width: 3, fmt: money, labels: true, labelPos: 'bottom', labelFmt: brlShort},
      partialIdx >= 0 ? {name: `${MONTHS[partialIdx]}/${P.yy} (parcial)`, type: 'line', values: S.cur.map((t) => (t && t.partial ? t.revenue : null)),
        color: COR.orange, marker: 'diamond', markerSize: 7, fmt: money, tips: S.cur.map((t) => (t && t.partial ? `Dados até ${t.end.slice(8)}/${t.end.slice(5, 7)}` : ''))} : null,
    ],
    hlines: S.refAvg ? [{value: S.refAvg, label: `Média ${R}: ${brlShort(S.refAvg)}`, color: COR.gray, dash: '2 4'}] : [],
    yFmt: brlShort, yTitle: 'Faturamento (R$)',
  };
  const idxOpts = {
    labels: MONTHS, tipLabels: MESES_NOMES, yMin: 0,
    series: [{name: 'Índice', type: 'bar', values: S.index, fmt: dec2, labels: true,
      colors: S.index.map((v) => (v != null && v > 1 ? COR.green : COR.red)),
      tips: S.ref.map((t) => (t ? `Faturamento: ${money(t.revenue)}` : ''))}],
    hlines: [{value: 1, color: COR.gray, dash: '1 0', width: 2}],
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
    series: [{name: 'Faturamento 12 meses', type: 'line', values: rol.map((r) => r.value), color: COR.amber, fill: true, width: 3, fmt: money}],
    yFmt: brlShort, yTitle: 'Fat. acumulado 12m (R$)',
    empty: 'São necessários 12 meses completos de vendas sincronizadas para a média móvel.',
  };

  document.getElementById('content').innerHTML = `
    <div class="page-title">${icon('trending-up')} Sazonalidade e tendências</div>
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
  const fat = t.revenue, mg = t.margin;
  const nx = nextMonthProjection(data, S);
  const cp = nx.value * 0.85, cr = nx.value, co = nx.value * 1.15;
  // Commercial projection only: gross profit at the current margin. Expenses and
  // results after them belong to Financeiro (fluxo de caixa, resultado gerencial).
  const brutoDe = (v) => (mg == null ? null : v * mg / 100);
  const [lp, lr, lo] = [cp, cr, co].map(brutoDe);
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
      {name: `${S.R} (referência)`, type: 'line', values: S.ref.map((c) => (c ? c.revenue : null)), color: COR.gray, dash: '4 4', width: 2, fmt: money},
    ],
    yFmt: brlShort, yTitle: 'Faturamento (R$)',
  };


  const idxNext = S.index[nx.m - 1];
  const top5 = (data.categories || []).slice().sort((a, b) => b.revenue - a.revenue).slice(0, 5);
  const trendIcon = idxNext == null ? icon('chevron-right') : idxNext > 1.1 ? icon('flame') : idxNext < 0.9 ? icon('snowflake') : icon('chevron-right');

  const eros = erosionRows(data).filter((r) => r.erosao > EROSAO_LIMIAR);
  const prods = data.products || [];
  const estrelas = prods.filter((p) => p.classification === 'Estrela').sort((a, b) => (b.profit || 0) - (a.profit || 0));
  const pesoMorto = prods.filter((p) => p.classification === 'Baixo giro');
  const cmp = data.comparison;
  const vc = cmp && cmp.totals.receipts ? growth(t.receipts, cmp.totals.receipts) : null;

  const doList = [];
  if (eros.length) doList.push(`${icon('triangle-alert')} <strong>Reajustar preços</strong> de ${eros.length} produtos Curva A com custo subindo`);
  if (estrelas.length) doList.push(`${icon('star')} <strong>Garantir estoque</strong> dos top Estrelas: ${estrelas.slice(0, 3).map((p) => esc(p.name)).join(', ')}`);
  if (idxNext != null && idxNext > 1.05) doList.push(`${icon('trending-up')} <strong>Reforçar compras</strong> — ${nx.nome} é forte (índice ${dec2(idxNext)})`);
  else if (idxNext != null && idxNext < 0.95) doList.push(`${icon('megaphone')} <strong>Planejar promoções</strong> — ${nx.nome} é fraco (índice ${dec2(idxNext)})`);
  doList.push(`${icon('target')} <strong>Meta de faturamento</strong>: ${brl(cr)}`);
  if (lr != null) doList.push(`${icon('dollar-sign')} <strong>Lucro bruto esperado</strong>: ${brl(lr)} (margem bruta atual de ${pct1(mg)})`);
  const watchList = [];
  if (vc != null) watchList.push(`${icon('users')} <strong>Fluxo de clientes</strong>: variou ${signedPct(vc)} vs o mesmo período do ano anterior`);
  if (data.margin_goal_pct != null) watchList.push(`${icon('bar-chart-3')} <strong>Margem bruta</strong>: manter acima da meta de ${dec2(data.margin_goal_pct)}% (atual: ${pct1(mg)})`);
  watchList.push(`${icon('tag')} <strong>Erosão</strong>: ${eros.length} produtos precisam de reajuste`);
  if (pesoMorto.length > 50) watchList.push(`${icon('trash-2')} <strong>Baixo giro</strong>: ${num(pesoMorto.length)} produtos vendem pouco e ganham pouco — avaliar se vale manter`);

  const basisTxt = nx.basis === 'sazonal'
    ? `Projeção sazonal: ${nx.curto}/${String(S.R).slice(2)} × fator ${dec2(S.factor)}.`
    : `Sem ${nx.curto}/${String(nx.y - 1).slice(2)} para projetar pela sazonalidade — base = ritmo atual de ${P.label}.`;

  document.getElementById('content').innerHTML = `
    <div class="page-title">${icon('sparkles')} Projeções de vendas</div>
    <div class="page-subtitle">Baseado nos dados, o que esperar e como se preparar?</div>
    <span class="periodo-badge">${icon('calendar')} Base: ${P.nome}/${P.y}${P.partial ? ` · parcial até ${String(P.endDay).padStart(2, '0')}/${String(P.m).padStart(2, '0')}` : ''}</span>
    <hr class="divider">

    ${section(`${icon('bar-chart-3')} Projeção de Faturamento — ${P.y} Completo`)}
    <div class="chart-container chart-h-400" id="chart-visao-proj"></div>
    ${explain(`Projeção ${P.y}`, `Amarelo sólido = real. Amarelo transparente = projeção sazonal. Laranja = mês em andamento. Linha cinza = ${S.R}.`,
      S.factor != null ? `Fator de ajuste: ${dec2(S.factor)} (${P.y} está ${signedPct((S.factor - 1) * 100)} vs ${S.R} nos ${S.pairs} meses comparáveis). Projeção = mês de ${S.R} × fator.`
        : `Não há meses fechados comparáveis entre ${P.y} e ${S.R} — sem projeção sazonal.`,
      'Antecipar faturamento para planejar compras e caixa.')}
    <hr class="divider">

    ${section(`${icon('target')} Cenários para ${nx.nome}/${nx.y}`)}
    <div class="kpi-grid kpi-grid-3">
      ${kpi(`${icon('frown')} Pessimista (−15%)`, brl(cp), `Lucro bruto: ${brl(lp)}`, lp != null && lp < 0 ? 'kpi-negative' : 'kpi-neutral')}
      ${kpi(`${icon('bar-chart-3')} Realista`, brl(cr), `Lucro bruto: ${brl(lr)}`, lr != null && lr < 0 ? 'kpi-negative' : 'kpi-positive')}
      ${kpi(`${icon('rocket')} Otimista (+15%)`, brl(co), `Lucro bruto: ${brl(lo)}`, lo != null && lo < 0 ? 'kpi-negative' : 'kpi-positive')}
    </div>
    ${explain(`Cenários ${nx.nome}`, '3 cenários de receita sobre a projeção: pessimista (−15%), realista e otimista (+15%). Lucro bruto = receita × margem bruta atual; despesas e o resultado após despesas ficam no Financeiro.',
      `Hipóteses: margem bruta atual de ${pct1(mg)} e ${S.pairs || 0} mês(es) fechados comparáveis entre ${P.y} e ${S.R}.`, 'Planejar compras e metas comerciais.', basisTxt)}
    <hr class="divider">

    <section id="visao-break-even" aria-labelledby="visao-break-even-title" hidden></section>

    ${section(`${icon('calendar')} Cobertura histórica da projeção`)}
    ${story(`${S.pairs || 0} mês(es) fechados comparáveis entre ${P.y} e ${S.R}. ${basisTxt} ${P.partial ? `${P.label} está em andamento: ritmo projetado de ${brl(pace)} para o mês inteiro.` : ''}`)}
    <hr class="divider">

    ${section(`${icon('package')} Top 5 Categorias — Performance e Tendência`)}
    <div class="kpi-grid kpi-grid-5">
      ${top5.map((c) => `<div class="kpi-card"><div class="kpi-title">${esc(c.name)}</div><div class="kpi-value kpi-value-sm">${brl(c.revenue)}</div>
        <div class="kpi-subtitle">Margem: ${pct1(c.margin)} | ${trendIcon} ${nx.curto}: ${dec2(idxNext)}</div></div>`).join('')}
    </div>
    ${explain('Top 5 Categorias', `As 5 maiores categorias de ${P.label} + tendência sazonal de ${nx.nome} (índice de ${S.R}).`,
      `${icon('flame')} = mês forte (> 1,10). ${icon('snowflake')} = fraco (< 0,90). ${icon('chevron-right')} = normal ou sem histórico.`, `Reforçar estoque das ${icon('flame')} e promover as ${icon('snowflake')}.`)}
    <hr class="divider">

    ${section(`${icon('clipboard-list')} Direcionamento Estratégico — ${nx.nome}/${nx.y}`)}
    <div class="row">
      <div class="col-50">
        <h3 class="action-title">${icon('circle-check')} O que FAZER em ${nx.nome}</h3>
        <ul class="action-list">${doList.map((li) => `<li>${li}</li>`).join('')}</ul>
      </div>
      <div class="col-50">
        <h3 class="action-title">${icon('triangle-alert')} O que MONITORAR</h3>
        <ul class="action-list">${watchList.map((li) => `<li>${li}</li>`).join('')}</ul>
      </div>
    </div>
    ${explain('Plano de Ação', 'Gerado automaticamente com base nos dados e projeções.',
      'Ações priorizadas por impacto: margem → estoque → sazonalidade.', 'Revise com os sócios no início de cada mês.')}
  `;
  mountEchartCombo(document.getElementById('chart-visao-proj'), projOpts);
  loadVisaoBreakEven(data.period, {pace, fat, P});
}

/* Velocímetro: the month's revenue pace against the break-even point of the
 * same competence, read from the management result (cost center). Hidden for
 * profiles without finance access; unavailable inputs show the reason. */
async function loadVisaoBreakEven(period, ctx) {
  const box = document.getElementById('visao-break-even');
  if (!box || typeof managementResultUrl !== 'function') return;
  let result;
  try {
    result = await api(managementResultUrl(period));
  } catch (e) {
    return;
  }
  if (!box.isConnected) return;
  const be = result.break_even || {};
  const {pace, fat, P} = ctx;
  const title = `${section(`${icon('gauge')} Velocímetro — ${P.label} vs ponto de equilíbrio`)}`;
  if (be.break_even_cents == null) {
    box.innerHTML = `${title}${story(esc(be.reason || 'Ponto de equilíbrio indisponível.'))}<hr class="divider">`;
    box.hidden = false;
    return;
  }
  const point = be.break_even_cents, ideal = point * 1.5;
  box.innerHTML = `${title}
    <div class="chart-container chart-h-330" id="chart-visao-gauge"></div>
    ${explain('Velocímetro', `Vermelho = abaixo do ponto de equilíbrio (${brl(point)}). Amarelo = acima do equilíbrio. Verde = acima da meta ideal (${brl(ideal)} = 1,5× equilíbrio).`,
      `Ponto de equilíbrio = custos fixos lançados (${brl(be.fixed_costs_cents)}) ÷ margem de contribuição (${pctBR(be.contribution_margin_pct)}), da Central de custos.`,
      'Mostra se o mês cobre os custos fixos com folga.',
      P.partial ? `Realizado até ${String(P.endDay).padStart(2, '0')}/${String(P.m).padStart(2, '0')}: ${brl(fat)}. O ponteiro usa o ritmo diário projetado para o mês inteiro.` : undefined)}
    <hr class="divider">`;
  box.hidden = false;
  mountEchartGauge(document.getElementById('chart-visao-gauge'), {
    value: pace, max: ideal * 1.5, fmt: brl,
    steps: [{to: point, color: COR.badSoft}, {to: ideal, color: COR.warnSoft}, {to: ideal * 1.5, color: COR.goodSoft}],
    threshold: point, thresholdTip: `Ponto de equilíbrio: ${brl(point)}`,
    tip: P.partial ? `Ritmo projetado: ${brl(pace)}\nRealizado até ${String(P.endDay).padStart(2, '0')}/${String(P.m).padStart(2, '0')}: ${brl(fat)}` : `Faturamento: ${brl(fat)}`,
    ticks: [0, point, ideal, ideal * 1.5],
    title: P.partial ? `Faturamento projetado ${P.label} (ritmo de ${P.endDay} dias)` : `Faturamento ${P.label}`,
    delta: {value: pace - point, text: `${pace >= point ? '▲' : '▼'} ${brl(Math.abs(pace - point))} vs ponto de equilíbrio`},
  });
}

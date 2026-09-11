/* Mercado duBairro — Apache ECharts prototype (Fase 2 of the 2026-09-11 UX audit).
 *
 * Scope: ONLY the two Resumo Executivo charts ("Receita diária" line, "Histórico
 * mensal" bar) are wired to ECharts here, to test two things before a full
 * migration: (1) does it run clean under this app's CSP (script-src 'self';
 * style-src 'self', no 'unsafe-inline') and (2) does its theme actually read as
 * this brand. Every other chart on the site is still the hand-rolled SVG system
 * in charts.js/insights.js.
 *
 * ECharts is vendored locally (assets/vendor/echarts/, Apache-2.0 — LICENSE and
 * NOTICE included there) — never a CDN, the CSP would block it anyway. The
 * "simple" build is Canvas-only (line/bar/pie + grid/legend/title/tooltip
 * components, ~170 KB gzipped): a canvas element carries no per-point DOM nodes
 * to style, which sidesteps the CSP issue entirely for the chart body.
 *
 * The one real CSP risk is ECharts' built-in HTML tooltip: it sets inline
 * style attributes on a DOM node it injects, which fails under a strict
 * style-src (github.com/apache/echarts/issues/19938, still open in 6.1.0).
 * Rather than special-case that, tooltip.show is false everywhere here and
 * hover is driven through ECharts' own mouse events into the SAME .chart-tip
 * element setupChartTooltip() (app.js) already uses for the SVG charts — one
 * tooltip system, and it never touches style-src at all (position is set via
 * the CSSOM — el.style.left = ... — which style-src does not govern; only
 * literal style="" attributes and <style> blocks are restricted by CSP).
 */
'use strict';

const ECHARTS_INSTANCES = [];

function disposeEcharts() {
  ECHARTS_INSTANCES.forEach((c) => { try { c.dispose(); } catch (e) { /* already gone */ } });
  ECHARTS_INSTANCES.length = 0;
}

function resizeEcharts() {
  ECHARTS_INSTANCES.forEach((c) => { try { c.resize(); } catch (e) {} });
}

// Reads the brand's actual CSS custom properties (style.css :root) instead of duplicating
// hex values here, so the chart theme can never drift from the rest of the UI.
function brandTokens() {
  const s = getComputedStyle(document.documentElement);
  const v = (name, fallback) => (s.getPropertyValue(name) || fallback).trim() || fallback;
  return {
    dark: v('--dark', '#2D2D2D'), yellow: v('--yellow', '#FFC107'), amber: v('--amber', '#B7791F'),
    blue: v('--blue', '#2E86C1'), muted: v('--text-muted', '#6B6B6B'),
  };
}

function echartsTooltip(chart, formatter) {
  const tip = document.querySelector('.chart-tip');
  if (!tip) return;
  chart.on('mouseover', (p) => {
    const text = formatter(p);
    if (!text) return;
    tip.textContent = text;
    tip.classList.remove('hidden');
  });
  chart.on('mouseout', () => tip.classList.add('hidden'));
  chart.getZr().on('globalout', () => tip.classList.add('hidden'));
}

const baseAxis = (t, formatter) => ({
  axisLine: {lineStyle: {color: '#ddd'}},
  axisTick: {show: false},
  axisLabel: {color: t.muted, fontSize: 11, fontFamily: 'inherit', formatter},
});

// 'YYYY-MM-DD' -> 'dd/mm' (daily chart labels). MONTHS is app.js's global pt-BR month
// list — referenced only inside this call-time formatter, so script load order is fine.
const dayMonthLabel = (v) => /^\d{4}-\d{2}-\d{2}$/.test(v) ? v.slice(8, 10) + '/' + v.slice(5, 7) : v;
// Full precision for the tooltip (the axis only has room for dd/mm).
const fullDateLabel = (v) => /^\d{4}-\d{2}-\d{2}$/.test(v) ? v.slice(8, 10) + '/' + v.slice(5, 7) + '/' + v.slice(0, 4) : v;
// 'YYYY-MM' -> 'mmm/aa' (monthly chart labels), matching how the period badge reads elsewhere.
const monthYearLabel = (v) => /^\d{4}-\d{2}$/.test(v)
  ? MONTHS[parseInt(v.slice(5, 7), 10) - 1] + '/' + v.slice(2, 4) : v;

function mountEchartLine(el, points) {
  if (!el) return;
  if (!points.length) { el.innerHTML = '<div class="chart-empty">Sem dados no período.</div>'; return; }
  const t = brandTokens();
  const chart = echarts.init(el, null, {renderer: 'canvas'});
  ECHARTS_INSTANCES.push(chart);
  chart.setOption({
    animationDuration: 300,
    grid: {left: 8, right: 16, top: 16, bottom: 28, containLabel: true},
    tooltip: {show: false},
    xAxis: Object.assign({type: 'category', data: points.map((p) => p.label), boundaryGap: false}, baseAxis(t, dayMonthLabel)),
    yAxis: Object.assign({type: 'value', splitLine: {lineStyle: {color: '#f0f0f0'}},
      axisLabel: {color: t.muted, fontSize: 11, formatter: (v) => 'R$' + compactNum(v)}}, {axisLine: {show: false}}),
    series: [{
      type: 'line', data: points.map((p) => p.value), symbol: 'circle', symbolSize: 6,
      lineStyle: {color: t.amber, width: 2.5}, itemStyle: {color: t.dark, borderColor: '#fff', borderWidth: 1},
      areaStyle: {color: t.yellow, opacity: 0.12},
    }],
  });
  echartsTooltip(chart, (p) => `${fullDateLabel(points[p.dataIndex].label)}\n${points[p.dataIndex].display}`);
}

function mountEchartBar(el, points) {
  if (!el) return;
  if (!points.length) { el.innerHTML = '<div class="chart-empty">Sem dados.</div>'; return; }
  const t = brandTokens();
  const chart = echarts.init(el, null, {renderer: 'canvas'});
  ECHARTS_INSTANCES.push(chart);
  chart.setOption({
    animationDuration: 300,
    grid: {left: 8, right: 16, top: 16, bottom: 28, containLabel: true},
    tooltip: {show: false},
    xAxis: Object.assign({type: 'category', data: points.map((p) => p.label)}, baseAxis(t, monthYearLabel)),
    yAxis: {type: 'value', splitLine: {lineStyle: {color: '#f0f0f0'}}, axisLine: {show: false},
      axisLabel: {color: t.muted, fontSize: 11, formatter: (v) => 'R$' + compactNum(v)}},
    series: [{type: 'bar', data: points.map((p) => p.value), barMaxWidth: 36,
      itemStyle: {color: t.blue, borderRadius: [3, 3, 0, 0]},
      emphasis: {itemStyle: {color: t.dark}}}],
  });
  echartsTooltip(chart, (p) => `${monthYearLabel(points[p.dataIndex].label)}\n${points[p.dataIndex].display}`);
}

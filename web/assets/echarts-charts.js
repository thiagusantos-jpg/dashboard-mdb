/* Mercado duBairro — Apache ECharts chart engine, replacing the hand-rolled SVG
 * charts (charts.js) across every page. Fase 2 (2026-09-11 UX audit) proved this
 * runs clean under the CSP and reads as the brand on Resumo's two time-series
 * charts; Fase 3 below carries that to Inteligência de Preços, Mapa de Produtos,
 * Diagnóstico, Sazonalidade and Visão Futurista's charts.
 *
 * ECharts is vendored locally (assets/vendor/echarts/, Apache-2.0 — LICENSE and
 * NOTICE included there) — never a CDN, the CSP would block it anyway. The
 * "common" build is Canvas-only (line/bar/scatter/heatmap/gauge/pie + grid/
 * legend/title/tooltip/markLine/markPoint components, ~240 KB gzipped): a
 * canvas has no per-point DOM to style, which sidesteps the CSP issue entirely
 * for the chart body.
 *
 * The one real CSP risk is ECharts' built-in HTML tooltip: it sets inline style
 * attributes on a DOM node it injects, which fails under a strict style-src
 * (github.com/apache/echarts/issues/19938, still open in 6.1.0). Rather than
 * special-case that, tooltip.show is false everywhere here and hover is driven
 * through ECharts' own mouse/zrender events into the SAME .chart-tip element
 * setupChartTooltip() (app.js) already uses — one tooltip system, and it never
 * touches style-src at all (position is set via the CSSOM — el.style.left =
 * ... — which style-src does not govern; only literal style="" attributes and
 * <style> blocks are restricted by CSP).
 *
 * Design: each mount* function below takes THE SAME option shape the SVG
 * function it replaces took (charts.js's comboChart/scatterChart/heatmapChart),
 * so insights.js's page renderers — all the carefully-tuned business logic:
 * quadrant cuts, erosion thresholds, seasonality math — do not change at all,
 * only the `${xyzChart(opts)}` inline-SVG call becomes a placeholder <div> plus
 * a mount*(el, opts) call after the page's innerHTML is set. gaugeChart() (the
 * Visão Futurista break-even speedometer) is intentionally NOT ported here: its
 * arbitrary (non-evenly-spaced) tick positions have no clean equivalent in
 * ECharts' gauge component without hand-placed graphic overlays that would not
 * reposition on resize — left on the old SVG renderer pending a follow-up.
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

/* Horizontal bar — Resumo's "Top 10 produtos por lucro" and any other ranked-name
 * list, where vertical bars force truncated, overlapping product names. */
function mountEchartBarH(el, points) {
  if (!el) return;
  if (!points.length) { el.innerHTML = '<div class="chart-empty">Sem dados.</div>'; return; }
  const t = brandTokens();
  const chart = echarts.init(el, null, {renderer: 'canvas'});
  ECHARTS_INSTANCES.push(chart);
  chart.setOption({
    animationDuration: 300,
    grid: {left: 8, right: 48, top: 8, bottom: 8, containLabel: true},
    tooltip: {show: false},
    xAxis: {type: 'value', show: false},
    yAxis: {type: 'category', data: points.map((p) => p.label), inverse: true,
      axisLine: {show: false}, axisTick: {show: false}, axisLabel: {color: t.dark, fontSize: 12}},
    series: [{type: 'bar', data: points.map((p) => p.value), barMaxWidth: 22,
      itemStyle: {color: t.blue, borderRadius: [0, 3, 3, 0]}, emphasis: {itemStyle: {color: t.dark}},
      label: {show: true, position: 'right', color: t.muted, fontSize: 11, formatter: (p) => points[p.dataIndex].display}}],
  });
  echartsTooltip(chart, (p) => `${points[p.dataIndex].label}\n${points[p.dataIndex].display}`);
}

/* ================================================================ Fase 3 adapters
 * mountEchartCombo / mountEchartScatter / mountEchartHeatmap take the exact option
 * shape the SVG comboChart()/scatterChart()/heatmapChart() (charts.js) took. */

function dashArray(dash) {
  // SVG stroke-dasharray syntax ('2 4', '1 0'/solid) as already written at every
  // call site in insights.js -> ECharts' lineStyle.type number-array form.
  return dash ? dash.trim().split(/\s+/).map(Number) : undefined;
}

// Tooltip for a combo chart: hovering anywhere in the plot shows every series' value
// at that x-position (like the SVG version's full-column hover band), not just the
// nearest point — closer to what a multi-series comparison chart needs than the
// single-point hover echartsTooltip() gives the line/bar charts above.
function comboAxisTooltip(chart, labels, tipLabels, series) {
  const tip = document.querySelector('.chart-tip');
  if (!tip) return;
  const el = chart.getDom();
  const hide = () => tip.classList.add('hidden');
  chart.getZr().on('mousemove', (e) => {
    const point = [e.offsetX, e.offsetY];
    if (!chart.containPixel({gridIndex: 0}, point)) return hide();
    const [xVal] = chart.convertFromPixel({xAxisIndex: 0}, point);
    const idx = Math.max(0, Math.min(labels.length - 1, Math.round(xVal)));
    const lines = series.map((s) => {
      const v = s.values[idx];
      if (v == null || !isFinite(v)) return null;
      const fmt = s.fmt || String;
      const extra = s.tips && s.tips[idx] ? ` — ${s.tips[idx]}` : '';
      return `${s.name}: ${fmt(v)}${extra}`;
    }).filter(Boolean);
    if (!lines.length) return hide();
    tip.textContent = `${tipLabels ? tipLabels[idx] : labels[idx]}\n${lines.join('\n')}`;
    tip.classList.remove('hidden');
  });
  chart.getZr().on('globalout', hide);
  el.addEventListener('mouseleave', hide);
}

/* Mixed bar/line categorical chart — replaces comboChart(). Supports: bar+line series
 * mixed (optionally on a second/right axis), per-point bar colors/strokes, dashed/
 * filled lines, diamond/circle/no markers, on-chart value labels, overlay bar mode
 * (stacked-at-same-x, for real/projected/in-progress bars), reference hlines, rotated
 * x labels, and a legend (native — series can be toggled by clicking it, same as the
 * SVG version's custom click-to-filter legend, no extra code needed here for that). */
function mountEchartCombo(el, o) {
  if (!el) return;
  const t = brandTokens();
  const series = (o.series || []).filter(Boolean);
  const anyValue = series.some((s) => s.values.some((v) => v != null && isFinite(v)));
  if (!o.labels || !o.labels.length || !anyValue) {
    el.innerHTML = `<div class="chart-empty">${esc(o.empty || 'Sem dados.')}</div>`;
    return;
  }
  const hasY2 = series.some((s) => s.axis === 'right');
  const markerSize = (s) => (s.markerSize || (s.marker === 'diamond' ? 6 : 4)) * 2;
  const echartSeries = series.map((s) => {
    if (s.type === 'bar') {
      const data = s.values.map((v, i) => {
        const color = s.colors ? s.colors[i] : s.color;
        const stroke = s.strokes ? s.strokes[i] : s.stroke;
        return {value: v, itemStyle: stroke ? {color, borderColor: stroke, borderWidth: 1.5} : {color}};
      });
      return {name: s.name, type: 'bar', data, yAxisIndex: s.axis === 'right' ? 1 : 0,
        itemStyle: {opacity: s.opacity == null ? 1 : s.opacity, borderRadius: [2, 2, 0, 0]},
        barGap: o.barMode === 'overlay' ? '-100%' : undefined,
        label: s.labels ? {show: true, position: 'top', color: t.muted, fontSize: 10,
          formatter: (p) => (s.labelFmt || s.fmt || String)(p.value)} : undefined};
    }
    return {name: s.name, type: 'line', data: s.values, yAxisIndex: s.axis === 'right' ? 1 : 0,
      symbol: s.marker === 'none' ? 'none' : s.marker === 'diamond' ? 'diamond' : 'circle',
      symbolSize: markerSize(s), connectNulls: false,
      lineStyle: {color: s.color, width: s.width || 2.5, type: dashArray(s.dash) || 'solid'},
      itemStyle: {color: s.color},
      areaStyle: s.fill ? {color: s.color, opacity: 0.12} : undefined,
      label: s.labels ? {show: true, position: s.labelPos === 'bottom' ? 'bottom' : 'top',
        color: t.muted, fontSize: 10, formatter: (p) => (s.labelFmt || s.fmt || String)(p.value)} : undefined};
  });
  // Reference lines (hlines) attach to the first series sharing their axis — markLine
  // data coordinates are relative to that series but the line itself spans the grid.
  (o.hlines || []).forEach((h) => {
    const target = echartSeries.find((s, i) => (series[i].axis === 'right') === (h.axis === 'right')) || echartSeries[0];
    if (!target) return;
    target.markLine = target.markLine || {silent: true, symbol: 'none', data: [],
      lineStyle: {}, label: {formatter: '', show: false}};
    target.markLine.data.push({yAxis: h.value});
    target.markLine.lineStyle = {color: h.color || '#999', width: h.width || 1.5, type: dashArray(h.dash) || [5, 4]};
    if (h.label) target.markLine.label = {show: true, formatter: h.label, position: 'end', color: h.color || '#888', fontSize: 10};
  });

  const chart = echarts.init(el, null, {renderer: 'canvas'});
  ECHARTS_INSTANCES.push(chart);
  const yAxis = [{type: 'value', name: o.yTitle, nameTextStyle: {color: t.muted, fontSize: 11},
    min: o.yMin, splitLine: {lineStyle: {color: '#f0f0f0'}}, axisLine: {show: false},
    axisLabel: {color: t.muted, fontSize: 11, formatter: o.yFmt || compactNum}}];
  if (hasY2) yAxis.push({type: 'value', name: o.y2Title, nameTextStyle: {color: t.muted, fontSize: 11},
    splitLine: {show: false}, axisLine: {show: false}, axisLabel: {color: t.muted, fontSize: 11, formatter: o.y2Fmt || compactNum}});
  chart.setOption({
    animationDuration: 300,
    grid: {left: 8, right: hasY2 ? 8 : 16, top: 16, bottom: o.rotate ? 76 : (series.length > 1 ? 40 : 28), containLabel: true},
    tooltip: {show: false},
    legend: series.length > 1 ? {bottom: 0, textStyle: {color: t.muted, fontSize: 11}, icon: 'roundRect'} : undefined,
    xAxis: Object.assign({type: 'category', data: o.labels, boundaryGap: true},
      baseAxis(t, undefined), o.rotate ? {axisLabel: {color: t.muted, fontSize: 11, rotate: 40}} : {}),
    yAxis,
    series: echartSeries,
  });
  comboAxisTooltip(chart, o.labels, o.tipLabels, series);
}

/* Bubble/scatter chart — replaces scatterChart(). One ECharts series per group, so
 * the native legend both labels AND toggles groups (click to filter), same
 * interaction the SVG version's custom legend gave, natively. Quadrant/annotation
 * text is a silent zero-size scatter series (positioned in data coordinates, so it
 * stays put if the axis range ever changes) rather than pixel-positioned graphics. */
function mountEchartScatter(el, o) {
  if (!el) return;
  const points = o.points || [];
  if (!points.length) { el.innerHTML = `<div class="chart-empty">${esc(o.empty || 'Sem dados.')}</div>`; return; }
  const t = brandTokens();
  const groups = o.groups || [{name: '', color: t.blue}];
  const series = groups.map((g, gi) => ({
    name: g.name, type: 'scatter',
    data: points.filter((p) => p.g === gi).map((p) => ({value: [p.x, p.y], symbolSize: p.r * 2, tipText: p.tip})),
    itemStyle: {color: g.color, opacity: 0.62, borderColor: '#fff', borderWidth: 0.8},
    emphasis: {itemStyle: {opacity: 0.9}},
  }));
  if (o.annotations && o.annotations.length) {
    series.push({name: '__annotations', type: 'scatter', silent: true, symbolSize: 0, tooltip: {show: false},
      data: o.annotations.map((a) => ({value: [a.x, a.y]})),
      label: {show: true, formatter: (p) => o.annotations[p.dataIndex].text,
        color: (p) => o.annotations[p.dataIndex].color || '#888', fontWeight: 700, fontSize: 12}});
  }
  const chart = echarts.init(el, null, {renderer: 'canvas'});
  ECHARTS_INSTANCES.push(chart);
  const scaleOpt = (s) => s ? {min: s.min, max: s.max} : {};
  const xAxis = Object.assign({type: 'value', name: o.xTitle, nameLocation: 'middle', nameGap: 28,
    nameTextStyle: {color: t.muted, fontSize: 11}, splitLine: {lineStyle: {color: '#f0f0f0'}}}, baseAxis(t, o.xFmt), scaleOpt(o.xScale));
  const yAxis = Object.assign({type: 'value', name: o.yTitle, nameTextStyle: {color: t.muted, fontSize: 11},
    splitLine: {lineStyle: {color: '#f0f0f0'}}, axisLine: {show: false},
    axisLabel: {color: t.muted, fontSize: 11, formatter: o.yFmt}}, scaleOpt(o.yScale));
  (o.vlines || []).forEach((v, i) => {
    series[0].markLine = series[0].markLine || {silent: true, symbol: 'none', data: [], lineStyle: {color: '#999', type: [5, 4]}};
    series[0].markLine.data.push({xAxis: v.x, label: v.label ? {show: true, formatter: v.label, color: '#888', fontSize: 10} : {show: false}});
  });
  (o.hlines || []).forEach((h) => {
    series[0].markLine = series[0].markLine || {silent: true, symbol: 'none', data: [], lineStyle: {color: '#999', type: [5, 4]}};
    series[0].markLine.data.push({yAxis: h.y, label: h.label ? {show: true, formatter: h.label, color: '#888', fontSize: 10} : {show: false}});
  });
  chart.setOption({
    animationDuration: 300,
    grid: {left: 8, right: 16, top: 16, bottom: 44, containLabel: true},
    tooltip: {show: false},
    legend: groups.length > 1 ? {bottom: 0, textStyle: {color: t.muted, fontSize: 11}, icon: 'circle',
      data: groups.map((g) => g.name)} : undefined,
    xAxis, yAxis, series,
  });
  echartsTooltip(chart, (p) => p.data && p.data.tipText);
}

/* Weekday × week-of-month heatmap — replaces heatmapChart(). Colors are computed with
 * the exact same heatColor() gradient charts.js already uses for the SVG version
 * (set directly per-cell) rather than delegating to ECharts' visualMap component, so
 * the two engines can never drift into slightly different color scales. */
function mountEchartHeatmap(el, o) {
  if (!el) return;
  const cells = o.cells || [];
  if (!cells.length) { el.innerHTML = `<div class="chart-empty">${esc(o.empty || 'Sem dados.')}</div>`; return; }
  const t = brandTokens();
  const vals = cells.map((c) => c.value);
  const lo = Math.min(...vals), hi = Math.max(...vals), span = (hi - lo) || 1;
  const data = cells.map((c) => {
    const norm = (c.value - lo) / span;
    return {value: [c.c, c.r, c.value], tipText: c.tip,
      itemStyle: {color: heatColor(norm), borderColor: '#fff', borderWidth: 2},
      label: {show: true, formatter: c.text, color: norm > 0.55 ? '#fff' : t.dark, fontSize: 11, fontWeight: 600}};
  });
  const chart = echarts.init(el, null, {renderer: 'canvas'});
  ECHARTS_INSTANCES.push(chart);
  chart.setOption({
    animationDuration: 300,
    grid: {left: 8, right: 8, top: 8, bottom: 8, containLabel: true},
    tooltip: {show: false},
    xAxis: {type: 'category', data: o.cols, splitArea: {show: false}, position: 'top',
      axisLine: {show: false}, axisTick: {show: false}, axisLabel: {color: t.muted, fontSize: 11}},
    yAxis: {type: 'category', data: o.rows, splitArea: {show: false},
      axisLine: {show: false}, axisTick: {show: false}, axisLabel: {color: t.muted, fontSize: 11}},
    series: [{type: 'heatmap', data, itemStyle: {borderRadius: 4},
      emphasis: {itemStyle: {borderColor: t.dark, borderWidth: 2}}}],
  });
  echartsTooltip(chart, (p) => p.data && p.data.tipText);
}

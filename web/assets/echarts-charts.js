/* Mercado duBairro — Apache ECharts chart engine. Every chart on the site (Fase
 * 2/3 of the 2026-09-11 UX audit, plus the break-even gauge) runs through here
 * now; charts.js keeps only the math/format helpers this file still calls
 * directly (niceScale, compactNum, bubbleRadius, heatColor).
 *
 * ECharts is vendored locally (assets/vendor/echarts/, Apache-2.0 — LICENSE and
 * NOTICE included there) — never a CDN, the CSP would block it anyway. The full
 * build (`echarts.min.js`) is used deliberately: the smaller `common`/`simple`
 * builds were tried and rejected (see vendor/echarts/README.md) — `common`
 * silently drops the `heatmap` series with no warning, `simple` also lacks
 * markLine/markPoint. Canvas renderer only, no SVG: a canvas has no per-point
 * DOM to style, which sidesteps the CSP issue entirely for the chart body.
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
 * function it replaces took (charts.js's former comboChart/scatterChart/
 * heatmapChart/gaugeChart), so insights.js's page renderers — all the
 * carefully-tuned business logic: quadrant cuts, erosion thresholds,
 * seasonality math, break-even zones — do not change at all, only the
 * `${xyzChart(opts)}` inline-SVG call becomes a placeholder <div> plus a
 * mount*(el, opts) call after the page's innerHTML is set.
 */
'use strict';

const ECHARTS_INSTANCES = [];

function disposeEcharts() {
  ECHARTS_INSTANCES.forEach((c) => { try { c.dispose(); } catch (e) { /* already gone */ } });
  ECHARTS_INSTANCES.length = 0;
}

function resizeEcharts() {
  // A gauge's tick labels/threshold marker are hand-placed graphic elements (pixel
  // coordinates, not chart data) — mountEchartGauge stashes a recompute callback on
  // the instance so they move with the gauge instead of staying put through a resize.
  ECHARTS_INSTANCES.forEach((c) => { try { c.resize(); if (c._reflow) c._reflow(); } catch (e) {} });
}

// ECharts' entry animation is JS-driven, so the CSS prefers-reduced-motion media query
// (style.css) cannot turn it off on its own — checked once per mount instead.
const CHART_ANIM_MS = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 300;

// Reads the brand's actual CSS custom properties (style.css :root) instead of duplicating
// hex values here, so the chart theme can never drift from the rest of the UI.
function brandTokens() {
  const s = getComputedStyle(document.documentElement);
  const v = (name, fallback) => (s.getPropertyValue(name) || fallback).trim() || fallback;
  return {
    dark: v('--dark', '#2D2D2D'), yellow: v('--yellow', '#FFC107'), amber: v('--amber', '#B7791F'),
    blue: v('--blue', '#2E86C1'), muted: v('--text-muted', '#6B6B6B'),
    // Only defined by the dark theme (style.css); the fallbacks are the light values.
    grid: v('--chart-grid', '#f0f0f0'), axis: v('--chart-axis', '#ddd'), surface: v('--chart-surface', '#fff'),
    weak: v('--chart-weak', '#E9A89F'),
  };
}

// Canvas has no per-point DOM, so a screen reader gets nothing from the chart itself —
// this appends a visually-hidden (.sr-only, style.css) data table right after the chart
// container with the same values ECharts just drew, one call per mount* function below.
function srDataTable(el, caption, headers, rows) {
  if (!el || !rows || !rows.length) return;
  const old = el.nextElementSibling;
  if (old && old.classList.contains('sr-only') && old.tagName === 'TABLE') old.remove();
  const html = `<table class="sr-only"><caption>${esc(caption)}</caption><thead><tr>${
    headers.map((h) => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${
    rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c == null ? '—' : String(c))}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  el.insertAdjacentHTML('afterend', html);
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
  axisLine: {lineStyle: {color: t.axis}},
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
    animationDuration: CHART_ANIM_MS,
    grid: {left: 8, right: 16, top: 16, bottom: 28, containLabel: true},
    tooltip: {show: false},
    xAxis: Object.assign({type: 'category', data: points.map((p) => p.label), boundaryGap: false}, baseAxis(t, dayMonthLabel)),
    yAxis: Object.assign({type: 'value', splitLine: {lineStyle: {color: t.grid}},
      axisLabel: {color: t.muted, fontSize: 11, formatter: (v) => 'R$' + compactNum(v)}}, {axisLine: {show: false}}),
    series: [{
      type: 'line', data: points.map((p) => p.value), symbol: 'circle', symbolSize: 6,
      lineStyle: {color: t.amber, width: 2.5}, itemStyle: {color: t.dark, borderColor: t.surface, borderWidth: 1},
      areaStyle: {color: t.yellow, opacity: 0.12},
    }],
  });
  echartsTooltip(chart, (p) => `${fullDateLabel(points[p.dataIndex].label)}\n${points[p.dataIndex].display}`);
}

/* Resumo's daily revenue: one bar per day with the weekday under the date (the
 * weekly rhythm is what explains a weak day), a dashed month average, days under
 * half of it in a muted red and today's still-open day faded. `stats` comes from
 * app.js dailyStats(); revenue is in cents. */
function mountEchartDaily(el, stats) {
  if (!el) return;
  if (!stats || !stats.days.length) { el.innerHTML = '<div class="chart-empty">Sem dados no período.</div>'; return; }
  const t = brandTokens();
  const chart = echarts.init(el, null, {renderer: 'canvas'});
  ECHARTS_INSTANCES.push(chart);
  const avg = stats.avg == null ? null : stats.avg / 100;
  chart.setOption({
    animationDuration: CHART_ANIM_MS,
    grid: {left: 8, right: 16, top: 26, bottom: 4, containLabel: true},
    tooltip: {show: false},
    xAxis: Object.assign({type: 'category', data: stats.days.map((d) => d.date)},
      baseAxis(t, (v, i) => `${v.slice(8, 10)}\n${stats.days[i].weekday}`), {axisLabel: {
        color: t.muted, fontSize: 11, lineHeight: 14, interval: 0,
        formatter: (v, i) => `${v.slice(8, 10)}\n${stats.days[i].weekday}`}}),
    yAxis: {type: 'value', splitLine: {lineStyle: {color: t.grid}}, axisLine: {show: false},
      axisLabel: {color: t.muted, fontSize: 11, formatter: (v) => 'R$' + compactNum(v)}},
    series: [{
      type: 'bar', barMaxWidth: 28,
      data: stats.days.map((d) => ({value: d.revenue / 100, itemStyle: {
        color: d.weak ? t.weak : t.yellow, opacity: d.inProgress ? 0.45 : 1, borderRadius: [3, 3, 0, 0]}})),
      markLine: avg == null ? undefined : {silent: true, symbol: 'none', data: [{yAxis: avg}],
        lineStyle: {color: t.dark, width: 1.5, type: [5, 4]},
        label: {show: true, position: 'insideEndTop', formatter: 'Média R$' + compactNum(avg), color: t.dark, fontSize: 11, fontWeight: 600}},
    }],
  });
  echartsTooltip(chart, (p) => {
    const d = stats.days[p.dataIndex];
    return `${fullDateLabel(d.date)} (${d.weekday})\n${money(d.revenue)}${d.inProgress ? '\nDia em andamento' : ''}`;
  });
  srDataTable(el, 'Receita diária', ['Dia', 'Receita'],
    stats.days.map((d) => [`${fullDateLabel(d.date)} (${d.weekday})`, money(d.revenue)]));
}

function mountEchartBar(el, points) {
  if (!el) return;
  if (!points.length) { el.innerHTML = '<div class="chart-empty">Sem dados.</div>'; return; }
  const t = brandTokens();
  const chart = echarts.init(el, null, {renderer: 'canvas'});
  ECHARTS_INSTANCES.push(chart);
  chart.setOption({
    animationDuration: CHART_ANIM_MS,
    grid: {left: 8, right: 16, top: 16, bottom: 28, containLabel: true},
    tooltip: {show: false},
    xAxis: Object.assign({type: 'category', data: points.map((p) => p.label)}, baseAxis(t, monthYearLabel)),
    yAxis: {type: 'value', splitLine: {lineStyle: {color: t.grid}}, axisLine: {show: false},
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
    animationDuration: CHART_ANIM_MS,
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
      // itemStyle.color here is only what the legend swatch reads (mirrors the SVG version's
      // same fallback) — each bar's real color still comes from its own data[i].itemStyle above.
      return {name: s.name, type: 'bar', data, yAxisIndex: s.axis === 'right' ? 1 : 0,
        itemStyle: {color: s.color || (s.colors && s.colors[0]) || '#999', opacity: s.opacity == null ? 1 : s.opacity, borderRadius: [2, 2, 0, 0]},
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
    min: o.yMin, splitLine: {lineStyle: {color: t.grid}}, axisLine: {show: false},
    axisLabel: {color: t.muted, fontSize: 11, formatter: o.yFmt || compactNum}}];
  if (hasY2) yAxis.push({type: 'value', name: o.y2Title, nameTextStyle: {color: t.muted, fontSize: 11},
    splitLine: {show: false}, axisLine: {show: false}, axisLabel: {color: t.muted, fontSize: 11, formatter: o.y2Fmt || compactNum}});
  chart.setOption({
    animationDuration: CHART_ANIM_MS,
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
    itemStyle: {color: g.color, opacity: 0.62, borderColor: t.surface, borderWidth: 0.8},
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
    nameTextStyle: {color: t.muted, fontSize: 11}, splitLine: {lineStyle: {color: t.grid}}}, baseAxis(t, o.xFmt), scaleOpt(o.xScale));
  const yAxis = Object.assign({type: 'value', name: o.yTitle, nameTextStyle: {color: t.muted, fontSize: 11},
    splitLine: {lineStyle: {color: t.grid}}, axisLine: {show: false},
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
    animationDuration: CHART_ANIM_MS,
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
      itemStyle: {color: heatColor(norm), borderColor: t.surface, borderWidth: 2},
      label: {show: true, formatter: c.text, color: norm > 0.55 ? '#fff' : t.dark, fontSize: 11, fontWeight: 600}};
  });
  const chart = echarts.init(el, null, {renderer: 'canvas'});
  ECHARTS_INSTANCES.push(chart);
  chart.setOption({
    animationDuration: CHART_ANIM_MS,
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

/* Semicircle break-even gauge — replaces gaugeChart(). Colored zones, the value
 * progress arc, and the value+delta text are all native ECharts gauge sub-options
 * (they reflow for free when center/radius are recomputed below). The one thing the
 * gauge series can't do — 4 tick labels at ARBITRARY, non-evenly-spaced positions
 * (0 / ponto de equilíbrio / meta ideal / topo) plus the red threshold line at the
 * break-even point — is drawn as `graphic` elements using the exact same polar
 * (angle, radius) math the old SVG gaugeChart() used, recomputed on every resize
 * via the chart's stashed _reflow() (see resizeEcharts() above) so they track the
 * gauge instead of drifting once the container's width changes. */
function mountEchartGauge(el, o) {
  if (!el) return;
  const t = brandTokens();
  const chart = echarts.init(el, null, {renderer: 'canvas'});
  ECHARTS_INSTANCES.push(chart);
  const max = o.max > 0 ? o.max : 1;
  const tip = document.querySelector('.chart-tip');

  const layout = () => {
    const W = chart.getWidth() || 560, H = 330;
    const cx = W / 2, cy = H - 70, R = Math.min(W / 2 - 60, 170), thick = 34;
    const ang = (v) => Math.PI * (1 - Math.max(0, Math.min(max, v)) / max);
    const pt = (a, r) => [cx + r * Math.cos(a), cy - r * Math.sin(a)];
    const zoneColors = (o.steps || []).map((s) => [Math.min(1, Math.max(0, s.to / max)), s.color]);

    const graphics = [];
    (o.ticks || []).forEach((v) => {
      const a = ang(v);
      const [x, y] = pt(a, R + thick / 2 + 12);
      graphics.push({type: 'text', x, y,
        style: {text: o.fmt(v), fill: '#777', fontSize: 11, font: '11px sans-serif',
          align: a > Math.PI * 0.6 ? 'right' : a < Math.PI * 0.4 ? 'left' : 'center', verticalAlign: 'middle'}});
    });
    if (o.threshold != null) {
      const a = ang(o.threshold);
      const [x0, y0] = pt(a, R - thick * 0.62), [x1, y1] = pt(a, R + thick * 0.62);
      graphics.push({type: 'line', shape: {x1: x0, y1: y0, x2: x1, y2: y1}, z: 10,
        style: {stroke: '#E74C3C', lineWidth: 4}, cursor: 'default',
        onmouseover: () => { if (tip && o.thresholdTip) { tip.textContent = o.thresholdTip; tip.classList.remove('hidden'); } },
        onmouseout: () => tip && tip.classList.add('hidden')});
    }
    chart.setOption({
      graphic: {elements: graphics},
      series: [{
        type: 'gauge', startAngle: 180, endAngle: 0, min: 0, max,
        center: [cx, cy], radius: R,
        axisLine: {lineStyle: {width: thick, color: zoneColors.length ? zoneColors : [[1, t.grid]]}},
        progress: {show: true, width: thick * 0.42, itemStyle: {color: o.color || t.amber}},
        pointer: {show: false}, anchor: {show: false},
        axisTick: {show: false}, splitLine: {show: false}, axisLabel: {show: false},
        title: {show: !!o.title, offsetCenter: [0, 18 - cy], color: t.dark, fontSize: 14, fontWeight: 600},
        detail: {show: true, offsetCenter: [0, -6], formatter: () => `{main|${o.fmt(o.value)}}${o.delta ? `\n{delta|${o.delta.text}}` : ''}`,
          rich: {main: {fontSize: 30, fontWeight: 700, color: t.dark, lineHeight: 36},
            delta: {fontSize: 14, fontWeight: 600, lineHeight: 20, color: o.delta && o.delta.value >= 0 ? '#1E8449' : '#C0392B'}}},
        data: [{value: o.value, name: o.title || ''}],
      }],
    }, {replaceMerge: ['graphic']});
  };

  layout();
  chart._reflow = layout;
  // componentType is 'series' for the gauge's own value arc/pointer and 'graphic' for the
  // hand-placed tick labels and threshold line — those already carry their own onmouseover
  // (above) and must not be clobbered by this catch-all firing right after on the same hover.
  echartsTooltip(chart, (p) => p.componentType === 'series' ? (o.tip || '') : '');
}

/* Loaded on demand (task C7). echarts.min.js is 1.12 MB (368 KB gzip), about
 * three quarters of all JavaScript, and login, Configurações and the whole
 * Financeiro module never draw a chart. The CSP (script-src 'self') allows a
 * same-origin script element. A failed download is not remembered, so the
 * next chart page tries again. `var` keeps the state visible to unit tests. */
var ECHARTS_SRC = 'assets/vendor/echarts/echarts.min.js?v=6.1.0';
var echartsLoading = null;

function ensureEcharts() {
  if (typeof window !== 'undefined' && window.echarts) return Promise.resolve();
  if (echartsLoading) return echartsLoading;
  echartsLoading = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = ECHARTS_SRC;
    script.onload = () => resolve();
    script.onerror = () => {
      echartsLoading = null;
      reject(new Error('Não foi possível carregar os gráficos.'));
    };
    document.head.appendChild(script);
  });
  return echartsLoading;
}

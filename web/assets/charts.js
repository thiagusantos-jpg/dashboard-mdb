/* Mercado duBairro — math/format helpers still used directly by echarts-charts.js
 * and insights.js's business logic. Every chart itself moved to ECharts
 * (echarts-charts.js) across Fase 2/3 of the 2026-09-11 UX audit, gaugeChart()
 * (the last hand-rolled SVG chart, Visão Futurista's break-even speedometer)
 * included — see mountEchartGauge() there for how its 4 arbitrary tick
 * positions and the break-even threshold line, neither a native gauge-series
 * feature, are drawn as resize-aware `graphic` overlays instead. */
'use strict';

function niceScale(min, max, ticks) {
  if (!isFinite(min) || !isFinite(max)) { min = 0; max = 1; }
  if (min === max) max = min + (Math.abs(min) || 1);
  const raw = (max - min) / (ticks || 5);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const values = [];
  for (let v = lo; v <= hi + step / 2; v += step) values.push(+v.toFixed(10));
  return {min: lo, max: hi, ticks: values};
}

function compactNum(v) {
  const a = Math.abs(v);
  if (a >= 1e6) return (v / 1e6).toFixed(1).replace('.', ',') + 'M';
  if (a >= 1e3) return Math.round(v / 1e3) + 'k';
  return String(Math.round(v * 100) / 100).replace('.', ',');
}

function chartEmpty(message) {
  return `<div class="chart-empty">${esc(message || 'Sem dados no período.')}</div>`;
}

/* Scale bubble radii by sqrt(value) between rmin and rmax. */
function bubbleRadius(values, rmin, rmax) {
  const max = Math.max(...values.map((v) => Math.max(0, v || 0)), 1);
  return (v) => (rmin || 3) + ((rmax || 16) - (rmin || 3)) * Math.sqrt(Math.max(0, v || 0) / max);
}

const HEAT_STOPS = [[255, 255, 204], [254, 217, 118], [253, 141, 60], [227, 26, 28], [128, 0, 38]];
function heatColor(t) {
  t = Math.max(0, Math.min(1, t)) * (HEAT_STOPS.length - 1);
  const i = Math.min(HEAT_STOPS.length - 2, Math.floor(t)), f = t - i;
  const c = HEAT_STOPS[i].map((a, k) => Math.round(a + (HEAT_STOPS[i + 1][k] - a) * f));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

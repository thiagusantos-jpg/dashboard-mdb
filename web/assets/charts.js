/* Mercado duBairro — chart helpers still used by the app.
 * Most charts moved to ECharts (echarts-charts.js) across Fase 2/3 of the
 * 2026-09-11 UX audit; what's left here is (1) math/format helpers those
 * ECharts adapters and insights.js's business logic still call directly
 * (niceScale, compactNum, bubbleRadius, heatColor) and (2) the one SVG chart
 * not yet migrated, gaugeChart() (Visão Futurista's break-even speedometer —
 * its arbitrary, non-evenly-spaced tick positions have no clean ECharts gauge
 * equivalent without hand-placed graphic overlays that would not reposition
 * on resize; a deliberate Fase 3 scoping decision, not an oversight). */
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

function svgOpen(W, H) {
  return `<svg viewBox="0 0 ${W} ${H}" class="chart-svg-i" role="img" xmlns="http://www.w3.org/2000/svg">`;
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

/* Semicircle gauge. steps: [{to, color}] in value units; threshold: value. */
function gaugeChart(o) {
  const W = o.width || 560, H = 330;
  // Leave room above the arc for the title and the top tick label.
  const cx = W / 2, cy = H - 70, R = Math.min(W / 2 - 60, 170), thick = 34;
  const max = o.max > 0 ? o.max : 1;
  const ang = (v) => Math.PI * (1 - Math.max(0, Math.min(max, v)) / max);
  const pt = (a, r) => [cx + r * Math.cos(a), cy - r * Math.sin(a)];
  const arc = (v0, v1, r) => {
    const [x0, y0] = pt(ang(v0), r), [x1, y1] = pt(ang(v1), r);
    return `M${x0.toFixed(1)},${y0.toFixed(1)} A${r},${r} 0 0 1 ${x1.toFixed(1)},${y1.toFixed(1)}`;
  };
  let g = '';
  let from = 0;
  (o.steps || []).forEach((s) => {
    g += `<path d="${arc(from, s.to, R)}" fill="none" stroke="${s.color}" stroke-width="${thick}"/>`;
    from = s.to;
  });
  g += `<path d="${arc(0, o.value, R)}" fill="none" stroke="${o.color || '#FFC107'}" stroke-width="${thick * 0.42}" class="chart-mark" data-tip="${esc(o.tip || '')}"/>`;
  if (o.threshold != null) {
    const [x0, y0] = pt(ang(o.threshold), R - thick * 0.62), [x1, y1] = pt(ang(o.threshold), R + thick * 0.62);
    g += `<line x1="${x0.toFixed(1)}" y1="${y0.toFixed(1)}" x2="${x1.toFixed(1)}" y2="${y1.toFixed(1)}" stroke="#E74C3C" stroke-width="4" class="chart-mark" data-tip="${esc(o.thresholdTip || '')}"/>`;
  }
  (o.ticks || []).forEach((t) => {
    const [x, y] = pt(ang(t), R + thick / 2 + 12);
    const a = ang(t);
    g += `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" class="chart-tick" text-anchor="${a > Math.PI * 0.6 ? 'end' : a < Math.PI * 0.4 ? 'start' : 'middle'}">${esc(o.fmt(t))}</text>`;
  });
  if (o.title) g += `<text x="${cx}" y="18" class="chart-gauge-title" text-anchor="middle">${esc(o.title)}</text>`;
  g += `<text x="${cx}" y="${cy - 6}" class="chart-gauge-value" text-anchor="middle">${esc(o.fmt(o.value))}</text>`;
  // Text on white: the darker positive/negative tokens (4.7:1 / 5.4:1), not the chart greens/reds.
  if (o.delta) g += `<text x="${cx}" y="${cy + 22}" class="chart-gauge-delta" text-anchor="middle" fill="${o.delta.value >= 0 ? '#1E8449' : '#C0392B'}">${esc(o.delta.text)}</text>`;
  return `<div class="chart-wrap chart-gauge">${svgOpen(W, H)}${g}</svg></div>`;
}

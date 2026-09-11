/* Mercado duBairro — interactive SVG charts (self-hosted, CSP-safe).
 * Every chart is returned as an HTML string. Interactivity (hover tooltips,
 * clickable legends) is wired once in app.js through delegated listeners on
 * data-tip / data-legend attributes — no inline handlers, no style=""
 * attributes; colours travel as SVG presentation attributes (fill/stroke). */
'use strict';

let chartSeq = 0;

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

function legendHtml(id, items) {
  return `<div class="chart-legend">${items.map((s, i) => `<button type="button" class="legend-item" data-legend="${id}" data-s="${i}" title="Clique para mostrar/ocultar">
      <svg width="12" height="12" aria-hidden="true"><rect x="0.5" y="0.5" width="11" height="11" rx="2" fill="${s.color}" fill-opacity="${s.opacity == null ? 1 : s.opacity}" stroke="${s.stroke || s.color}"/></svg>${esc(s.name)}</button>`).join('')}</div>`;
}

function svgOpen(W, H) {
  return `<svg viewBox="0 0 ${W} ${H}" class="chart-svg-i" role="img" xmlns="http://www.w3.org/2000/svg">`;
}

function diamond(x, y, r) {
  return `M${x},${y - r} L${x + r},${y} L${x},${y + r} L${x - r},${y} Z`;
}

/* Categorical x-axis chart mixing bar and line series.
 * series: {name, type:'bar'|'line', values:[number|null], color, colors?:[per-point],
 *          opacity?, stroke?, dash?, fill?, marker?:'circle'|'diamond', axis?:'left'|'right',
 *          labels?:bool, labelPos?:'top'|'bottom', fmt?:fn, tips?:[per-point extra text]} */
function comboChart(o) {
  const id = 'c' + (++chartSeq);
  const labels = o.labels || [];
  const series = (o.series || []).filter(Boolean);
  const n = labels.length;
  const anyValue = series.some((s) => s.values.some((v) => v != null && isFinite(v)));
  if (!n || !anyValue) return chartEmpty(o.empty);
  const W = o.width || 760, H = o.height || 340;
  const hasY2 = series.some((s) => s.axis === 'right');
  const m = {l: 66, r: hasY2 ? 66 : 18, t: 24, b: o.rotate ? 104 : 38};
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const fmtFor = (s) => s.fmt || (s.axis === 'right' ? o.y2Fmt : o.yFmt) || compactNum;

  const scaleFor = (axis) => {
    const vals = [];
    series.filter((s) => (s.axis || 'left') === axis).forEach((s) => s.values.forEach((v) => {
      if (v != null && isFinite(v)) vals.push(v);
    }));
    (o.hlines || []).filter((h) => (h.axis || 'left') === axis).forEach((h) => vals.push(h.value));
    if (!vals.length) return null;
    let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
    if (axis === 'left' && o.yMin != null) lo = o.yMin;
    hi += (hi - lo) * (o.headroom == null ? 0.12 : o.headroom);
    return niceScale(lo, hi, 5);
  };
  const s1 = scaleFor('left') || niceScale(0, 1, 5);
  const s2 = hasY2 ? scaleFor('right') : null;
  const yOf = (sc) => (v) => m.t + ih - ((v - sc.min) / (sc.max - sc.min)) * ih;
  const Y1 = yOf(s1), Y2 = s2 ? yOf(s2) : null;
  const band = iw / n;
  const cx = (i) => m.l + band * (i + 0.5);

  let g = '';
  // grid + y axes
  s1.ticks.forEach((t) => {
    const y = Y1(t).toFixed(1);
    g += `<line x1="${m.l}" y1="${y}" x2="${W - m.r}" y2="${y}" class="chart-grid"/>`;
    g += `<text x="${m.l - 8}" y="${y}" class="chart-tick" text-anchor="end" dominant-baseline="middle">${esc((o.yFmt || compactNum)(t))}</text>`;
  });
  if (s2) s2.ticks.forEach((t) => {
    g += `<text x="${W - m.r + 8}" y="${Y2(t).toFixed(1)}" class="chart-tick" dominant-baseline="middle">${esc((o.y2Fmt || compactNum)(t))}</text>`;
  });
  if (s1.min < 0) g += `<line x1="${m.l}" y1="${Y1(0).toFixed(1)}" x2="${W - m.r}" y2="${Y1(0).toFixed(1)}" class="chart-zero"/>`;
  if (o.yTitle) g += `<text transform="translate(14 ${m.t + ih / 2}) rotate(-90)" class="chart-axis-title" text-anchor="middle">${esc(o.yTitle)}</text>`;
  if (o.y2Title) g += `<text transform="translate(${W - 12} ${m.t + ih / 2}) rotate(90)" class="chart-axis-title" text-anchor="middle">${esc(o.y2Title)}</text>`;

  // x labels (thinned when dense)
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(iw / (o.rotate ? 22 : 44)))));
  labels.forEach((l, i) => {
    if (i % every) return;
    const x = cx(i).toFixed(1), y = m.t + ih + 16;
    g += o.rotate
      ? `<text transform="translate(${x} ${y - 6}) rotate(-40)" class="chart-tick" text-anchor="end">${esc(l)}</text>`
      : `<text x="${x}" y="${y}" class="chart-tick" text-anchor="middle">${esc(l)}</text>`;
  });

  // hover bands: whole-column tooltip listing every series (below the marks)
  labels.forEach((l, i) => {
    const lines = series.map((s) => s.values[i] == null ? null : `${s.name}: ${fmtFor(s)(s.values[i])}`).filter(Boolean);
    if (!lines.length) return;
    const head = o.tipLabels ? o.tipLabels[i] : l;
    g += `<rect x="${(m.l + band * i).toFixed(1)}" y="${m.t}" width="${band.toFixed(1)}" height="${ih}" class="chart-hover" data-tip="${esc(head + '\n' + lines.join('\n'))}"/>`;
  });

  // bars
  const bars = series.filter((s) => s.type === 'bar');
  const groupW = band * (o.barMode === 'overlay' ? 0.62 : 0.8);
  const slotW = o.barMode === 'overlay' ? groupW : groupW / Math.max(1, bars.length);
  series.forEach((s, si) => {
    const Y = s.axis === 'right' ? Y2 : Y1;
    const fmt = fmtFor(s);
    let body = '';
    if (s.type === 'bar') {
      const bi = bars.indexOf(s);
      const base = Y(Math.max(0, (s.axis === 'right' ? s2 : s1).min));
      s.values.forEach((v, i) => {
        if (v == null || !isFinite(v)) return;
        const x = cx(i) - groupW / 2 + (o.barMode === 'overlay' ? 0 : bi * slotW) + slotW * 0.06;
        const w = slotW * 0.88;
        const yv = Y(v);
        const top = Math.min(yv, base), h = Math.max(1, Math.abs(base - yv));
        const fill = s.colors ? s.colors[i] : s.color;
        const stroke = s.strokes ? s.strokes[i] : s.stroke;  // per-point, like colors
        const tip = `${o.tipLabels ? o.tipLabels[i] : labels[i]}\n${s.name}: ${fmt(v)}${s.tips && s.tips[i] ? '\n' + s.tips[i] : ''}`;
        body += `<rect x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="${fill}" fill-opacity="${s.opacity == null ? 1 : s.opacity}"${stroke ? ` stroke="${stroke}" stroke-width="1.5"` : ''} class="chart-mark" data-tip="${esc(tip)}"/>`;
        if (s.labels) {
          const ly = v >= 0 ? top - 5 : top + h + 11;
          body += `<text x="${(x + w / 2).toFixed(1)}" y="${ly.toFixed(1)}" class="chart-value" text-anchor="middle">${esc((s.labelFmt || fmt)(v))}</text>`;
        }
      });
    } else {
      let d = '', area = '', started = false, first = null, last = null;
      s.values.forEach((v, i) => {
        if (v == null || !isFinite(v)) { started = false; return; }
        const x = cx(i).toFixed(1), y = Y(v).toFixed(1);
        d += `${started ? 'L' : 'M'}${x},${y} `;
        if (first == null) first = x;
        last = x;
        started = true;
      });
      if (s.fill && first != null) {
        const pts = s.values.map((v, i) => v == null ? null : `${cx(i).toFixed(1)},${Y(v).toFixed(1)}`).filter(Boolean);
        const zero = Y(Math.max(0, (s.axis === 'right' ? s2 : s1).min)).toFixed(1);
        area = `<path d="M${first},${zero} L${pts.join(' L')} L${last},${zero} Z" fill="${s.color}" fill-opacity="0.12"/>`;
      }
      body += area;
      body += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2.5}"${s.dash ? ` stroke-dasharray="${s.dash}"` : ''} stroke-linejoin="round"/>`;
      s.values.forEach((v, i) => {
        if (v == null || !isFinite(v)) return;
        const x = cx(i), y = Y(v);
        const tip = `${o.tipLabels ? o.tipLabels[i] : labels[i]}\n${s.name}: ${fmt(v)}${s.tips && s.tips[i] ? '\n' + s.tips[i] : ''}`;
        const r = s.markerSize || (s.marker === 'diamond' ? 6 : 4);
        body += s.marker === 'none' ? '' : s.marker === 'diamond'
          ? `<path d="${diamond(x, y, r)}" fill="${s.color}" stroke="#fff" stroke-width="1" class="chart-mark" data-tip="${esc(tip)}"/>`
          : `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r}" fill="${s.color}" stroke="#fff" stroke-width="1" class="chart-mark" data-tip="${esc(tip)}"/>`;
        if (s.labels) {
          const ly = s.labelPos === 'bottom' ? y + r + 12 : y - r - 5;
          g += `<text x="${x.toFixed(1)}" y="${ly.toFixed(1)}" class="chart-value" text-anchor="middle" data-series="${id}-${si}">${esc((s.labelFmt || fmt)(v))}</text>`;
        }
      });
    }
    g += `<g data-series="${id}-${si}">${body}</g>`;
  });

  // reference lines
  (o.hlines || []).forEach((h) => {
    const Y = h.axis === 'right' ? Y2 : Y1;
    const y = Y(h.value).toFixed(1);
    g += `<line x1="${m.l}" y1="${y}" x2="${W - m.r}" y2="${y}" stroke="${h.color || '#999'}" stroke-width="${h.width || 1.5}" stroke-dasharray="${h.dash || '5 4'}"/>`;
    if (h.label) g += `<text x="${W - m.r - 4}" y="${(+y - 5).toFixed(1)}" class="chart-annot" text-anchor="end" fill="${h.labelColor || '#888'}">${esc(h.label)}</text>`;
  });

  const legend = o.legend === false || series.length < 2 ? '' : legendHtml(id, series.map((s) => ({
    name: s.name, color: s.color || (s.colors && s.colors[0]) || '#999', opacity: s.opacity, stroke: s.stroke})));
  return `<div class="chart-wrap">${svgOpen(W, H)}${g}</svg>${legend}</div>`;
}

/* Bubble/scatter chart. points: {x, y, r, g, tip}; groups: [{name, color}] */
function scatterChart(o) {
  const id = 'c' + (++chartSeq);
  const pts = (o.points || []).filter((p) => isFinite(p.x) && isFinite(p.y));
  if (!pts.length) return chartEmpty(o.empty);
  const W = o.width || 760, H = o.height || 440;
  const m = {l: 66, r: 20, t: 20, b: 48};
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  (o.vlines || []).forEach((v) => xs.push(v.x));
  (o.hlines || []).forEach((h) => ys.push(h.y));
  const sx = o.xScale || niceScale(Math.min(...xs), Math.max(...xs) * 1.05, 6);
  const sy = o.yScale || niceScale(Math.min(...ys), Math.max(...ys), 6);
  const X = (v) => m.l + ((v - sx.min) / (sx.max - sx.min)) * iw;
  const Y = (v) => m.t + ih - ((v - sy.min) / (sy.max - sy.min)) * ih;
  let g = '';
  sy.ticks.forEach((t) => {
    g += `<line x1="${m.l}" y1="${Y(t).toFixed(1)}" x2="${W - m.r}" y2="${Y(t).toFixed(1)}" class="chart-grid"/>`;
    g += `<text x="${m.l - 8}" y="${Y(t).toFixed(1)}" class="chart-tick" text-anchor="end" dominant-baseline="middle">${esc((o.yFmt || compactNum)(t))}</text>`;
  });
  sx.ticks.forEach((t) => {
    g += `<line x1="${X(t).toFixed(1)}" y1="${m.t}" x2="${X(t).toFixed(1)}" y2="${m.t + ih}" class="chart-grid"/>`;
    g += `<text x="${X(t).toFixed(1)}" y="${m.t + ih + 16}" class="chart-tick" text-anchor="middle">${esc((o.xFmt || compactNum)(t))}</text>`;
  });
  if (o.xTitle) g += `<text x="${m.l + iw / 2}" y="${H - 6}" class="chart-axis-title" text-anchor="middle">${esc(o.xTitle)}</text>`;
  if (o.yTitle) g += `<text transform="translate(14 ${m.t + ih / 2}) rotate(-90)" class="chart-axis-title" text-anchor="middle">${esc(o.yTitle)}</text>`;
  (o.vlines || []).forEach((v) => {
    g += `<line x1="${X(v.x).toFixed(1)}" y1="${m.t}" x2="${X(v.x).toFixed(1)}" y2="${m.t + ih}" stroke="#999" stroke-dasharray="5 4"/>`;
    if (v.label) g += `<text x="${(X(v.x) + 4).toFixed(1)}" y="${m.t + 12}" class="chart-annot" fill="#888">${esc(v.label)}</text>`;
  });
  (o.hlines || []).forEach((h) => {
    g += `<line x1="${m.l}" y1="${Y(h.y).toFixed(1)}" x2="${W - m.r}" y2="${Y(h.y).toFixed(1)}" stroke="#999" stroke-dasharray="5 4"/>`;
    if (h.label) g += `<text x="${W - m.r - 4}" y="${(Y(h.y) - 5).toFixed(1)}" class="chart-annot" text-anchor="end" fill="#888">${esc(h.label)}</text>`;
  });
  (o.annotations || []).forEach((a) => {
    g += `<text x="${X(a.x).toFixed(1)}" y="${Y(a.y).toFixed(1)}" class="chart-quadrant" text-anchor="middle" fill="${a.color || '#888'}">${esc(a.text)}</text>`;
  });
  (o.groups || []).forEach((grp, gi) => {
    const mine = pts.filter((p) => p.g === gi).sort((a, b) => b.r - a.r);
    g += `<g data-series="${id}-${gi}">${mine.map((p) =>
      `<circle cx="${X(p.x).toFixed(1)}" cy="${Y(p.y).toFixed(1)}" r="${p.r.toFixed(1)}" fill="${grp.color}" fill-opacity="0.62" stroke="#fff" stroke-width="0.8" class="chart-mark" data-tip="${esc(p.tip)}"/>`
    ).join('')}</g>`;
  });
  const legend = (o.groups || []).length > 1 ? legendHtml(id, o.groups.map((grp, gi) => ({
    name: `${grp.name} (${pts.filter((p) => p.g === gi).length})`, color: grp.color, opacity: 0.62}))) : '';
  return `<div class="chart-wrap">${svgOpen(W, H)}${g}</svg>${legend}</div>`;
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

/* Heatmap. cells: {r, c, value, text, tip} */
function heatmapChart(o) {
  const cells = o.cells || [];
  if (!cells.length) return chartEmpty(o.empty);
  const W = o.width || 520, rows = o.rows, cols = o.cols;
  const m = {l: 62, r: 10, t: 10, b: 30};
  const cw = (W - m.l - m.r) / cols.length, chh = Math.min(62, Math.max(38, cw * 0.62));
  const H = m.t + m.b + chh * rows.length;
  const vals = cells.map((c) => c.value);
  const lo = Math.min(...vals), hi = Math.max(...vals), span = (hi - lo) || 1;
  let g = '';
  cols.forEach((c, i) => {
    g += `<text x="${(m.l + cw * (i + 0.5)).toFixed(1)}" y="${H - 10}" class="chart-tick" text-anchor="middle">${esc(c)}</text>`;
  });
  rows.forEach((r, i) => {
    g += `<text x="${m.l - 8}" y="${(m.t + chh * (i + 0.5)).toFixed(1)}" class="chart-tick" text-anchor="end" dominant-baseline="middle">${esc(r)}</text>`;
  });
  rows.forEach((r, ri) => cols.forEach((c, ci) => {
    const x = m.l + cw * ci, y = m.t + chh * ri;
    const cell = cells.find((k) => k.r === ri && k.c === ci);
    if (!cell) {
      g += `<rect x="${(x + 1).toFixed(1)}" y="${(y + 1).toFixed(1)}" width="${(cw - 2).toFixed(1)}" height="${(chh - 2).toFixed(1)}" rx="3" fill="#F3F3F3"/>`;
      return;
    }
    const t = (cell.value - lo) / span;
    g += `<rect x="${(x + 1).toFixed(1)}" y="${(y + 1).toFixed(1)}" width="${(cw - 2).toFixed(1)}" height="${(chh - 2).toFixed(1)}" rx="3" fill="${heatColor(t)}" class="chart-mark" data-tip="${esc(cell.tip)}"/>`;
    g += `<text x="${(x + cw / 2).toFixed(1)}" y="${(y + chh / 2).toFixed(1)}" class="chart-cell" text-anchor="middle" dominant-baseline="middle" fill="${t > 0.55 ? '#fff' : '#2D2D2D'}">${esc(cell.text)}</text>`;
  }));
  return `<div class="chart-wrap">${svgOpen(W, H)}${g}</svg></div>`;
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

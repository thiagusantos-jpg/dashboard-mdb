# Apache ECharts (vendored)

- Version: 6.1.0
- Build: `dist/echarts.min.js` (the full/default build) from the official npm
  package — every chart type and component, Canvas renderer only, no SVG (a
  canvas has no per-point DOM to style, which sidesteps this app's strict
  style-src entirely — see web/assets/echarts-charts.js for why the built-in
  tooltip is disabled everywhere instead).
  - Do NOT swap this for `echarts.common.min.js` or `echarts.simple.min.js` to
    save size: both were tried during Fase 3 (2026-09-11) and `common` silently
    drops the `heatmap` series (`chart.getOption().series` comes back empty,
    no console warning/error at all — the class ships in the bundle's source
    but the build's own entry point never calls `echarts.use()` on it) and
    `simple` additionally lacks markLine/markPoint (used for every reference
    line — averages, break-even, the "1.00 = mês médio" line). Confirm a
    smaller build registers everything this app calls (heatmap, gauge,
    markLine, markPoint, legend, scatter, bar, line) before ever switching.
- License: Apache-2.0 (LICENSE + NOTICE in this folder).
- Served from `assets/vendor/` only — the app's CSP is `script-src 'self'`, so this
  must never be loaded from a CDN.
- To upgrade: `npm view echarts version`, then re-copy `dist/echarts.min.js`,
  `LICENSE` and `NOTICE` from a fresh `npm install echarts@<version>` and update
  this file and the `?v=` query string on the `<script>` tag in web/index.html.

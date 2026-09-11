# Apache ECharts (vendored)

- Version: 6.1.0
- Build: `dist/echarts.simple.min.js` from the official npm package (line, bar, pie,
  grid, legend, title, dataZoom, markLine/markPoint; Canvas renderer only — no SVG,
  no tooltip DOM injection is used, see web/assets/echarts-charts.js).
- License: Apache-2.0 (LICENSE in this folder).
- Served from `assets/vendor/` only — the app's CSP is `script-src 'self'`, so this
  must never be loaded from a CDN.
- To upgrade: `npm view echarts version`, then re-copy `dist/echarts.simple.min.js`
  and `LICENSE` from a fresh `npm install echarts@<version>` and update this file.

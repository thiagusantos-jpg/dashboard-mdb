# Lucide icons (vendored)

Source: lucide-static v1.45.0 (https://lucide.dev), ISC license — see LICENSE.

Only the icon path data actually used by the dashboard was copied, inline,
into `web/assets/icons.js` (the `ICONS` map). There is no build step and no
CDN load here, matching the app's CSP (`script-src 'self'`) and the way
Apache ECharts is vendored under `web/assets/vendor/echarts/`.

To add another icon: find its SVG at
https://github.com/lucide-icons/lucide/tree/main/icons, copy the inner
`<path>`/`<circle>`/... markup (not the outer `<svg>` tag) into `ICONS` in
`web/assets/icons.js`, keyed by the icon's kebab-case name.

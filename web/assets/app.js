/* Mercado duBairro — dashboard frontend.
 * Talks only to the authenticated /api/* backend (backend/api.py). No Excel
 * import, no localStorage as source of truth, no external script/style
 * loads — the backend's CSP is script-src 'self'; style-src 'self', so
 * every interaction here uses addEventListener + CSS classes, never
 * onclick="" attributes or style="" strings. Charts are hand-rolled SVG
 * to avoid a third-party CDN entirely. */
'use strict';

const APP = {
  csrf: null,
  companies: [],
  company: null,
  periods: [],       // [{period, updated_at, version}], newest first
  period: null,
  page: 'resumo',
  status: null,       // last /status payload
  dashboard: null,    // last /dashboard payload
  pollTimer: null,
};

/* ---------------------------------------------------------------- fetch */

async function api(path, opts) {
  opts = opts || {};
  const headers = Object.assign({}, opts.headers || {});
  if (opts.body) headers['Content-Type'] = 'application/json';
  if (opts.method && opts.method !== 'GET') headers['x-csrf-token'] = APP.csrf || '';
  const res = await fetch(path, Object.assign({credentials: 'same-origin'}, opts, {headers}));
  if (res.status === 401) {
    showLogin('Sua sessão expirou. Entre novamente.');
    throw new Error('unauthenticated');
  }
  if (!res.ok) {
    let detail = 'Erro ' + res.status;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

/* ---------------------------------------------------------------- format */

const money = (cents) => cents == null ? 'Indisponível' :
  new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'}).format(cents / 100);
const pct = (v) => v == null ? '—' : v.toFixed(2).replace('.', ',') + '%';
const num = (v) => v == null ? '—' : new Intl.NumberFormat('pt-BR').format(v);
const dt = (iso) => iso ? new Date(iso).toLocaleString('pt-BR') : '—';
const MONTHS = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'})[c]);

/* ---------------------------------------------------------------- charts (self-hosted SVG, no CDN) */

function svgLineChart(points) {
  if (!points.length) return '<div class="chart-empty">Sem dados no período.</div>';
  const w = 700, h = 260, pad = 30;
  const values = points.map((p) => p.value);
  const max = Math.max(...values, 0), min = Math.min(...values, 0);
  const range = (max - min) || 1;
  const stepX = points.length > 1 ? (w - pad * 2) / (points.length - 1) : 0;
  const x = (i) => pad + i * stepX;
  const y = (v) => h - pad - ((v - min) / range) * (h - pad * 2);
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  const dots = points.map((p, i) =>
    `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="3" class="chart-dot"><title>${esc(p.label)}: ${esc(p.display)}</title></circle>`
  ).join('');
  return `<svg viewBox="0 0 ${w} ${h}" class="chart-svg" preserveAspectRatio="none">
    <line x1="${pad}" y1="${(h - pad).toFixed(1)}" x2="${w - pad}" y2="${(h - pad).toFixed(1)}" class="chart-axis"/>
    <path d="${path}" class="chart-line"/>${dots}
  </svg>`;
}

function svgBarChart(points) {
  if (!points.length) return '<div class="chart-empty">Sem dados.</div>';
  const w = 700, h = 260, pad = 30;
  const max = Math.max(...points.map((p) => p.value), 1);
  const bw = (w - pad * 2) / points.length;
  const bars = points.map((p, i) => {
    const bh = (p.value / max) * (h - pad * 2 - 14);
    const bx = pad + i * bw + bw * 0.15;
    const by = h - pad - bh;
    return `<rect x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${(bw * 0.7).toFixed(1)}" height="${bh.toFixed(1)}" class="chart-bar"><title>${esc(p.label)}: ${esc(p.display)}</title></rect>`;
  }).join('');
  const labels = points.map((p, i) =>
    `<text x="${(pad + i * bw + bw / 2).toFixed(1)}" y="${(h - pad + 14).toFixed(1)}" class="chart-label" text-anchor="middle">${esc(p.label)}</text>`
  ).join('');
  return `<svg viewBox="0 0 ${w} ${h}" class="chart-svg" preserveAspectRatio="none">
    <line x1="${pad}" y1="${(h - pad).toFixed(1)}" x2="${w - pad}" y2="${(h - pad).toFixed(1)}" class="chart-axis"/>
    ${bars}${labels}
  </svg>`;
}

/* ---------------------------------------------------------------- boot */

function boot() {
  document.getElementById('login-form').addEventListener('submit', onLoginSubmit);
  document.getElementById('custo-fixo-input').addEventListener('change', updateCustoFixo);
  document.querySelectorAll('img[data-fallback]').forEach((img) => {
    img.addEventListener('error', () => img.classList.add('hidden'));
  });
  document.addEventListener('click', onDelegatedClick);
  setupChartTooltip();
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    // Insight charts are drawn at their pixel width; redraw from the cached payload.
    resizeTimer = setTimeout(() => { if (INSIGHT_PAGES.includes(APP.page) && APP.dashboard) renderPage(); }, 250);
  });
  refreshSession();
}

const INSIGHT_PAGES = ['precos', 'mapa', 'diagnostico', 'sazonalidade', 'visao'];

function setupChartTooltip() {
  const tip = document.createElement('div');
  tip.className = 'chart-tip hidden';
  document.body.appendChild(tip);
  document.addEventListener('mouseover', (ev) => {
    const el = ev.target.closest && ev.target.closest('[data-tip]');
    if (!el || !el.dataset.tip) return tip.classList.add('hidden');
    tip.textContent = el.dataset.tip;
    tip.classList.remove('hidden');
  });
  document.addEventListener('mousemove', (ev) => {
    if (tip.classList.contains('hidden')) return;
    const x = ev.clientX + 14, y = ev.clientY + 14;
    tip.style.left = Math.max(8, Math.min(x, window.innerWidth - tip.offsetWidth - 8)) + 'px';
    tip.style.top = (y + tip.offsetHeight > window.innerHeight - 8 ? ev.clientY - tip.offsetHeight - 10 : y) + 'px';
  });
}

function onDelegatedClick(ev) {
  const nav = ev.target.closest('[data-nav]');
  if (nav) return navigate(nav.dataset.nav);
  const explainBtn = ev.target.closest('[data-explain]');
  if (explainBtn) return explainBtn.nextElementSibling.classList.toggle('open');
  const tab = ev.target.closest('[data-tab]');
  if (tab) {
    const bar = tab.closest('.tabs');
    bar.querySelectorAll('[data-tab]').forEach((b) => b.classList.toggle('active', b === tab));
    for (let s = bar.nextElementSibling; s && s.classList.contains('tab-content'); s = s.nextElementSibling) {
      s.classList.toggle('active', s.id === tab.dataset.tab);
    }
    return;
  }
  const legend = ev.target.closest('[data-legend]');
  if (legend) {
    const off = legend.classList.toggle('off');
    document.querySelectorAll(`[data-series="${legend.dataset.legend}-${legend.dataset.s}"]`)
      .forEach((el) => el.classList.toggle('series-off', off));
    return;
  }
  const year = ev.target.closest('[data-year]');
  if (year) return selectYear(year.dataset.year);
  const period = ev.target.closest('[data-period]');
  if (period) return selectPeriod(period.dataset.period);
  const sync = ev.target.closest('[data-sync]');
  if (sync) return triggerSync(sync.dataset.sync);
  if (ev.target.closest('[data-logout]')) return logout();
}

async function refreshSession() {
  try {
    const session = await api('/api/session');
    onAuthenticated(session);
  } catch (e) {
    showLogin();
  }
}

function showLogin(message) {
  document.getElementById('loading').classList.add('hidden');
  document.getElementById('app').classList.add('hidden');
  document.getElementById('login-screen').classList.remove('hidden');
  document.getElementById('login-error').textContent = message || '';
  document.getElementById('login-password').focus();
}

async function onLoginSubmit(ev) {
  ev.preventDefault();
  const pass = document.getElementById('login-password').value;
  const btn = document.getElementById('login-submit');
  btn.disabled = true;
  document.getElementById('login-error').textContent = '';
  try {
    const res = await fetch('/api/login', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password: pass}),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Não foi possível entrar.');
    }
    const session = await api('/api/session');
    document.getElementById('login-screen').classList.add('hidden');
    onAuthenticated(session);
  } catch (e) {
    document.getElementById('login-error').textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function logout() {
  try { await api('/api/logout', {method: 'POST'}); } catch (e) {}
  location.reload();
}

async function onAuthenticated(session) {
  APP.csrf = session.csrf;
  APP.companies = session.companies || [];
  document.getElementById('sidebar-user').textContent = session.user || '';
  document.getElementById('loading').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  if (!APP.companies.length) {
    renderEmptyState('Nenhuma empresa liberada para esta credencial Mobne.');
    return;
  }
  APP.company = APP.companies[0].id;
  await refreshStatus();
  navigate('resumo');
}

/* ---------------------------------------------------------------- status/period */

async function refreshStatus() {
  APP.status = await api(`/api/companies/${APP.company}/status`);
  APP.periods = APP.status.periods || [];
  buildPeriodSelector();
  const hasActiveJob = (APP.status.jobs || []).some((j) => j.state === 'queued' || j.state === 'running');
  if (hasActiveJob && !APP.pollTimer) {
    APP.pollTimer = setInterval(async () => {
      await refreshStatus();
      if (APP.page === 'sync') renderSyncPage();
    }, 4000);
  } else if (!hasActiveJob && APP.pollTimer) {
    clearInterval(APP.pollTimer);
    APP.pollTimer = null;
    APP.dashboard = null;  // a finished sync may have refreshed catalogs/sales
  }
}

function buildPeriodSelector() {
  const years = [...new Set(APP.periods.map((p) => p.period.slice(0, 4)))].sort().reverse();
  const yearTabs = document.getElementById('year-tabs');
  const monthGrid = document.getElementById('month-buttons');
  const periodStatus = document.getElementById('period-status');

  if (!years.length) {
    yearTabs.innerHTML = '';
    monthGrid.innerHTML = '';
    periodStatus.textContent = 'Nenhum período sincronizado ainda.';
    return;
  }
  if (!APP.period || !APP.periods.some((p) => p.period === APP.period)) {
    APP.period = APP.periods[0].period;
  }
  const activeYear = APP.period.slice(0, 4);

  yearTabs.innerHTML = years.map((y) =>
    `<button class="tab-btn-period ${y === activeYear ? 'active' : ''}" data-year="${y}">${y}</button>`
  ).join('');

  const known = new Map(APP.periods.map((p) => [p.period, p]));
  monthGrid.innerHTML = MONTHS.map((label, i) => {
    const period = `${activeYear}-${String(i + 1).padStart(2, '0')}`;
    const info = known.get(period);
    const active = period === APP.period ? 'active' : '';
    const disabled = info ? '' : 'disabled';
    return `<button class="month-btn ${active}" ${disabled} data-period="${period}">${label}</button>`;
  }).join('');

  const current = known.get(APP.period);
  periodStatus.textContent = current ? `Atualizado ${dt(current.updated_at)} · v${current.version}` : '';
}

function selectYear(year) {
  const inYear = APP.periods.find((p) => p.period.startsWith(year));
  APP.period = inYear ? inYear.period : `${year}-01`;
  buildPeriodSelector();
  if (APP.page !== 'sync') renderPage();
}

function selectPeriod(period) {
  APP.period = period;
  buildPeriodSelector();
  if (APP.page !== 'sync') renderPage();
}

/* ---------------------------------------------------------------- nav */

function navigate(page) {
  APP.page = page;
  document.querySelectorAll('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.page === page));
  renderPage();
  window.scrollTo(0, 0);
}

async function renderPage() {
  if (APP.page === 'sync') return renderSyncPage();
  if (!APP.period) {
    return renderEmptyState('Nenhum período sincronizado ainda. Vá em "Sincronização Mobne" e clique em Sincronizar agora.');
  }
  // Every data page reads the same /dashboard payload; reuse it until the period or its version changes.
  const info = APP.periods.find((p) => p.period === APP.period);
  const cached = APP.dashboard && APP.dashboard.period === APP.period && APP.dashboardCompany === APP.company &&
    (!info || info.version === APP.dashboard.version);
  if (!cached) {
    document.getElementById('content').innerHTML = '<div class="loading loading-inline"><div class="loading-spinner"></div>Carregando…</div>';
    try {
      APP.dashboard = await api(`/api/companies/${APP.company}/dashboard?period=${APP.period}`);
      APP.dashboardCompany = APP.company;
    } catch (e) {
      APP.dashboard = null;
      if (e.status === 404) return renderEmptyState(e.message);
      return renderEmptyState('Não foi possível carregar os dados: ' + e.message);
    }
    document.getElementById('custo-fixo-input').value = (APP.dashboard.fixed_cost_cents / 100).toFixed(2);
  }
  const renderers = {resumo: renderResumo, estoque: renderEstoque, precos: renderPrecos, mapa: renderMapa,
    diagnostico: renderDiagnostico, sazonalidade: renderSazonalidade, visao: renderVisao};
  if (APP.page === 'sync') return renderSyncPage();  // user navigated away mid-fetch
  (renderers[APP.page] || renderResumo)(APP.dashboard);
}

function renderEmptyState(message) {
  document.getElementById('content').innerHTML = `
    <div class="page-title">Mercado duBairro</div>
    <div class="story-box mt-16">${esc(message)}</div>`;
}

function headerBlock(data) {
  const [y, m] = data.period.split('-');
  const label = `${MONTHS[parseInt(m, 10) - 1]}/${y}`;
  const partial = data.partial_month ? ` · em andamento (dados até ${data.as_of})` : '';
  return `
    <div class="page-title">📊 Resumo Executivo</div>
    <div class="page-subtitle">Vendas PDV reconciliadas diretamente do Mobne — sem Excel, sem localStorage.</div>
    <span class="periodo-badge">${label}${partial}</span>
    <span class="periodo-badge muted">Atualizado ${dt(data.updated_at)} · v${data.version}</span>
    ${reconciliationBanner(data.reconciliation)}
  `;
}

function reconciliationBanner(r) {
  if (r.exact_match) {
    return `<div class="disclosure-banner">✅ Reconciliado exatamente com a Análise Mobne — ${money(r.receipt_revenue)} conferido, sem divergência.</div>`;
  }
  return `<div class="disclosure-banner warn">⚠️ Diferença de ${money(r.difference)} entre Cupom e Análise Mobne
    (${r.missing_documents} documento${r.missing_documents === 1 ? '' : 's'}: ${r.missing_document_ids.join(', ')}).
    Publicado por estar dentro da tolerância declarada de ${money(r.tolerance_cents)}. Cupom é a receita oficial.</div>`;
}

function renderResumo(data) {
  const t = data.totals;
  const cmp = data.comparison;
  const cmpHtml = cmp ? `<div class="kpi-card">
      <div class="kpi-title">Vs. ${cmp.period.slice(0, 4)} (mesmo período)</div>
      <div class="kpi-value ${cmp.revenue_change == null ? '' : (cmp.revenue_change >= 0 ? 'kpi-positive' : 'kpi-negative')}">
        ${cmp.revenue_change == null ? '—' : (cmp.revenue_change >= 0 ? '+' : '') + pct(cmp.revenue_change)}
      </div>
      <div class="kpi-subtitle">Receita: ${money(cmp.totals.revenue)}</div>
    </div>` : `<div class="kpi-card"><div class="kpi-title">Produtos vendidos</div><div class="kpi-value">${num(t.products)}</div></div>`;

  const dailyPoints = (data.daily || []).map((d) => ({label: d.date, value: d.revenue / 100, display: money(d.revenue)}));
  const timelinePoints = (data.timeline || []).map((tl) => ({label: tl.period, value: tl.revenue / 100, display: money(tl.revenue)}));

  document.getElementById('content').innerHTML = `
    ${headerBlock(data)}
    <div class="kpi-grid kpi-grid-4">
      <div class="kpi-card"><div class="kpi-title">Receita Líquida</div><div class="kpi-value">${money(t.revenue)}</div>
        <div class="kpi-subtitle">${num(t.receipts)} documentos · ${num(t.cancelled)} cancelados</div></div>
      <div class="kpi-card"><div class="kpi-title">Lucro Bruto</div>
        <div class="kpi-value ${t.profit == null ? 'kpi-unavailable' : (t.profit >= 0 ? 'kpi-positive' : 'kpi-negative')}">${money(t.profit)}</div>
        <div class="kpi-subtitle">${t.unknown > 0 ? num(t.unknown) + ' itens sem custo — não estimados' : 'Todos os itens com custo'}</div></div>
      <div class="kpi-card"><div class="kpi-title">Margem</div>
        <div class="kpi-value ${t.margin == null ? 'kpi-unavailable' : ''}">${pct(t.margin)}</div>
        <div class="kpi-subtitle">Ticket médio: ${money(t.ticket)}</div></div>
      ${cmpHtml}
    </div>

    <div class="row">
      <div class="col-60">
        <div class="section-header">Receita diária</div>
        <div class="chart-container chart-box">${svgLineChart(dailyPoints)}</div>
      </div>
      <div class="col-40">
        <div class="section-header">Categorias</div>
        <div class="data-table-container table-scroll-sm">
          <table class="data-table"><thead><tr><th>Categoria</th><th>Receita</th><th>Margem</th></tr></thead>
          <tbody>${(data.categories || []).map((c) => `<tr><td>${esc(c.name)}</td><td>${money(c.revenue)}</td><td>${pct(c.margin)}</td></tr>`).join('')}</tbody></table>
        </div>
      </div>
    </div>

    <div class="section-header">Produtos (curva ABC do período)</div>
    <div class="data-table-container">
      <table class="data-table"><thead><tr><th>Produto</th><th>Categoria</th><th>ABC</th><th>Classificação</th><th>Receita</th><th>Margem</th><th>Giro</th></tr></thead>
      <tbody>${(data.products || []).map((p) => `<tr><td>${esc(p.name)}</td><td>${esc(p.category)}</td><td>${p.abc}</td>
        <td>${esc(p.classification)}</td><td>${money(p.revenue)}</td><td>${pct(p.margin)}</td><td>${pct(p.turnover * 100)}</td></tr>`).join('')}</tbody></table>
    </div>

    <div class="section-header">Histórico mensal</div>
    <div class="chart-container chart-box">${svgBarChart(timelinePoints)}</div>

    <div class="section-header">Resultado líquido simulado</div>
    <div class="story-box">
      Simulação: receita − custo dos itens conhecidos − custo fixo cadastrado. Não é o lucro líquido contábil
      (despesas reais não confirmadas com o Mobne) e não deve ser tratado como resultado realizado.
    </div>
    <div class="kpi-grid kpi-grid-2">
      <div class="kpi-card"><div class="kpi-title">Custo fixo mensal cadastrado</div><div class="kpi-value">${money(data.fixed_cost_cents)}</div></div>
      <div class="kpi-card"><div class="kpi-title">Resultado simulado</div>
        <div class="kpi-value ${data.simulated_net == null ? 'kpi-unavailable' : (data.simulated_net >= 0 ? 'kpi-positive' : 'kpi-negative')}">${money(data.simulated_net)}</div></div>
    </div>
  `;
}

function renderEstoque(data) {
  const inv = (data.inventory || []).slice().sort((a, b) => b.revenue - a.revenue);
  document.getElementById('content').innerHTML = `
    <div class="page-title">📦 Produtos &amp; Estoque</div>
    <div class="page-subtitle">Estoque e preço são o retrato ATUAL do Mobne — não representam o histórico do período selecionado.</div>
    <span class="periodo-badge muted">Estoque: ${dt(data.stock_updated_at)}</span>
    <span class="periodo-badge muted">Preços: ${dt(data.prices_updated_at)}</span>
    <div class="data-table-container table-scroll-tall">
      <table class="data-table"><thead><tr>
        <th>Produto</th><th>Categoria</th><th>Estoque atual</th><th>Preço atual</th><th>Custo atual</th>
        <th>Receita no período</th><th>Margem</th><th>ABC</th>
      </tr></thead>
      <tbody>${inv.map((p) => `<tr><td>${esc(p.name)}</td><td>${esc(p.category)}</td>
        <td>${p.stock == null ? '—' : num(p.stock)}</td><td>${money(p.current_price)}</td><td>${money(p.current_cost)}</td>
        <td>${money(p.revenue)}</td><td>${pct(p.margin)}</td><td>${p.abc}</td></tr>`).join('')}</tbody></table>
    </div>
  `;
}

/* ---------------------------------------------------------------- sync page */

function jobBadge(state) {
  const map = {queued: 'badge-info', running: 'badge-info', completed: 'badge-success', failed: 'badge-error'};
  const label = {queued: 'Na fila', running: 'Em execução', completed: 'Concluído', failed: 'Falhou'};
  return `<span class="${map[state] || 'badge-muted'}">${label[state] || state}</span>`;
}

function renderSyncPage() {
  const s = APP.status;
  const catalogs = s.catalogs || {};
  const catalogRows = ['categories', 'products', 'stock', 'prices'].map((key) => {
    const c = catalogs[key];
    const labels = {categories: 'Categorias', products: 'Produtos', stock: 'Estoque', prices: 'Preços'};
    return `<tr><td>${labels[key]}</td><td>${c ? num(c.count) : '—'}</td><td>${c ? dt(c.updated_at) : 'Não sincronizado'}</td></tr>`;
  }).join('');

  document.getElementById('content').innerHTML = `
    <div class="page-title">🔗 Sincronização Mobne</div>
    <div class="page-subtitle">${esc(s.source)} · intervalo automático: ${s.interval_minutes} min</div>

    <div class="btn-row">
      <button class="btn-primary btn-wide" data-sync="recent">Sincronizar agora (recente)</button>
      <button class="btn-secondary" data-sync="reconcile">Reconciliar todo o histórico</button>
      <button class="btn-secondary" data-sync="history">Carregar histórico completo</button>
    </div>

    <div class="section-header">Catálogos</div>
    <div class="data-table-container">
      <table class="data-table"><thead><tr><th>Base</th><th>Itens</th><th>Atualizado</th></tr></thead>
      <tbody>${catalogRows}</tbody></table>
    </div>

    <div class="section-header">Períodos de vendas sincronizados</div>
    <div class="data-table-container table-scroll-md">
      <table class="data-table"><thead><tr><th>Período</th><th>Atualizado</th><th>Versão</th></tr></thead>
      <tbody>${(s.periods || []).map((p) => `<tr><td>${p.period}</td><td>${dt(p.updated_at)}</td><td>${p.version}</td></tr>`).join('') || '<tr><td colspan="3">Nenhum período ainda.</td></tr>'}</tbody></table>
    </div>

    <div class="section-header">Execuções recentes</div>
    <div class="jobs-list">
      ${(s.jobs || []).map((j) => `<div class="job-row">
          <div><strong>${j.mode}</strong> ${jobBadge(j.state)}
            <div class="job-detail">${esc(j.detail || '')}</div>
            ${j.error ? `<div class="job-error">${esc(j.error)}</div>` : ''}
          </div>
          <div class="job-detail">${dt(j.updated_at)}${j.total ? ` · ${j.completed}/${j.total}` : ''}</div>
        </div>`).join('') || '<div class="story-box">Nenhuma execução ainda.</div>'}
    </div>
  `;
}

async function triggerSync(mode) {
  try {
    await api(`/api/companies/${APP.company}/sync`, {method: 'POST', body: JSON.stringify({mode})});
    await refreshStatus();
    renderSyncPage();
  } catch (e) {
    alert('Não foi possível iniciar a sincronização: ' + e.message);
  }
}

/* ---------------------------------------------------------------- config */

let custoFixoTimer = null;
function updateCustoFixo() {
  clearTimeout(custoFixoTimer);
  custoFixoTimer = setTimeout(async () => {
    const value = parseFloat(document.getElementById('custo-fixo-input').value);
    if (isNaN(value) || value < 0) return;
    try {
      await api(`/api/companies/${APP.company}/config`, {
        method: 'PUT', body: JSON.stringify({fixed_cost_cents: Math.round(value * 100)}),
      });
      APP.dashboard = null;
      if (APP.page !== 'sync') renderPage();
    } catch (e) {
      alert('Não foi possível salvar o custo fixo: ' + e.message);
    }
  }, 500);
}

boot();

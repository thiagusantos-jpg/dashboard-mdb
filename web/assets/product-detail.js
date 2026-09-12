/* Reposição preventiva e página de detalhe do produto. Loaded before app.js
 * and uses its shared api(), esc(), money(), num(), pct(), dt(), APP globals. */
'use strict';

async function renderReposicao(data, token) {
  token = token || beginPage();
  document.getElementById('content').innerHTML = `
    <div class="page-title">${icon('target')} Reposição</div>
    <div class="page-subtitle">Sugestão de compra por produto, com todos os números que formam a conta.</div>
    <div class="skeleton-block" aria-label="Carregando reposição"></div>`;
  let recommendations;
  try {
    recommendations = await api(`/api/companies/${APP.company}/replenishment?as_of=${APP.period}-01`);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;  // usuário já saiu desta rota
    document.getElementById('content').innerHTML = `
      <div class="page-title">${icon('target')} Reposição</div>
      <div class="story-box mt-16">Não foi possível carregar: ${esc(e.message)}</div>`;
    return;
  }
  if (!APP.pageState.isCurrent(token)) return;  // resposta obsoleta: descarta em silêncio
  const rows = recommendations.map((r) => `
    <tr>
      <td><a href="#/produto/${esc(APP.period)}?id=${esc(r.product_id)}">${esc(r.name)}</a></td>
      <td class="num">${dec2(r.daily_demand)}</td>
      <td class="num">${dec2(r.available)}</td>
      <td class="num">${dec2(r.reorder_point)}</td>
      <td class="num">${num(r.suggested_quantity)}</td>
      <td class="num">${r.lead_days}d + ${r.safety_days}d</td>
      <td class="num">${r.pack_size}</td>
    </tr>`).join('');
  document.getElementById('content').innerHTML = `
    <div class="page-title">${icon('target')} Reposição</div>
    <div class="page-subtitle">Sugestão de compra por produto, com todos os números que formam a conta.</div>
    <span class="periodo-badge">📅 Base: ${esc(APP.period)}</span>
    <hr class="divider">
    <div class="table-wrap"><table>
      <thead><tr><th>Produto</th><th>Demanda/dia</th><th>Disponível</th><th>Ponto de pedido</th>
        <th>Sugerido</th><th>Lead + segurança</th><th>Múltiplo</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="7">Nenhum produto precisa de reposição agora.</td></tr>'}</tbody>
    </table></div>`;
}

async function renderProdutoDetalhe(data, token) {
  token = token || beginPage();
  const productId = APP.routeParams.get('id');
  document.getElementById('content').innerHTML = `
    <div class="page-title">${icon('package')} Produto</div>
    <div class="skeleton-block" aria-label="Carregando produto"></div>`;
  if (!productId) {
    document.getElementById('content').innerHTML = `
      <div class="page-title">${icon('package')} Produto</div>
      <div class="story-box mt-16">Nenhum produto selecionado. Volte para Produtos &amp; Estoque e escolha um item.</div>`;
    return;
  }
  let detail;
  try {
    detail = await api(`/api/companies/${APP.company}/products/${productId}?period=${APP.period}`);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;  // usuário já saiu desta rota
    document.getElementById('content').innerHTML = `
      <div class="page-title">${icon('package')} Produto</div>
      <div class="story-box mt-16">Não foi possível carregar: ${esc(e.message)}</div>`;
    return;
  }
  if (!APP.pageState.isCurrent(token)) return;  // resposta obsoleta: descarta em silêncio
  const p = detail.product;
  const r = detail.replenishment;
  const historyRows = detail.history.map((h) => `
    <tr>
      <td>${dateBR(h.observed_at)}</td>
      <td class="num">${h.stock_quantity == null ? '—' : num(h.stock_quantity)}</td>
      <td class="num">${money(h.price_cents)}</td>
      <td class="num">${money(h.cost_cents)}</td>
    </tr>`).join('');

  document.getElementById('content').innerHTML = `
    <div class="page-title">${icon('package')} ${esc(p.name)}</div>
    <div class="page-subtitle">${esc(p.category)} · Competência: ${esc(APP.period)}</div>
    <hr class="divider">
    <div class="kpi-grid kpi-grid-4">
      ${kpi('Receita no período', money(p.revenue))}
      ${kpi('Margem', p.margin == null ? '—' : pct(p.margin))}
      ${kpi('Preço atual', money(p.current_price))}
      ${kpi('Estoque atual', p.stock == null ? '—' : num(p.stock))}
    </div>
    <div class="section-header">Reposição sugerida</div>
    ${r ? `
      <div class="table-wrap"><table>
        <thead><tr><th>Demanda/dia</th><th>Disponível</th><th>Ponto de pedido</th><th>Sugerido</th></tr></thead>
        <tbody><tr>
          <td class="num">${dec2(r.daily_demand)}</td>
          <td class="num">${dec2(r.available)}</td>
          <td class="num">${dec2(r.reorder_point)}</td>
          <td class="num">${num(r.suggested_quantity)}</td>
        </tr></tbody>
      </table></div>`
      : '<div class="story-box">Nenhuma reposição sugerida para este produto agora.</div>'}
    <div class="section-header">Histórico de estoque e preço</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Data</th><th>Estoque</th><th>Preço</th><th>Custo</th></tr></thead>
      <tbody>${historyRows || '<tr><td colspan="4">Nenhum snapshot registrado ainda.</td></tr>'}</tbody>
    </table></div>
    <div class="section-header">Ações e promoções</div>
    <div class="story-box">Central de Ações e histórico de promoções deste produto serão exibidos aqui.</div>`;
}

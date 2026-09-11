/* ============================================================
   MERCADO duBAIRRO — Dashboard App (Static Version)
   All 6 pages with Plotly.js charts
   ============================================================ */

const CUSTO_FIXO_DEFAULT = 16913.46;
const META_LIQUIDA = 0.15;
const COLORS = {
  yellow: '#FFC107', dark: '#2D2D2D', gray: '#666666',
  green: '#27AE60', red: '#E74C3C', blue: '#2E86C1',
  light_gray: '#F5F5F5', orange: '#F39C12',
  green_dark: '#1E8449', green_light: '#82E0AA',
  yellow_light: '#F9E79F', red_light: '#F5B7B1',
};
const MESES_NOMES = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];
const MESES_LABELS = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
const DAY_MAP = { Monday: 'Segunda', Tuesday: 'Terça', Wednesday: 'Quarta', Thursday: 'Quinta', Friday: 'Sexta', Saturday: 'Sábado', Sunday: 'Domingo' };
const DAY_ORDER = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'];

let DATA = {};
let CUSTO_FIXO = CUSTO_FIXO_DEFAULT;
let currentPage = 'resumo';

// Month selector state
let SELECTED_MES = null;      // Mês selecionado (1-12)
let SELECTED_ANO = 2026;      // Ano selecionado
let AVAILABLE_MESES = {};     // Mapa de períodos disponíveis: "2025_1", "2026_1", etc

// ============================================================
// HELPERS
// ============================================================
function safeDiv(a, b, d = 0) { return b !== 0 && b != null ? a / b : d; }
function fmt(v) { return v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function fmtInt(v) { return Math.round(v).toLocaleString('pt-BR'); }
function fmtK(v) { return 'R$' + Math.round(v / 1000) + 'k'; }
function deltaColor(v, tol = 2) { if (Math.abs(v) <= tol) return 'kpi-neutral'; return v > 0 ? 'kpi-positive' : 'kpi-negative'; }
function deltaArrow(v, tol = 2) { if (Math.abs(v) <= tol) return '●'; return v > 0 ? '▲' : '▼'; }

function getMesRef(yoy) {
  const m26 = yoy.filter(r => r.Receita_2026 > 0);
  if (m26.length > 0) { const last = m26[m26.length - 1]; return { nome: last.Mes, num: last.Mes_Num }; }
  return { nome: 'Janeiro', num: 1 };
}

// Month selector functions
function getSelectedMes() {
  const nome = MESES_NOMES[SELECTED_MES] || 'Janeiro';
  return { nome, num: SELECTED_MES, ano: SELECTED_ANO };
}

function filterDataByPeriod(data, mes, ano) {
  return data.filter(row => {
    const [m, y] = row.Periodo.split('/');
    return parseInt(m) === mes && parseInt(y) === ano;
  });
}

function detectAvailablePeriods() {
  AVAILABLE_MESES = {};
  DATA.yoy.forEach(row => {
    if (row.Receita_2025 > 0) AVAILABLE_MESES[`2025_${row.Mes_Num}`] = true;
    if (row.Receita_2026 > 0) AVAILABLE_MESES[`2026_${row.Mes_Num}`] = true;
  });

  // Auto-detect most recent month with 2026 data
  const m26 = DATA.yoy.filter(r => r.Receita_2026 > 0);
  if (m26.length > 0) {
    const last = m26[m26.length - 1];
    SELECTED_MES = last.Mes_Num;
    SELECTED_ANO = 2026;
  }
}

function selectMes(mes, ano) {
  // Auto-select first available month if switching year and current month unavailable
  if (ano !== SELECTED_ANO && !AVAILABLE_MESES[`${ano}_${mes}`]) {
    for (let i = 1; i <= 12; i++) {
      if (AVAILABLE_MESES[`${ano}_${i}`]) {
        mes = i;
        break;
      }
    }
  }

  SELECTED_MES = mes;
  SELECTED_ANO = ano;

  localStorage.setItem('dubairro_selected_period', JSON.stringify({
    mes: mes,
    ano: ano,
    timestamp: Date.now()
  }));

  renderMonthSelector();
  updateYearTabs();
  navigate(currentPage);
}

function restoreSelectedPeriod() {
  const saved = localStorage.getItem('dubairro_selected_period');
  if (saved) {
    try {
      const { mes, ano } = JSON.parse(saved);
      if (AVAILABLE_MESES[`${ano}_${mes}`]) {
        SELECTED_MES = mes;
        SELECTED_ANO = ano;
        return;
      }
    } catch (e) {
      console.warn('Invalid saved period:', e);
    }
  }
  // Fall back to auto-detect if saved data unavailable
}

function renderMonthSelector() {
  const container = document.getElementById('month-buttons');
  if (!container) return;

  container.innerHTML = '';

  for (let i = 1; i <= 12; i++) {
    const btn = document.createElement('button');
    btn.className = 'month-btn';
    btn.textContent = MESES_LABELS[i - 1];

    const key = `${SELECTED_ANO}_${i}`;
    const hasData = AVAILABLE_MESES[key];

    if (!hasData) {
      btn.disabled = true;
    }

    if (SELECTED_MES === i) {
      btn.classList.add('active');
    }

    btn.onclick = () => {
      if (hasData) selectMes(i, SELECTED_ANO);
    };

    container.appendChild(btn);
  }
}

function updateYearTabs() {
  document.querySelectorAll('.tab-btn-period').forEach(btn => {
    btn.classList.remove('active');
    if (parseInt(btn.dataset.year) === SELECTED_ANO) {
      btn.classList.add('active');
    }
  });
}

function sumField(arr, field) { return arr.reduce((s, r) => s + (r[field] || 0), 0); }

function kpiCard(title, value, subtitle, colorClass) {
  const sub = subtitle ? `<div class="kpi-subtitle ${colorClass || ''}">${subtitle}</div>` : '';
  return `<div class="kpi-card"><div class="kpi-title">${title}</div><div class="kpi-value">${value}</div>${sub}</div>`;
}

function storyBox(text) { return `<div class="story-box">💡 ${text}</div>`; }
function sectionHeader(text) { return `<div class="section-header">${text}</div>`; }
function periodoBadge(mes, ano) { return `<span class="periodo-badge">📅 Analisando: ${mes}/${ano}</span>`; }

function tooltipBox(title, what, how, why, example) {
  const ex = example ? `<p><strong>Exemplo prático:</strong> ${example}</p>` : '';
  return `<div class="tooltip-box">
    <button class="tooltip-toggle" onclick="this.nextElementSibling.classList.toggle('open')">💡 Como interpretar: ${title}</button>
    <div class="tooltip-content">
      <p><strong>O que mostra:</strong> ${what}</p>
      <p><strong>Como ler:</strong> ${how}</p>
      <p><strong>Por que importa:</strong> ${why}</p>${ex}
    </div></div>`;
}

function dataTable(headers, rows, maxRows) {
  const limited = maxRows ? rows.slice(0, maxRows) : rows;
  let html = '<div class="data-table-container"><table class="data-table"><thead><tr>';
  headers.forEach(h => html += `<th>${h}</th>`);
  html += '</tr></thead><tbody>';
  limited.forEach(r => { html += '<tr>'; r.forEach(c => html += `<td>${c}</td>`); html += '</tr>'; });
  html += '</tbody></table></div>';
  return html;
}

function margemColor(md) {
  if (md > 55) return COLORS.green_dark;
  if (md > 40) return COLORS.green;
  if (md > 30) return COLORS.orange;
  return COLORS.red;
}

// ============================================================
// DATA LOADING
// ============================================================
async function loadData() {
  const files = ['vendas_mensais', 'vendas_diarias', 'produtos', 'calendario', 'yoy', 'erosao'];
  const promises = files.map(f =>
    fetch(`/data/${f}.json`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${r.statusText}`);
        return r.json();
      })
      .catch(err => {
        console.error(`Erro ao carregar ${f}.json:`, err);
        throw new Error(`Falha ao carregar ${f}.json: ${err.message}`);
      })
  );

  try {
    const results = await Promise.all(promises);
    files.forEach((f, i) => DATA[f] = results[i]);

    // Initialize month selector
    detectAvailablePeriods();
    restoreSelectedPeriod();
    renderMonthSelector();
  } catch (error) {
    throw new Error(`Erro ao carregar dados: ${error.message}`);
  }
}

// ============================================================
// PAGE 1: RESUMO EXECUTIVO
// ============================================================
function pageResumo() {
  const vm = filterDataByPeriod(DATA.vendas_mensais, SELECTED_MES, SELECTED_ANO), yoy = DATA.yoy, produtos = DATA.produtos;
  const { nome: mesNome, num: mesNum, ano: mesAno } = getSelectedMes();

  const fat = sumField(vm, 'Vlr_Venda'), lb = sumField(vm, 'Vlr_Lucro'), ll = lb - CUSTO_FIXO;
  const mb = safeDiv(lb, fat) * 100, mr = safeDiv(ll, fat) * 100;
  const pe = mb > 0 ? safeDiv(CUSTO_FIXO, mb / 100) : 0;
  const folga = pe > 0 ? (safeDiv(fat, pe) - 1) * 100 : 0;
  const cupons = sumField(vm, 'Qtde_Documentos'), tm = safeDiv(fat, cupons);
  const skus = produtos.length;

  let vr = 0, vl = 0, vc = 0, vt = 0, c25 = 0, r25 = 0, t25 = 0;
  const yoyMes = yoy.filter(r => r.Receita_2026 > 0);
  if (yoyMes.length > 0) {
    const row = yoyMes[yoyMes.length - 1];
    vr = row.Var_Receita_Pct || 0; vl = row.Var_Lucro_Pct || 0;
    c25 = row.Cupons_2025 || 0; r25 = row.Receita_2025 || 0;
    t25 = safeDiv(r25, c25); vc = safeDiv(cupons - c25, c25) * 100; vt = safeDiv(tm - t25, t25) * 100;
  }

  const status = mr > 20 ? '✅ Saudável' : (mr > 15 ? '⚠️ Atenção' : '🔴 Crítico');
  const ca = produtos.filter(p => p.Curva === 'A').length;

  let html = `<h2>📊 Resumo Executivo</h2><p class="page-subtitle">Como foi o mês? Estamos melhor ou pior que antes?</p>`;
  html += periodoBadge(mesNome, mesAno) + '<hr class="divider">';

  // KPI Row 1
  html += '<div class="kpi-grid kpi-grid-4">';
  html += kpiCard('Faturamento do Mês', `R$ ${fmt(fat)}`, `${deltaArrow(vr)} ${vr >= 0 ? '+' : ''}${vr.toFixed(1)}% vs ${mesNome}/25`, deltaColor(vr));
  html += kpiCard('Lucro Líquido', `R$ ${fmt(ll)}`, `Bruto: R$ ${fmt(lb)} − Fixo: R$ ${fmt(CUSTO_FIXO)}`);
  html += kpiCard('Margem Real', `${mr.toFixed(1)}%`, `Meta: 15% | ${status}`);
  html += kpiCard('Ponto de Equilíbrio', `R$ ${fmtInt(pe)}`, `Folga de ${Math.round(folga)}%`, folga > 50 ? 'kpi-positive' : 'kpi-negative');
  html += '</div>';

  // KPI Row 2
  html += '<div class="kpi-grid kpi-grid-4">';
  html += kpiCard('Nº de Cupons (Clientes)', fmtInt(cupons), `${deltaArrow(vc)} ${vc >= 0 ? '+' : ''}${vc.toFixed(1)}% vs ${mesNome}/25`, deltaColor(vc));
  html += kpiCard('Ticket Médio', `R$ ${fmt(tm)}`, `${deltaArrow(vt)} ${vt >= 0 ? '+' : ''}${vt.toFixed(1)}% vs ${mesNome}/25`, deltaColor(vt));
  html += kpiCard('SKUs Ativos', fmtInt(skus), `Curva A: ${ca} produtos`);
  html += kpiCard('Variação YoY Lucro', `${vl >= 0 ? '+' : ''}${vl.toFixed(1)}%`, `${deltaArrow(vl)} ${vl >= 0 ? '+' : ''}${vl.toFixed(1)}% vs ${mesNome}/25`, deltaColor(vl));
  html += '</div>';

  html += tooltipBox('KPIs do Resumo Executivo',
    'Os 8 indicadores-chave do mês, comparados com o mesmo mês do ano anterior.',
    'Setas verdes (▲) = melhoria, vermelhas (▼) = piora, laranja (●) = estável (variação menor que ±2%).',
    'Permite em 5 segundos entender a saúde geral do mercado.',
    `Faturamento de ${mesNome}/26: R$ ${fmtInt(fat)}. Em ${mesNome}/25: R$ ${fmtInt(r25)}. Variação de ${vr >= 0 ? '+' : ''}${vr.toFixed(1)}%.`);

  if (vr !== 0) {
    const ef = Math.abs(vl) < Math.abs(vr) ? 'mais eficientes — vendemos menos, mas lucramos mais por real vendido' : 'com desafios de margem';
    html += storyBox(`Em ${mesNome}/26, o faturamento variou ${vr >= 0 ? '+' : ''}${vr.toFixed(1)}% vs ${mesNome}/25, mas o lucro variou ${vl >= 0 ? '+' : ''}${vl.toFixed(1)}%. Estamos ${ef}. O fluxo de clientes variou ${vc >= 0 ? '+' : ''}${Math.round(vc)}% e o ticket médio variou ${vt >= 0 ? '+' : ''}${Math.round(vt)}%.`);
  }

  html += '<hr class="divider"><div class="row"><div class="col-60">';
  html += sectionHeader('Evolução Mensal — 2025 vs 2026 (mês a mês)');
  html += '<div id="chart-evolucao" class="chart-container"></div>';
  html += tooltipBox('Evolução Mensal 2025 vs 2026', 'Barras cinzas = 2025. Barras amarelas = 2026. Linhas = lucro.', 'Compare cada mês lado a lado.', 'Identifica tendências de crescimento ou queda.');
  html += '</div><div class="col-40">';
  html += sectionHeader(`Participação por Categoria (${mesNome}/26)`);
  html += '<div id="chart-treemap" class="chart-container"></div>';
  html += '<p class="chart-caption">🟢 Margem > 55%  |  🟡 40-55%  |  🟠 30-40%  |  🔴 < 30%</p>';
  html += tooltipBox('Treemap por Categoria', 'Tamanho = faturamento. Cor = margem.', 'Blocos grandes + verdes = categorias fortes.', 'Mostra de onde vem o dinheiro e se é lucrativo.');
  html += '</div></div>';

  html += sectionHeader(`Top 10 Produtos por Lucro — ${mesNome}/26`);
  html += '<div id="chart-top10" class="chart-container"></div>';

  const top10 = [...produtos].sort((a, b) => b.Lucro_Total - a.Lucro_Total).slice(0, 10);
  const lt = top10.reduce((s, p) => s + p.Lucro_Total, 0);
  const ltot = sumField(produtos, 'Lucro_Total');
  const pct = safeDiv(lt, ltot) * 100;
  html += tooltipBox('Top 10 por Lucro', 'Os 10 produtos mais lucrativos. Cinza = custo, verde = lucro.', 'Quanto mais verde, melhor a margem.', 'Proteger estoque e preço desses produtos a todo custo.', `Juntos representam ${Math.round(pct)}% do lucro total.`);
  html += storyBox(`Os 10 produtos mais lucrativos representam ${Math.round(pct)}% do lucro. ${top10[0].Produto} lidera com R$ ${fmtInt(top10[0].Lucro_Total)}.`);

  document.getElementById('content').innerHTML = html;

  // Render charts
  renderEvolucaoChart(yoy);
  renderTreemap(vm);
  renderTop10Chart(top10);
}

function renderEvolucaoChart(yoy) {
  const d25meses = [], d25rec = [], d25luc = [], d26meses = [], d26rec = [], d26luc = [];
  yoy.forEach(r => {
    if (r.Receita_2025 > 0) { d25meses.push(r.Mes); d25rec.push(r.Receita_2025); d25luc.push(r.Lucro_2025); }
    if (r.Receita_2026 > 0) { d26meses.push(r.Mes); d26rec.push(r.Receita_2026); d26luc.push(r.Lucro_2026); }
  });
  const traces = [
    { x: d25meses, y: d25rec, name: 'Fat. 2025', type: 'bar', marker: { color: '#D5DBDB' }, text: d25rec.map(fmtK), textposition: 'outside', textfont: { size: 9 } },
    { x: d26meses, y: d26rec, name: 'Fat. 2026', type: 'bar', marker: { color: COLORS.yellow }, text: d26rec.map(fmtK), textposition: 'outside', textfont: { size: 9 } },
    { x: d25meses, y: d25luc, name: 'Lucro 2025', type: 'scatter', mode: 'lines+markers', line: { color: COLORS.green, width: 2, dash: 'dot' }, yaxis: 'y2' },
    { x: d26meses, y: d26luc, name: 'Lucro 2026', type: 'scatter', mode: 'lines+markers', line: { color: COLORS.green_dark, width: 3 }, yaxis: 'y2' },
  ];
  const layout = { barmode: 'group', height: 380, margin: { l: 50, r: 50, t: 30, b: 40 }, legend: { orientation: 'h', y: -0.15 }, plot_bgcolor: 'white', yaxis: { title: 'Faturamento (R$)' }, yaxis2: { title: 'Lucro (R$)', overlaying: 'y', side: 'right' } };
  Plotly.newPlot('chart-evolucao', traces, layout, { responsive: true });
}

function renderTreemap(vm) {
  const sorted = [...vm].sort((a, b) => b.Vlr_Venda - a.Vlr_Venda);
  const trace = {
    type: 'treemap', labels: sorted.map(r => r.Categoria), parents: sorted.map(() => ''),
    values: sorted.map(r => r.Vlr_Venda), texttemplate: '<b>%{label}</b><br>R$%{value:,.0f}',
    marker: { colors: sorted.map(r => margemColor(r.Markdown_Pct)) },
    hovertemplate: '<b>%{label}</b><br>R$%{value:,.2f}<extra></extra>'
  };
  Plotly.newPlot('chart-treemap', [trace], { height: 380, margin: { l: 10, r: 10, t: 10, b: 10 } }, { responsive: true });
}

function renderTop10Chart(top10) {
  const reversed = [...top10].reverse();
  const traces = [
    { y: reversed.map(p => p.Produto), x: reversed.map(p => p.Receita_Total - p.Lucro_Total), name: 'Custo', type: 'bar', orientation: 'h', marker: { color: '#D5DBDB' } },
    { y: reversed.map(p => p.Produto), x: reversed.map(p => p.Lucro_Total), name: 'Lucro', type: 'bar', orientation: 'h', marker: { color: COLORS.green }, text: reversed.map(p => `R$ ${fmtInt(p.Lucro_Total)}`), textposition: 'outside', textfont: { size: 10 } },
  ];
  Plotly.newPlot('chart-top10', traces, { barmode: 'stack', height: 350, margin: { l: 250, r: 80, t: 10, b: 30 }, legend: { orientation: 'h', y: -0.1 }, plot_bgcolor: 'white', xaxis: { title: 'R$' } }, { responsive: true });
}

// ============================================================
// PAGE 2: INTELIGENCIA DE PRECOS
// ============================================================
function pagePrecos() {
  const vm = filterDataByPeriod(DATA.vendas_mensais, SELECTED_MES, SELECTED_ANO), erosao = DATA.erosao, produtos = DATA.produtos;
  const { nome: mesNome, ano: mesAno } = getSelectedMes();
  const tv = sumField(vm, 'Vlr_Venda');
  const mdm = safeDiv(vm.reduce((s, r) => s + (r.Vlr_Venda || 0) * (r.Markdown_Pct || 0) / 100, 0), tv) * 100;
  const cs = erosao.filter(r => r.Alerta && r.Alerta.includes('SUBIU'));
  const cc = erosao.filter(r => r.Alerta && r.Alerta.includes('CAIU'));
  const ca = produtos.filter(p => p.Curva === 'A');
  const mbLow = ca.filter(p => p.Margem_Media < 35);
  const oport = sumField(mbLow, 'Receita_Total') * 0.05;

  let html = `<h2>💰 Inteligência de Preços</h2><p class="page-subtitle">Onde estou deixando dinheiro na mesa?</p>`;
  html += periodoBadge(mesNome, mesAno) + '<hr class="divider">';

  html += '<div class="kpi-grid kpi-grid-3">';
  html += kpiCard('Markdown Médio Ponderado', `${mdm.toFixed(1)}%`, `De cada R$1 vendido, R$ ${(mdm / 100).toFixed(2)} é margem`);
  html += kpiCard('Produtos com Custo Subindo', `${cs.length}`, 'Curva A com erosão detectada', 'kpi-negative');
  html += kpiCard('Oportunidade Estimada', `R$ ${fmtInt(oport)}/mês`, `${mbLow.length} produtos com margem < 35%`, 'kpi-neutral');
  html += '</div>';
  html += tooltipBox('KPIs de Preços', 'Markdown = margem bruta. Erosão = custo subiu sem reajuste.', 'Markdown alto = saudável. Custo subindo = alerta.', 'Proteger a margem é proteger o lucro.', `${cs.length} produtos precisam de reajuste.`);
  html += storyBox(`Margem média: ${mdm.toFixed(1)}%. ${cs.length} produtos Curva A com custo subindo — reajustar para evitar erosão.`);
  html += '<hr class="divider">';

  html += '<div class="row"><div class="col-60">';
  html += sectionHeader(`Duelo de Produtos (${mesNome}/26)`);
  html += '<div id="chart-scatter-precos" class="chart-container"></div>';
  html += tooltipBox('Scatter Plot de Preços', 'Cada bolha = produto Curva A. X = faturamento. Y = margem. Tamanho = lucro.', 'Superior direito = melhor. Inferior direito = vende mas não lucra.', 'Identifica onde reajustar preço.');
  html += '</div><div class="col-40">';
  html += sectionHeader(`Ranking Margem por Categoria (${mesNome}/26)`);
  const crk = [...vm].sort((a, b) => b.Markdown_Pct - a.Markdown_Pct);
  const headers = ['Status', 'Categoria', 'Fat.', 'Markdown'];
  const rows = crk.map(r => [
    r.Markdown_Pct > 55 ? '🟢' : (r.Markdown_Pct > 40 ? '🟡' : '🔴'),
    r.Categoria, `R$ ${fmtInt(r.Vlr_Venda)}`, `${r.Markdown_Pct.toFixed(1)}%`
  ]);
  html += dataTable(headers, rows);
  html += tooltipBox('Ranking por Categoria', '24 categorias ordenadas por margem. 🟢>55% 🟡40-55% 🔴<40%.', 'Categorias 🔴 com alto faturamento são as mais urgentes.', 'Renegociar fornecedores ou reajustar preços.');
  html += '</div></div>';

  // Erosion alerts
  html += sectionHeader(`🚨 Alerta de Erosão — Curva A (${mesNome}/26)`);
  html += '<p style="color:#666;font-size:13px;">Produtos onde o custo de reposição mudou significativamente.</p>';
  html += '<div class="tabs"><button class="tab-btn active" onclick="switchTab(this,\'tab-subiu\')">🔴 Custo Subiu</button>';
  html += '<button class="tab-btn" onclick="switchTab(this,\'tab-caiu\')">🟢 Custo Caiu</button></div>';

  html += '<div id="tab-subiu" class="tab-content active">';
  if (cs.length > 0) {
    const csRows = [...cs].sort((a, b) => a.Erosao_Margem - b.Erosao_Margem).map(r => [
      r.Produto, `R$ ${fmt(r.Vlr_Venda)}`, `${(r.Margem_Pct || 0).toFixed(1)}%`,
      `${(r.Markdown_Pct || 0).toFixed(1)}%`, `${(r.Markdown_Ult_Entrada || 0).toFixed(1)}%`,
      `${(r.Erosao_Margem || 0).toFixed(1)} pts`
    ]);
    html += dataTable(['Produto', 'Faturamento', 'Margem %', 'Markdown Atual', 'Markdown Ult. Entrada', 'Erosão (pts)'], csRows);
    html += storyBox(`${cs.length} produtos com custo subindo. Reajustar preço para proteger margem futura.`);
  } else { html += '<div class="badge-success">Nenhum produto com custo subindo!</div>'; }
  html += '</div>';

  html += '<div id="tab-caiu" class="tab-content">';
  if (cc.length > 0) {
    const ccRows = [...cc].sort((a, b) => a.Erosao_Margem - b.Erosao_Margem).map(r => [
      r.Produto, `R$ ${fmt(r.Vlr_Venda)}`, `${(r.Margem_Pct || 0).toFixed(1)}%`,
      `${(r.Markdown_Pct || 0).toFixed(1)}%`, `${(r.Markdown_Ult_Entrada || 0).toFixed(1)}%`,
      `${(r.Erosao_Margem || 0).toFixed(1)} pts`
    ]);
    html += dataTable(['Produto', 'Faturamento', 'Margem %', 'Markdown Atual', 'Markdown Ult. Entrada', 'Erosão (pts)'], ccRows);
    html += storyBox(`${cc.length} produtos com custo caindo. Mantenha preço para aumentar margem!`);
  } else { html += '<div class="badge-info">Nenhum produto com custo caindo.</div>'; }
  html += '</div>';

  html += tooltipBox('Erosão de Margem', 'Compara markdown atual vs última entrada. Diferença = tendência do custo.', 'Positivo = custo subiu (ruim). Negativo = custo caiu (bom).', 'Alerta antecipado do que VAI acontecer com a margem.');

  document.getElementById('content').innerHTML = html;

  // Scatter chart
  const cap = ca.filter(p => p.Receita_Total > 50);
  const classColors = { '⭐ Estrela': COLORS.green, '💰 Gerador de Caixa': COLORS.yellow, '🔍 Oportunidade': COLORS.blue, '⚠️ Peso Morto': COLORS.red };
  const groups = {};
  cap.forEach(p => { const c = p.Classificacao; if (!groups[c]) groups[c] = []; groups[c].push(p); });
  const traces = Object.entries(groups).map(([cls, prods]) => ({
    x: prods.map(p => p.Receita_Total), y: prods.map(p => p.Margem_Media),
    text: prods.map(p => p.Produto), mode: 'markers', name: cls, type: 'scatter',
    marker: { color: classColors[cls] || '#999', size: prods.map(p => Math.max(8, Math.min(30, p.Lucro_Total / 50))), opacity: 0.7 },
    hovertemplate: '<b>%{text}</b><br>Receita: R$%{x:,.2f}<br>Margem: %{y:.1f}%<extra></extra>'
  }));
  const ar = cap.length > 0 ? sumField(cap, 'Receita_Total') / cap.length : 0;
  Plotly.newPlot('chart-scatter-precos', traces, {
    height: 450, plot_bgcolor: 'white', margin: { l: 50, r: 20, t: 30, b: 40 },
    xaxis: { title: 'Faturamento (R$)' }, yaxis: { title: 'Margem (%)' },
    legend: { orientation: 'h', y: -0.15 },
    shapes: [
      { type: 'line', x0: ar, x1: ar, y0: 0, y1: 100, line: { dash: 'dash', color: '#999' } },
      { type: 'line', x0: 0, x1: Math.max(...cap.map(p => p.Receita_Total)) * 1.1, y0: mdm, y1: mdm, line: { dash: 'dash', color: '#999' } }
    ],
    annotations: [
      { x: ar, y: 95, text: `Receita: R$${Math.round(ar)}`, showarrow: false, font: { size: 10, color: '#999' } },
      { x: Math.max(...cap.map(p => p.Receita_Total)) * 0.9, y: mdm + 3, text: `Margem: ${mdm.toFixed(0)}%`, showarrow: false, font: { size: 10, color: '#999' } }
    ]
  }, { responsive: true });
}

// ============================================================
// PAGE 3: MAPA DE PRODUTOS
// ============================================================
function pageMapa() {
  const produtos = DATA.produtos;
  const { nome: mesNome, ano: mesAno } = getSelectedMes();
  const est = produtos.filter(p => p.Classificacao && p.Classificacao.includes('Estrela'));
  const ger = produtos.filter(p => p.Classificacao && p.Classificacao.includes('Gerador'));
  const opo = produtos.filter(p => p.Classificacao && p.Classificacao.includes('Oportunidade'));
  const pm = produtos.filter(p => p.Classificacao && p.Classificacao.includes('Peso Morto'));
  const lt = sumField(produtos, 'Lucro_Total');
  const ps = [...produtos].sort((a, b) => b.Lucro_Total - a.Lucro_Total);
  let cumSum = 0, n80 = 0;
  for (const p of ps) { cumSum += p.Lucro_Total; n80++; if (cumSum >= lt * 0.8) break; }

  let html = `<h2>🗺️ Mapa de Produtos — Matriz de Rentabilidade</h2><p class="page-subtitle">Quais produtos são estrelas e quais são peso morto?</p>`;
  html += periodoBadge(mesNome, mesAno) + '<hr class="divider">';

  html += '<div class="kpi-grid kpi-grid-4">';
  html += kpiCard('⭐ Estrelas', `${est.length}`, `R$ ${fmtInt(sumField(est, 'Lucro_Total'))} lucro`);
  html += kpiCard('💰 Geradores', `${ger.length}`, `R$ ${fmtInt(sumField(ger, 'Lucro_Total'))} lucro`);
  html += kpiCard('🔍 Oportunidades', `${opo.length}`, `R$ ${fmtInt(sumField(opo, 'Lucro_Total'))} lucro`);
  html += kpiCard('⚠️ Peso Morto', `${pm.length}`, `R$ ${fmtInt(sumField(pm, 'Lucro_Total'))} lucro`);
  html += '</div>';
  html += tooltipBox('Matriz 2×2', 'Giro × Margem. ⭐Alto/Alto 💰Alto/Baixo 🔍Baixo/Alto ⚠️Baixo/Baixo.', '⭐Proteger 💰Renegociar 🔍Dar visibilidade ⚠️Avaliar remoção.', 'Permite priorizar decisões sobre cada grupo.', `Apenas ${n80} de ${fmtInt(produtos.length)} produtos geram 80% do lucro.`);
  html += storyBox(`Apenas ${n80} produtos (de ${fmtInt(produtos.length)}) geram 80% do lucro. As ${est.length} Estrelas são intocáveis.`);
  html += '<hr class="divider">';

  html += sectionHeader(`Matriz de Rentabilidade (${mesNome}/26)`);
  html += '<div id="chart-matriz" class="chart-container"></div>';
  html += tooltipBox('Scatter Plot Giro vs Margem', 'Cada bolha = produto. X = giro. Y = margem. Tamanho = faturamento.', 'Superior direito = ⭐. Inferior direito = 💰. Passe o mouse para ver detalhes.', 'Ferramenta principal para decisões de mix.');

  // Tables
  html += '<div class="row"><div class="col-50">';
  html += sectionHeader('⭐ Estrelas');
  const estSorted = [...est].sort((a, b) => b.Lucro_Total - a.Lucro_Total);
  html += dataTable(['Produto', 'Dias', 'Margem %', 'Receita', 'Lucro'],
    estSorted.map(p => [p.Produto, p.Dias_Vendidos, `${(p.Margem_Media || 0).toFixed(1)}%`, `R$ ${fmt(p.Receita_Total)}`, `R$ ${fmt(p.Lucro_Total)}`]));
  html += sectionHeader('🔍 Oportunidades (Top 15)');
  const opoTop = [...opo].sort((a, b) => b.Lucro_Total - a.Lucro_Total).slice(0, 15);
  html += dataTable(['Produto', 'Dias', 'Margem %', 'Receita', 'Lucro'],
    opoTop.map(p => [p.Produto, p.Dias_Vendidos, `${(p.Margem_Media || 0).toFixed(1)}%`, `R$ ${fmt(p.Receita_Total)}`, `R$ ${fmt(p.Lucro_Total)}`]));
  html += '</div><div class="col-50">';
  html += sectionHeader('💰 Geradores de Caixa');
  const gerSorted = [...ger].sort((a, b) => b.Receita_Total - a.Receita_Total);
  html += dataTable(['Produto', 'Dias', 'Margem %', 'Receita', 'Lucro'],
    gerSorted.map(p => [p.Produto, p.Dias_Vendidos, `${(p.Margem_Media || 0).toFixed(1)}%`, `R$ ${fmt(p.Receita_Total)}`, `R$ ${fmt(p.Lucro_Total)}`]));
  html += sectionHeader('⚠️ Peso Morto (Top 15)');
  const pmTop = [...pm].sort((a, b) => b.Receita_Total - a.Receita_Total).slice(0, 15);
  html += dataTable(['Produto', 'Dias', 'Margem %', 'Receita', 'Lucro'],
    pmTop.map(p => [p.Produto, p.Dias_Vendidos, `${(p.Margem_Media || 0).toFixed(1)}%`, `R$ ${fmt(p.Receita_Total)}`, `R$ ${fmt(p.Lucro_Total)}`]));
  html += '</div></div>';

  document.getElementById('content').innerHTML = html;

  // Scatter chart
  const pp = produtos.filter(p => p.Receita_Total > 20);
  const classColors = { '⭐ Estrela': COLORS.green, '💰 Gerador de Caixa': COLORS.yellow, '🔍 Oportunidade': COLORS.blue, '⚠️ Peso Morto': '#CCCCCC' };
  const groups = {};
  pp.forEach(p => { const c = p.Classificacao; if (!groups[c]) groups[c] = []; groups[c].push(p); });
  const traces = Object.entries(groups).map(([cls, prods]) => ({
    x: prods.map(p => p.Giro), y: prods.map(p => p.Margem_Media),
    text: prods.map(p => p.Produto), mode: 'markers', name: cls, type: 'scatter',
    marker: { color: classColors[cls] || '#999', size: prods.map(p => Math.max(6, Math.min(35, p.Receita_Total / 100))), opacity: 0.7 },
    hovertemplate: '<b>%{text}</b><br>Giro: %{x:.0%}<br>Margem: %{y:.1f}%<extra></extra>'
  }));
  Plotly.newPlot('chart-matriz', traces, {
    height: 500, plot_bgcolor: 'white', margin: { l: 50, r: 20, t: 30, b: 40 },
    xaxis: { title: 'Giro (% dias com venda)', range: [-0.05, 1.05], tickformat: '.0%' },
    yaxis: { title: 'Margem (%)' }, legend: { orientation: 'h', y: -0.12 },
    shapes: [
      { type: 'line', x0: 0.6, x1: 0.6, y0: 0, y1: 100, line: { dash: 'dash', color: '#999' } },
      { type: 'line', x0: -0.05, x1: 1.05, y0: 50, y1: 50, line: { dash: 'dash', color: '#999' } }
    ],
    annotations: [
      { x: 0.85, y: 85, text: '⭐ ESTRELAS', showarrow: false, font: { size: 12, color: COLORS.green } },
      { x: 0.85, y: 15, text: '💰 GERADORES', showarrow: false, font: { size: 12, color: COLORS.orange } },
      { x: 0.15, y: 85, text: '🔍 OPORTUNIDADES', showarrow: false, font: { size: 12, color: COLORS.blue } },
      { x: 0.15, y: 15, text: '⚠️ PESO MORTO', showarrow: false, font: { size: 12, color: COLORS.red } }
    ]
  }, { responsive: true });
}

// ============================================================
// PAGE 4: DIAGNOSTICO DE FATURAMENTO
// ============================================================
function pageDiagnostico() {
  const vm = filterDataByPeriod(DATA.vendas_mensais, SELECTED_MES, SELECTED_ANO), vd = DATA.vendas_diarias, yoy = DATA.yoy;
  const { nome: mesNome, ano: mesAno } = getSelectedMes();
  const fat = sumField(vm, 'Vlr_Venda'), cup = sumField(vm, 'Qtde_Documentos'), tk = safeDiv(fat, cup);
  const yoyMes = yoy.filter(r => r.Receita_2026 > 0);
  let c25 = 0, t25 = 0, vc = 0, vt = 0, r25 = 0;
  if (yoyMes.length > 0) {
    const r = yoyMes[yoyMes.length - 1];
    c25 = r.Cupons_2025 || 0; r25 = r.Receita_2025 || 0;
    t25 = safeDiv(r25, c25); vc = safeDiv(cup - c25, c25) * 100; vt = safeDiv(tk - t25, t25) * 100;
  }

  let html = `<h2>🔍 Diagnóstico de Faturamento</h2><p class="page-subtitle">Menos clientes, menos gasto, ou mix mudou?</p>`;
  html += periodoBadge(mesNome, mesAno) + '<hr class="divider">';

  html += '<div class="kpi-grid kpi-grid-4">';
  html += kpiCard('FATURAMENTO =', `R$ ${fmtInt(fat)}`, 'Cupons × Ticket Médio');
  html += kpiCard('Nº Cupons', fmtInt(cup), `${deltaArrow(vc)} ${vc >= 0 ? '+' : ''}${Math.round(vc)}% vs ${mesNome}/25`, deltaColor(vc));
  html += kpiCard('× Ticket Médio', `R$ ${fmt(tk)}`, `${deltaArrow(vt)} ${vt >= 0 ? '+' : ''}${Math.round(vt)}% vs ${mesNome}/25`, deltaColor(vt));
  let diagCard;
  if (r25 > 0 && c25 > 0) {
    const ic = (cup - c25) * t25, it = (tk - t25) * cup;
    diagCard = kpiCard('Diagnóstico', Math.abs(ic) > Math.abs(it) ? 'Fluxo ↓' : 'Ticket ↓', `Cupons: R$ ${ic >= 0 ? '+' : ''}${fmtInt(ic)} | Ticket: R$ ${it >= 0 ? '+' : ''}${fmtInt(it)}`);
  } else { diagCard = kpiCard('Diagnóstico', '—', 'Sem dados YoY'); }
  html += diagCard;
  html += '</div>';
  html += tooltipBox('Decomposição do Faturamento', 'FAT = Cupons × Ticket. Se caiu, ou veio menos gente ou gastou menos.', "'Fluxo ↓' = problema de atração. 'Ticket ↓' = problema de gasto por cliente.", 'Fluxo → marketing/fachada. Ticket → cross-selling/mix.');
  html += storyBox(`Faturamento = ${fmtInt(cup)} cupons × R$ ${fmt(tk)}. Fluxo variou ${vc >= 0 ? '+' : ''}${Math.round(vc)}% e ticket variou ${vt >= 0 ? '+' : ''}${Math.round(vt)}% vs ${mesNome}/25.`);
  html += '<hr class="divider">';

  html += '<div class="row"><div class="col-50">';
  html += sectionHeader(`Contribuição por Categoria (${mesNome}/26)`);
  html += '<div id="chart-contribuicao" class="chart-container"></div>';
  html += tooltipBox('Contribuição por Categoria', 'Top 12 categorias. Verde = lucro positivo.', 'Barras mais altas = mais faturamento.', 'Identifica motores do faturamento.');
  html += '</div><div class="col-50">';
  html += sectionHeader(`Heatmap por Dia (${mesNome}/26)`);
  html += '<div id="chart-heatmap" class="chart-container"></div>';
  html += tooltipBox('Heatmap Semanal', `Faturamento de cada dia de ${mesNome}/26.`, 'Cores quentes = dias fortes. Frias = fracos.', 'Identifica padrões semanais e dias atípicos.');
  html += '</div></div>';

  html += sectionHeader(`Faturamento Médio por Dia (${mesNome}/26)`);
  html += '<div id="chart-diamedio" class="chart-container"></div>';

  // Find best/worst day
  const dayAgg = {};
  vd.forEach(r => {
    const dsp = DAY_MAP[r.Dia_Semana] || r.Dia_Semana;
    if (!dayAgg[dsp]) dayAgg[dsp] = { total: 0, dias: 0 };
    dayAgg[dsp].total += r.Vlr_Venda; dayAgg[dsp].dias++;
  });
  const dayData = DAY_ORDER.filter(d => dayAgg[d]).map(d => ({ dia: d, media: dayAgg[d].total / dayAgg[d].dias }));
  const bd = dayData.length > 0 ? dayData.reduce((a, b) => a.media > b.media ? a : b).dia : 'N/A';
  const wd = dayData.length > 0 ? dayData.reduce((a, b) => a.media < b.media ? a : b).dia : 'N/A';
  html += tooltipBox('Faturamento por Dia da Semana', `Média diária em ${mesNome}/26. Domingo em vermelho.`, 'Barras altas = dias fortes. Use para planejar estoque.', 'Promoções nos dias fracos, reforço nos fortes.');
  html += storyBox(`${bd} é o mais forte, ${wd} o mais fraco. Promoções para ${wd}, reforço de estoque para ${bd}.`);

  document.getElementById('content').innerHTML = html;

  // Category contribution chart
  const vw = [...vm].sort((a, b) => b.Vlr_Venda - a.Vlr_Venda).slice(0, 12);
  Plotly.newPlot('chart-contribuicao', [{
    x: vw.map(r => r.Categoria), y: vw.map(r => r.Vlr_Venda), type: 'bar',
    marker: { color: vw.map(r => r.Vlr_Lucro > 0 ? COLORS.green : COLORS.red) },
    text: vw.map(r => `R$${fmtInt(r.Vlr_Venda)}`), textposition: 'outside', textfont: { size: 9 }
  }], { height: 380, plot_bgcolor: 'white', margin: { l: 50, r: 10, t: 10, b: 100 }, xaxis: { tickangle: -45 }, yaxis: { title: 'Faturamento (R$)' } }, { responsive: true });

  // Heatmap
  const semData = {};
  vd.forEach(r => {
    const dsp = DAY_MAP[r.Dia_Semana] || r.Dia_Semana;
    const key = `${r.Semana}_${dsp}`;
    if (!semData[key]) semData[key] = { sem: r.Semana, dia: dsp, total: 0 };
    semData[key].total += r.Vlr_Venda;
  });
  const semanas = [...new Set(Object.values(semData).map(r => r.Sem))].sort((a, b) => a - b);
  const diasPresentes = DAY_ORDER.filter(d => Object.values(semData).some(r => r.dia === d));
  const zData = semanas.map(s => diasPresentes.map(d => { const k = `${s}_${d}`; return semData[k] ? Math.round(semData[k].total) : 0; }));
  Plotly.newPlot('chart-heatmap', [{
    z: zData, x: diasPresentes, y: semanas.map(s => `Sem ${s}`), type: 'heatmap',
    colorscale: 'YlOrRd', text: zData.map(r => r.map(v => v.toString())), texttemplate: '%{text}', hovertemplate: '%{x} %{y}<br>R$ %{z:,.0f}<extra></extra>'
  }], { height: 380, margin: { l: 60, r: 10, t: 10, b: 40 } }, { responsive: true });

  // Day average chart
  Plotly.newPlot('chart-diamedio', [{
    x: dayData.map(d => d.dia), y: dayData.map(d => d.media), type: 'bar',
    marker: { color: dayData.map(d => d.dia !== 'Domingo' ? COLORS.yellow : COLORS.red) },
    text: dayData.map(d => `R$ ${fmtInt(d.media)}`), textposition: 'outside'
  }], { height: 280, plot_bgcolor: 'white', margin: { l: 50, r: 10, t: 10, b: 30 }, yaxis: { title: 'Fat. Médio (R$)' } }, { responsive: true });
}

// ============================================================
// PAGE 5: SAZONALIDADE E TENDENCIAS
// ============================================================
function pageSazonalidade() {
  const { ano: mesAno } = getSelectedMes();
  const yoy = DATA.yoy, produtos = DATA.produtos;
  const fa25 = sumField(yoy, 'Receita_2025'), fmm25 = safeDiv(fa25, 12), la25 = sumField(yoy, 'Lucro_2025');
  const m26 = yoy.filter(r => r.Receita_2026 > 0), fa26 = sumField(m26, 'Receita_2026');

  let html = `<h2>📈 Sazonalidade e Tendências</h2><p class="page-subtitle">Padrão de 2025 para planejar 2026</p><hr class="divider">`;

  // Projection for Feb/26
  const j25v = yoy.find(r => r.Mes_Num === 1), f25v = yoy.find(r => r.Mes_Num === 2);
  const j26v = yoy.find(r => r.Mes_Num === 1);
  let projFev = null;
  if (j25v && f25v && j26v && j25v.Receita_2025 > 0 && f25v.Receita_2025 > 0 && j26v.Receita_2026 > 0) {
    const sf = f25v.Receita_2025 / j25v.Receita_2025;
    projFev = { valor: j26v.Receita_2026 * sf, sf: sf };
  }

  html += '<div class="kpi-grid kpi-grid-4">';
  html += kpiCard('Faturamento 2025 (Completo)', `R$ ${fmtInt(fa25)}`, `Média: R$ ${fmtInt(fmm25)}/mês`);
  html += kpiCard('Lucro 2025 (Completo)', `R$ ${fmtInt(la25)}`, `Margem: ${(safeDiv(la25, fa25) * 100).toFixed(1)}%`);
  html += kpiCard('Acumulado 2026', `R$ ${fmtInt(fa26)}`, `${m26.length} mês(es)`);
  html += projFev
    ? kpiCard('Projeção Fev/26', `R$ ${fmtInt(projFev.valor)}`, `Fev/25 foi ${((projFev.sf - 1) * 100) >= 0 ? '+' : ''}${((projFev.sf - 1) * 100).toFixed(1)}% vs Jan/25`)
    : kpiCard('Projeção Fev/26', '—', 'Dados insuficientes');
  html += '</div>';
  html += tooltipBox('KPIs Sazonalidade', '2025 completo (referência) + 2026 parcial + projeção.', 'Projeção usa padrão sazonal: se Fev/25 foi X% vs Jan/25, aplica sobre Jan/26.', 'Planejar compras, estoque e caixa.');
  html += '<hr class="divider">';

  html += '<div class="row"><div class="col-60">';
  html += sectionHeader('Sazonalidade — 2025 (Completo) vs 2026 (Parcial)');
  html += '<div id="chart-sazonalidade" class="chart-container"></div>';
  html += tooltipBox('Sazonalidade 2025 vs 2026', 'Cinza = 2025. Losangos amarelos = 2026 real. Linha pontilhada = média 2025.', 'Compare o losango de 2026 com o ponto do MESMO mês de 2025.', '2025 mostra o padrão.');
  html += '</div><div class="col-40">';
  html += sectionHeader('Índice de Sazonalidade — 2025');
  html += '<div id="chart-indice" class="chart-container"></div>';
  html += tooltipBox('Índice de Sazonalidade', 'Cada barra = faturamento do mês ÷ média anual de 2025. 1.00 = exatamente na média.', 'Verde (>1.00) = mês forte. Vermelho (<1.00) = mês fraco.', 'Prever meses fortes e fracos de 2026.');
  html += '</div></div>';

  html += sectionHeader('Mix de Produtos — 2025 (Completo)');
  html += '<div id="chart-mix" class="chart-container"></div>';
  const sp = yoy.filter(r => r.SKUs_2025 > 0);
  if (sp.length > 0) {
    const p1 = sp[0].SKUs_2025, u1 = sp[sp.length - 1].SKUs_2025;
    html += tooltipBox('Evolução do Mix', 'SKUs vendidos por mês em 2025.', 'Linha descendo = menos variedade.', 'Menos produtos = menos motivos para o cliente.', `De ${Math.round(p1)} para ${Math.round(u1)} (${Math.round(u1 - p1)}).`);
    html += storyBox(`Mix encolheu de ${Math.round(p1)} para ${Math.round(u1)} SKUs em 2025 (${Math.round(u1 - p1)}).`);
  }

  html += sectionHeader('Tendência — 12 Meses Móveis');
  html += '<div id="chart-tendencia" class="chart-container"></div>';

  document.getElementById('content').innerHTML = html;

  // Seasonality chart
  const r26vals = yoy.filter(r => r.Receita_2026 > 0);
  const r26labels = r26vals.map(r => MESES_LABELS[r.Mes_Num - 1]);
  const traces = [
    { x: MESES_LABELS, y: yoy.map(r => r.Receita_2025), name: '2025 (completo)', mode: 'lines+markers+text', line: { color: '#AAA', width: 2 }, marker: { size: 8 }, text: yoy.map(r => fmtK(r.Receita_2025)), textposition: 'top center', textfont: { size: 9 } },
    { x: r26labels, y: r26vals.map(r => r.Receita_2026), name: '2026 (real)', mode: 'lines+markers+text', line: { color: COLORS.yellow, width: 3 }, marker: { size: 12, symbol: 'diamond' }, text: r26vals.map(r => fmtK(r.Receita_2026)), textposition: 'bottom center', textfont: { size: 10 } }
  ];
  Plotly.newPlot('chart-sazonalidade', traces, {
    height: 400, plot_bgcolor: 'white', margin: { l: 50, r: 20, t: 30, b: 40 },
    yaxis: { title: 'Faturamento (R$)' }, legend: { orientation: 'h', y: -0.1 },
    shapes: [{ type: 'line', x0: -0.5, x1: 11.5, y0: fmm25, y1: fmm25, line: { dash: 'dot', color: '#CCC' } }],
    annotations: [{ x: 10, y: fmm25, text: `Média 2025: ${fmtK(fmm25)}`, showarrow: false, font: { size: 10, color: '#CCC' } }]
  }, { responsive: true });

  // Seasonality index chart
  const idx = yoy.map(r => fmm25 > 0 ? r.Receita_2025 / fmm25 : 0);
  Plotly.newPlot('chart-indice', [{
    x: MESES_LABELS, y: idx, type: 'bar',
    marker: { color: idx.map(v => v > 1 ? COLORS.green : COLORS.red) },
    text: idx.map(v => v.toFixed(2)), textposition: 'outside', textfont: { size: 10 }
  }], {
    height: 400, plot_bgcolor: 'white', margin: { l: 50, r: 20, t: 30, b: 40 },
    yaxis: { title: 'Índice (1.00 = média)' },
    shapes: [{ type: 'line', x0: -0.5, x1: 11.5, y0: 1, y1: 1, line: { color: '#999', width: 2 } }]
  }, { responsive: true });

  // Mix chart
  if (sp.length > 0) {
    Plotly.newPlot('chart-mix', [{
      x: sp.map(r => r.Mes), y: sp.map(r => r.SKUs_2025), mode: 'lines+markers+text',
      line: { color: COLORS.blue, width: 2 }, marker: { size: 10 },
      text: sp.map(r => Math.round(r.SKUs_2025).toString()), textposition: 'top center'
    }], { height: 280, plot_bgcolor: 'white', margin: { l: 50, r: 20, t: 30, b: 40 }, yaxis: { title: 'Nº SKUs' } }, { responsive: true });
  }

  // Rolling 12 months
  const ra = yoy.map(r => r.Receita_2025);
  yoy.forEach(r => { if (r.Receita_2026 > 0) ra.push(r.Receita_2026); });
  if (ra.length >= 12) {
    const rol = [], lbl = [];
    for (let i = 11; i < ra.length; i++) {
      let sum = 0;
      for (let j = Math.max(0, i - 11); j <= i; j++) sum += ra[j];
      rol.push(sum);
      lbl.push(i < 12 ? MESES_LABELS[i] + '/25' : MESES_LABELS[i - 12] + '/26');
    }
    Plotly.newPlot('chart-tendencia', [{
      x: lbl, y: rol, mode: 'lines+markers', line: { color: COLORS.blue, width: 3 },
      fill: 'tozeroy', fillcolor: 'rgba(46,134,193,0.1)'
    }], { height: 280, plot_bgcolor: 'white', margin: { l: 70, r: 20, t: 30, b: 40 }, yaxis: { title: 'Fat. Acum. 12m (R$)' } }, { responsive: true });

    if (rol.length > 1) {
      const tp = safeDiv(rol[rol.length - 1] - rol[0], rol[0]) * 100;
      const el = document.getElementById('chart-tendencia');
      el.insertAdjacentHTML('afterend', tooltipBox('12 Meses Móveis', 'Soma dos últimos 12 meses em cada ponto. Elimina sazonalidade.', 'Subindo = negócio crescendo. Descendo = encolhendo.', 'Melhor indicador de tendência real.') + storyBox(`Faturamento 12m: R$ ${fmtInt(rol[rol.length - 1])}. Tendência ${tp > 0 ? 'subindo' : 'caindo'} (${tp >= 0 ? '+' : ''}${tp.toFixed(1)}%).`));
    }
  }
}

// ============================================================
// PAGE 6: VISAO FUTURISTA
// ============================================================
function pageVisao() {
  const yoy = DATA.yoy, vm = filterDataByPeriod(DATA.vendas_mensais, SELECTED_MES, SELECTED_ANO), produtos = DATA.produtos;
  const { nome: mesNome, num: mesNum, ano: mesAno } = getSelectedMes();
  const fat = sumField(vm, 'Vlr_Venda'), lb = sumField(vm, 'Vlr_Lucro'), mg = safeDiv(lb, fat) * 100;
  const fmm25 = safeDiv(sumField(yoy, 'Receita_2025'), 12);

  // Seasonal indices + adjustment factor
  const idxSaz = {};
  yoy.forEach(r => { if (fmm25 > 0 && r.Receita_2025 > 0) idxSaz[r.Mes_Num] = r.Receita_2025 / fmm25; });
  const md = yoy.filter(r => r.Receita_2026 > 0);
  let fa = 1.0;
  if (md.length > 0) {
    const r26 = sumField(md, 'Receita_2026'), r25eq = md.reduce((s, r) => s + (r.Receita_2025 || 0), 0);
    if (r25eq > 0) fa = r26 / r25eq;
  }

  // Projections
  const proj = [];
  for (let m = 1; m <= 12; m++) {
    const yr = yoy.find(r => r.Mes_Num === m) || {};
    const r25 = yr.Receita_2025 || 0, r26 = yr.Receita_2026 || 0;
    proj.push({ mes: MESES_NOMES[m], num: m, lbl: MESES_LABELS[m - 1], r25, r26, proj: r25 > 0 ? r25 * fa : 0, tipo: r26 > 0 ? 'Real' : 'Projeção' });
  }

  const pm = mesNum < 12 ? mesNum + 1 : 1, pmn = MESES_NOMES[pm];
  const ppj = proj.find(p => p.num === pm);
  const ppjVal = ppj ? ppj.proj : fat;
  const cp = ppjVal * 0.85, crVal = ppjVal, co = ppjVal * 1.15;
  const lp = cp * (mg / 100) - CUSTO_FIXO;
  const lr = crVal * (mg / 100) - CUSTO_FIXO;
  const lo = co * (mg / 100) - CUSTO_FIXO;

  let html = `<h2>🔮 Visão Futurista — Cenários e Projeções</h2><p class="page-subtitle">Baseado nos dados, o que esperar e como se preparar?</p><hr class="divider">`;

  html += sectionHeader('📊 Projeção de Faturamento — 2026 Completo');
  html += '<div id="chart-projecao" class="chart-container"></div>';
  html += tooltipBox('Projeção 2026', 'Amarelo sólido = real. Amarelo transparente = projeção sazonal. Cinza = 2025.', `Fator de ajuste: ${fa.toFixed(2)} (2026 está a ${((fa - 1) * 100) >= 0 ? '+' : ''}${((fa - 1) * 100).toFixed(1)}% de 2025).`, 'Antecipar faturamento para planejar compras e caixa.');
  html += '<hr class="divider">';

  // Scenarios
  html += sectionHeader(`🎯 Cenários para ${pmn}/26`);
  html += '<div class="kpi-grid kpi-grid-3">';
  html += kpiCard('😟 Pessimista (-15%)', `R$ ${fmtInt(cp)}`, `Lucro: R$ ${fmtInt(lp)}`, lp < 0 ? 'kpi-negative' : 'kpi-neutral');
  html += kpiCard('📊 Realista', `R$ ${fmtInt(crVal)}`, `Lucro: R$ ${fmtInt(lr)}`, 'kpi-positive');
  html += kpiCard('🚀 Otimista (+15%)', `R$ ${fmtInt(co)}`, `Lucro: R$ ${fmtInt(lo)}`, 'kpi-positive');
  html += '</div>';
  html += tooltipBox(`Cenários ${pmn}`, '3 cenários baseados na projeção sazonal: pessimista, realista, otimista.', 'Se o pessimista já dá lucro, o negócio está seguro.', 'Planejar caixa e definir metas realistas.');
  html += '<hr class="divider">';

  // Gauge
  html += sectionHeader(`🏎️ Velocímetro — ${mesNome}/26 vs Metas`);
  html += '<div id="chart-gauge" class="chart-container"></div>';
  const mm = mg > 0 ? safeDiv(CUSTO_FIXO, mg / 100) : 0, mi = mm * 1.5;
  html += tooltipBox('Velocímetro', `Vermelho = prejuízo (<R$ ${fmtInt(mm)}). Amarelo = acima do break-even. Verde = meta ideal.`, `Break-even: R$ ${fmtInt(mm)}. Meta ideal: R$ ${fmtInt(mi)}.`, 'Quanto mais para a direita (verde), mais saudável.');
  html += '<hr class="divider">';

  // Top 5 categories
  html += sectionHeader('📦 Top 5 Categorias — Performance e Tendência');
  const vmTop = [...vm].sort((a, b) => b.Vlr_Venda - a.Vlr_Venda).slice(0, 5);
  html += '<div class="kpi-grid kpi-grid-5">';
  vmTop.forEach(row => {
    const idx = idxSaz[pm] || 1.0;
    const emoji = idx > 1.1 ? '🔥' : (idx < 0.9 ? '❄️' : '➡️');
    html += `<div class="kpi-card"><div class="kpi-title">${row.Categoria}</div><div class="kpi-value" style="font-size:16px;">R$ ${fmtInt(row.Vlr_Venda)}</div><div class="kpi-subtitle">Margem: ${row.Markdown_Pct.toFixed(0)}% | ${emoji} ${pmn}: ${idx.toFixed(2)}</div></div>`;
  });
  html += '</div>';
  html += tooltipBox('Top 5 Categorias', 'As 5 maiores categorias + tendência sazonal do próximo mês.', '🔥 = mês forte (>1.10). ❄️ = fraco (<0.90). ➡️ = normal.', 'Reforçar estoque das 🔥 e promover as ❄️.');
  html += '<hr class="divider">';

  // Action plan
  html += sectionHeader(`📋 Direcionamento Estratégico — ${pmn}/26`);
  const est = produtos.filter(p => p.Classificacao && p.Classificacao.includes('Estrela'));
  const pmo = produtos.filter(p => p.Classificacao && p.Classificacao.includes('Peso Morto'));
  const cs = DATA.erosao.filter(r => r.Alerta && r.Alerta.includes('SUBIU'));
  const idxPm = idxSaz[pm] || 1.0;

  html += '<div class="row"><div class="col-50">';
  html += `<h3>✅ O que FAZER em ${pmn}</h3><ul class="action-list">`;
  if (cs.length > 0) html += `<li>🔴 <strong>Reajustar preços</strong> de ${cs.length} produtos com custo subindo</li>`;
  if (est.length > 0) {
    const tops = [...est].sort((a, b) => b.Lucro_Total - a.Lucro_Total).slice(0, 3).map(p => p.Produto);
    html += `<li>⭐ <strong>Garantir estoque</strong> dos top Estrelas: ${tops.join(', ')}</li>`;
  }
  if (idxPm > 1.05) html += `<li>📈 <strong>Reforçar compras</strong> — ${pmn} é forte (índice ${idxPm.toFixed(2)})</li>`;
  else if (idxPm < 0.95) html += `<li>📢 <strong>Planejar promoções</strong> — ${pmn} é fraco (índice ${idxPm.toFixed(2)})</li>`;
  html += `<li>🎯 <strong>Meta faturamento</strong>: R$ ${fmtInt(crVal)}</li>`;
  html += `<li>💰 <strong>Meta lucro líquido</strong>: R$ ${fmtInt(lr)}</li>`;
  html += '</ul></div><div class="col-50">';
  html += '<h3>⚠️ O que MONITORAR</h3><ul class="action-list">';

  const yoyM = yoy.filter(r => r.Receita_2026 > 0);
  const c25Ref = yoyM.length > 0 ? yoyM[yoyM.length - 1].Cupons_2025 : 1;
  const cupAtual = sumField(vm, 'Qtde_Documentos');
  const vcRef = safeDiv(cupAtual - c25Ref, c25Ref) * 100;
  html += `<li>👥 <strong>Fluxo de clientes</strong>: variou ${vcRef >= 0 ? '+' : ''}${Math.round(vcRef)}% vs ano anterior</li>`;
  html += `<li>📊 <strong>Margem real</strong>: manter acima de 15% (atual: ${mg.toFixed(1)}%)</li>`;
  html += `<li>🏷️ <strong>Erosão</strong>: ${cs.length} produtos precisam reajuste</li>`;
  if (pmo.length > 50) html += `<li>🗑️ <strong>Peso Morto</strong>: ${pmo.length} produtos a avaliar</li>`;
  html += '</ul></div></div>';
  html += tooltipBox('Plano de Ação', 'Gerado automaticamente com base nos dados e projeções.', 'Ações priorizadas por impacto: margem → estoque → sazonalidade.', 'Revise com os sócios no início de cada mês.');

  document.getElementById('content').innerHTML = html;

  // Projection chart
  const drReal = proj.filter(p => p.r26 > 0), dfProj = proj.filter(p => p.r26 === 0 && p.proj > 0);
  const projTraces = [
    { x: proj.map(p => p.lbl), y: proj.map(p => p.r25), name: '2025 (ref)', type: 'scatter', mode: 'lines+markers', line: { color: '#CCC', width: 2, dash: 'dot' }, marker: { size: 6 } },
  ];
  if (drReal.length > 0) projTraces.push({
    x: drReal.map(p => p.lbl), y: drReal.map(p => p.r26), name: '2026 (real)', type: 'bar',
    marker: { color: COLORS.yellow }, text: drReal.map(p => fmtK(p.r26)), textposition: 'outside'
  });
  if (dfProj.length > 0) projTraces.push({
    x: dfProj.map(p => p.lbl), y: dfProj.map(p => p.proj), name: '2026 (projeção)', type: 'bar',
    marker: { color: 'rgba(255,193,7,0.4)', line: { color: COLORS.yellow, width: 2 } },
    text: dfProj.map(p => fmtK(p.proj)), textposition: 'outside'
  });
  Plotly.newPlot('chart-projecao', projTraces, {
    height: 400, plot_bgcolor: 'white', barmode: 'overlay',
    margin: { l: 50, r: 20, t: 30, b: 40 }, yaxis: { title: 'Faturamento (R$)' },
    legend: { orientation: 'h', y: -0.1 }
  }, { responsive: true });

  // Gauge
  Plotly.newPlot('chart-gauge', [{
    type: 'indicator', mode: 'gauge+number+delta', value: fat,
    number: { prefix: 'R$ ', valueformat: ',.0f' },
    delta: { reference: mi, prefix: 'R$ ', valueformat: ',.0f' },
    title: { text: `Faturamento ${mesNome}/26` },
    gauge: {
      axis: { range: [0, mi * 1.5], tickformat: ',.0f', tickprefix: 'R$ ' },
      bar: { color: COLORS.yellow },
      steps: [
        { range: [0, mm], color: '#FADBD8' },
        { range: [mm, mi], color: '#F9E79F' },
        { range: [mi, mi * 1.5], color: '#D5F5E3' }
      ],
      threshold: { line: { color: COLORS.red, width: 4 }, thickness: 0.75, value: mm }
    }
  }], { height: 300, margin: { l: 20, r: 20, t: 60, b: 20 } }, { responsive: true });
}

// ============================================================
// TAB SWITCHING
// ============================================================
function switchTab(btn, tabId) {
  const parent = btn.closest('.tabs') || btn.parentElement;
  parent.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  let sibling = parent.nextElementSibling;
  while (sibling && sibling.classList.contains('tab-content')) {
    sibling.classList.remove('active');
    sibling = sibling.nextElementSibling;
  }
  document.getElementById(tabId).classList.add('active');
}

// ============================================================
// PAGE 7: IMPORTAÇÃO DE DADOS
// ============================================================
function pageImportacao() {
  const html = `
    <h1>📥 Importação de Dados</h1>
    <p style="color:#666;margin-bottom:20px;">Carregue dados em Excel para análise</p>
    <hr>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px;">
      <div>
        <h3>📋 Instruções</h3>
        <div class="import-instructions">
          <p><strong>Formatos Aceitos:</strong></p>
          <ul>
            <li><strong>Vendas:</strong> Data, Categoria, Produto, Quantidade, Valor_Unitario, Vlr_Venda, Custo, Vlr_Lucro, Qtde_Documentos</li>
            <li><strong>Produtos:</strong> Produto, Categoria, Custo_Medio, Preco, Estoque</li>
            <li><strong>Simples:</strong> Data, Categoria, Produto, Faturamento</li>
          </ul>
          <p><strong>Dicas:</strong></p>
          <ol>
            <li>Use colunas com nomes exatos</li>
            <li>Máximo 5MB por arquivo</li>
            <li>Formato: .xlsx ou .xls</li>
          </ol>
        </div>
      </div>

      <div>
        <h3>🔒 Segurança</h3>
        <div class="import-security">
          <p><strong>Status:</strong> ✅ Administrador</p>
          <p><strong>Últimos uploads:</strong></p>
          <ul>
            <li id="last-upload" style="color:#999;">Nenhum ainda nesta sessão</li>
          </ul>
        </div>
      </div>
    </div>

    <hr>

    <div class="import-upload-area" id="upload-area">
      <input type="file" id="excel-file" accept=".xlsx,.xls" style="display:none;" onchange="handleFileSelect(event)">
      <div style="text-align:center;padding:30px;background:#f9f9f9;border:2px dashed #ccc;border-radius:8px;cursor:pointer;" onclick="document.getElementById('excel-file').click();">
        <p style="font-size:24px;">📂</p>
        <p style="font-size:16px;font-weight:bold;">Clique para selecionar ou arraste um arquivo aqui</p>
        <p style="color:#999;">Formatos aceitos: .xlsx, .xls (Máximo 5MB)</p>
      </div>
    </div>

    <div id="preview-container" style="display:none;margin-top:30px;">
      <h3>👁️ Preview dos Dados</h3>
      <div id="format-info" style="padding:10px;background:#e3f2fd;border-radius:4px;margin-bottom:10px;"></div>
      <div id="validation-info" style="padding:10px;margin-bottom:10px;"></div>
      <div id="data-table" style="max-height:400px;overflow-y:auto;margin-bottom:10px;"></div>
      <button class="btn-import" onclick="processAndSaveData()">✅ Processar e Salvar Dados</button>
      <button class="btn-cancel" onclick="resetUpload()">❌ Cancelar</button>
    </div>

    <div id="result-container" style="display:none;margin-top:30px;">
      <div id="result-message"></div>
      <button class="btn-import" onclick="resetUpload()">🔄 Importar Novo Arquivo</button>
    </div>
  `;

  document.getElementById('content').innerHTML = html;

  // Drag and drop
  const uploadArea = document.getElementById('upload-area');
  uploadArea.addEventListener('dragover', e => {
    e.preventDefault();
    uploadArea.style.background = '#f0f0f0';
  });
  uploadArea.addEventListener('dragleave', e => {
    e.preventDefault();
    uploadArea.style.background = 'transparent';
  });
  uploadArea.addEventListener('drop', e => {
    e.preventDefault();
    uploadArea.style.background = 'transparent';
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      document.getElementById('excel-file').files = files;
      handleFileSelect({target: {files: files}});
    }
  });
}

let uploadedData = null;
let detectedFormat = null;

function handleFileSelect(event) {
  const file = event.target.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const data = e.target.result;
      const workbook = XLSX.read(data, {type: 'array'});
      const sheetName = workbook.SheetNames[0];
      const worksheet = workbook.Sheets[sheetName];
      const json = XLSX.utils.sheet_to_json(worksheet);

      if (json.length === 0) {
        showError('❌ Arquivo vazio!');
        return;
      }

      uploadedData = json;
      detectedFormat = detectExcelFormat(json[0]);

      // Validar
      const {isValid, message} = validateExcelData(json, detectedFormat);

      // Mostrar preview
      document.getElementById('upload-area').style.display = 'none';
      document.getElementById('preview-container').style.display = 'block';

      document.getElementById('format-info').innerHTML = `📊 <strong>Formato detectado:</strong> ${detectedFormat.toUpperCase()}`;
      document.getElementById('validation-info').innerHTML = `<div style="padding:8px;border-radius:4px;background:${isValid ? '#c8e6c9' : '#ffcdd2'};color:${isValid ? '#2e7d32' : '#c62828'};">${message}</div>`;

      // Tabela
      const table = createDataTable(json.slice(0, 10));
      document.getElementById('data-table').innerHTML = table;
      document.getElementById('data-table').innerHTML += `<p style="color:#999;margin-top:10px;"><strong>Total de linhas:</strong> ${json.length}</p>`;

      if (!isValid) {
        document.querySelector('.btn-import').disabled = true;
        document.querySelector('.btn-import').style.opacity = '0.5';
      }
    } catch (err) {
      showError(`❌ Erro ao ler arquivo: ${err.message}`);
    }
  };
  reader.readAsArrayBuffer(file);
}

function detectExcelFormat(firstRow) {
  const keys = Object.keys(firstRow).map(k => k.toUpperCase().trim());

  // Vendas
  if (keys.includes('VLR_VENDA') && keys.includes('CUSTO') && keys.includes('QTDE_DOCUMENTOS')) {
    return 'vendas';
  }

  // Produtos
  if (keys.includes('CUSTO_MEDIO') && keys.includes('PRECO') && keys.includes('ESTOQUE')) {
    return 'produtos';
  }

  // Simples
  if (keys.includes('DATA') && keys.includes('CATEGORIA') && keys.includes('PRODUTO') && keys.includes('FATURAMENTO')) {
    return 'simples';
  }

  return 'desconhecido';
}

function validateExcelData(data, format) {
  const expectedColumns = {
    'vendas': ['Data', 'Categoria', 'Produto', 'Quantidade', 'Valor_Unitario', 'Vlr_Venda', 'Custo', 'Vlr_Lucro', 'Qtde_Documentos'],
    'produtos': ['Produto', 'Categoria', 'Custo_Medio', 'Preco', 'Estoque'],
    'simples': ['Data', 'Categoria', 'Produto', 'Faturamento']
  };

  const expected = expectedColumns[format] || [];
  const actual = Object.keys(data[0]).map(k => k.trim());
  const actualUpper = actual.map(k => k.toUpperCase());
  const expectedUpper = expected.map(k => k.toUpperCase());

  const missing = expectedUpper.filter(col => !actualUpper.includes(col));

  if (missing.length > 0) {
    return {
      isValid: false,
      message: `❌ Colunas faltando: ${missing.join(', ')}`
    };
  }

  return {
    isValid: true,
    message: '✅ Dados validados com sucesso'
  };
}

function createDataTable(data) {
  if (!data || data.length === 0) return '<p>Sem dados</p>';

  const keys = Object.keys(data[0]);
  let html = '<div class="data-table-container"><table class="data-table"><thead><tr>';

  keys.forEach(k => html += `<th>${k}</th>`);
  html += '</tr></thead><tbody>';

  data.forEach(row => {
    html += '<tr>';
    keys.forEach(k => {
      let val = row[k];
      if (typeof val === 'number') val = val.toLocaleString('pt-BR', {minimumFractionDigits: 2, maximumFractionDigits: 2});
      html += `<td>${val || '-'}</td>`;
    });
    html += '</tr>';
  });

  html += '</tbody></table></div>';
  return html;
}

function processAndSaveData() {
  const btn = document.querySelector('.btn-import');
  btn.disabled = true;
  btn.textContent = '⏳ Processando...';

  setTimeout(() => {
    try {
      let processedData = uploadedData;

      // Normalizar nomes de colunas
      processedData = processedData.map(row => {
        const newRow = {};
        Object.keys(row).forEach(key => {
          newRow[key.toUpperCase().trim()] = row[key];
        });
        return newRow;
      });

      // Salvar no localStorage (simulando o backend)
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
      const key = `data_upload_${detectedFormat}_${timestamp}`;
      localStorage.setItem(key, JSON.stringify(processedData));

      // Atualizar último upload
      document.getElementById('last-upload').textContent = `✅ ${detectedFormat.toUpperCase()} - ${new Date().toLocaleString('pt-BR')}`;

      // Mostrar resultado
      document.getElementById('preview-container').style.display = 'none';
      document.getElementById('result-container').style.display = 'block';
      document.getElementById('result-message').innerHTML = `
        <div style="padding:15px;background:#c8e6c9;border-radius:4px;border-left:4px solid #2e7d32;">
          <h3 style="color:#2e7d32;margin:0 0 10px 0;">✅ Dados salvos com sucesso!</h3>
          <div style="color:#1b5e20;">
            <p><strong>Tipo:</strong> ${detectedFormat.toUpperCase()}</p>
            <p><strong>Linhas:</strong> ${uploadedData.length}</p>
            <p><strong>Armazenado em:</strong> localStorage (${key})</p>
          </div>
        </div>
      `;
    } catch (err) {
      showError(`❌ Erro ao processar: ${err.message}`);
    }

    btn.disabled = false;
    btn.textContent = '✅ Processar e Salvar Dados';
  }, 500);
}

function resetUpload() {
  document.getElementById('excel-file').value = '';
  document.getElementById('upload-area').style.display = 'block';
  document.getElementById('preview-container').style.display = 'none';
  document.getElementById('result-container').style.display = 'none';
  uploadedData = null;
  detectedFormat = null;
}

function showError(message) {
  document.getElementById('preview-container').style.display = 'none';
  document.getElementById('result-container').style.display = 'block';
  document.getElementById('result-message').innerHTML = `
    <div style="padding:15px;background:#ffcdd2;border-radius:4px;border-left:4px solid #c62828;color:#b71c1c;">
      ${message}
    </div>
  `;
}

// ============================================================
// NAVIGATION
// ============================================================
function navigate(page) {
  currentPage = page;
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  document.querySelector(`[data-page="${page}"]`).classList.add('active');

  switch (page) {
    case 'resumo': pageResumo(); break;
    case 'precos': pagePrecos(); break;
    case 'mapa': pageMapa(); break;
    case 'diagnostico': pageDiagnostico(); break;
    case 'sazonalidade': pageSazonalidade(); break;
    case 'visao': pageVisao(); break;
    case 'importacao': pageImportacao(); break;
  }
  window.scrollTo(0, 0);
}

// ============================================================
// SIMULATOR
// ============================================================
function updateCustoFixo() {
  const input = document.getElementById('custo-fixo-input');
  const val = parseFloat(input.value.replace(/[^\d.,]/g, '').replace(',', '.'));
  if (!isNaN(val) && val > 0) {
    CUSTO_FIXO = val;
    const badge = document.getElementById('sim-badge');
    if (val !== CUSTO_FIXO_DEFAULT) {
      badge.textContent = `⚡ Simulando com R$ ${fmtInt(val)}`;
      badge.style.display = 'block';
    } else { badge.style.display = 'none'; }
    document.getElementById('info-custo').textContent = `R$ ${fmt(val)}`;
    navigate(currentPage);
  }
}

// ============================================================
// INIT
// ============================================================
async function init() {
  const debugDiv = document.getElementById('loading-debug');
  try {
    if (debugDiv) debugDiv.textContent = 'Iniciando carregamento de dados...';
    await loadData();
    if (debugDiv) debugDiv.textContent = '✅ Dados carregados com sucesso!';
    document.getElementById('loading').style.display = 'none';
    document.getElementById('app').style.display = 'flex';
    navigate('resumo');
  } catch (e) {
    console.error('Erro ao carregar aplicação:', e);
    const errorHTML = `
      <div style="color:red;text-align:center;padding:20px;">
        <h3>❌ Erro ao carregar dados</h3>
        <p style="margin:15px 0; font-size:14px;"><strong>${e.message}</strong></p>
        <div style="text-align:left;background:#f5f5f5;padding:10px;border-radius:4px;margin:10px 0;font-size:11px;color:#666;">
          <p><strong>Dicas:</strong></p>
          <ul style="margin:5px 0;padding-left:20px;">
            <li>Verifique se a pasta <code>/data/</code> existe</li>
            <li>Confirme que os arquivos JSON estão presentes</li>
            <li>Tente recarregar a página (F5)</li>
            <li>Abra o console (F12) para mais detalhes</li>
          </ul>
        </div>
        <button onclick="location.reload()" style="padding:8px 16px;background:#2196F3;color:white;border:none;border-radius:4px;cursor:pointer;">🔄 Recarregar Página</button>
      </div>
    `;
    document.getElementById('loading').innerHTML = errorHTML;
  }
}

document.addEventListener('DOMContentLoaded', init);

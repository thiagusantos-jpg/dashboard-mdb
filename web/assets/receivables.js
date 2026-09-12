/* Recebíveis Stone: importa o arquivo de conciliação (XML layout 2.4),
 * mostra a agenda de recebimentos esperados e a taxa efetiva vs. contratada.
 * Loaded before app.js and uses its shared api(), esc(), money(), APP globals
 * plus finance.js's dateBR()/financeError() and loans.js's addMonthsISO(). */
'use strict';

async function renderRecebiveis() {
  const title = '🏷️ Recebíveis';
  const subtitle = 'Agenda de recebimentos e taxa efetiva de adquirência a partir do arquivo de conciliação Stone.';
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <div class="skeleton-block" aria-label="Carregando recebíveis"></div>`;
  const start = new Date().toISOString().slice(0, 10);
  const end = addMonthsISO(start, 2);
  let cashAccounts, settlements, feeReport;
  try {
    [cashAccounts, settlements, feeReport] = await Promise.all([
      api(`/api/companies/${APP.company}/finance/cash-accounts`),
      api(`/api/companies/${APP.company}/finance/receivables/expected-settlements?start=${start}&end=${end}`),
      api(`/api/companies/${APP.company}/finance/receivables/effective-fee-report?start=${start}&end=${end}`),
    ]);
  } catch (e) {
    return financeError(title, subtitle, e);
  }

  const accountOptions = cashAccounts.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');
  const settlementRows = settlements.map((s) => `
    <tr>
      <td>${dateBR(s.settlement_date)}</td>
      <td>${s.count}</td>
      <td>${money(s.gross_cents)}</td>
      <td>${money(s.fee_cents)}</td>
      <td>${money(s.net_cents)}</td>
    </tr>`).join('');

  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <hr class="divider">
    <div class="kpi-grid kpi-grid-3">
      ${kpi('Taxa efetiva', feeReport.effective_rate_pct == null ? '—' : feeReport.effective_rate_pct.toFixed(2).replace('.', ',') + '%')}
      ${kpi('Taxa contratada', feeReport.contracted_rate_pct == null ? 'Não configurada' : feeReport.contracted_rate_pct.toFixed(2).replace('.', ',') + '%')}
      ${kpi('Variação', feeReport.variance_pct == null ? '—' : (feeReport.variance_pct >= 0 ? '+' : '') + feeReport.variance_pct.toFixed(2).replace('.', ',') + 'p.p.',
        null, feeReport.variance_pct != null && feeReport.variance_pct > 0 ? 'kpi-negative' : null)}
    </div>
    <div class="settings-block">
      <h2>Importar arquivo de conciliação</h2>
      <p>Envie o XML (layout 2.4) baixado do portal Stone.</p>
      <form id="receivables-import-form" class="inline-form">
        <select id="receivables-account" class="login-input" required>${accountOptions || '<option value="">Cadastre uma conta de caixa primeiro</option>'}</select>
        <input type="file" id="receivables-file" accept=".xml" required>
        <button type="submit" class="btn-primary">Importar</button>
        <span id="receivables-import-status" class="sim-status" role="status" aria-live="polite"></span>
      </form>
    </div>
    <div class="section-header">Agenda de recebimentos (próximos 60 dias)</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Data</th><th>Transações</th><th>Bruto</th><th>Taxa</th><th>Líquido</th></tr></thead>
      <tbody>${settlementRows || '<tr><td colspan="5">Nenhum recebível importado ainda.</td></tr>'}</tbody>
    </table></div>`;

  document.getElementById('receivables-import-form').addEventListener('submit', onImportReceivables);
}

async function onImportReceivables(event) {
  event.preventDefault();
  const status = document.getElementById('receivables-import-status');
  const accountId = document.getElementById('receivables-account').value;
  const fileInput = document.getElementById('receivables-file');
  if (!accountId || !fileInput.files.length) return;
  status.textContent = 'Importando…';
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  try {
    const res = await fetch(`/api/companies/${APP.company}/finance/cash-accounts/${accountId}/receivables-import`, {
      method: 'POST', credentials: 'same-origin',
      headers: {'x-csrf-token': APP.csrf || ''},
      body: formData,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Não foi possível importar o arquivo.');
    }
    const result = await res.json();
    status.textContent = `Importado: ${result.imported} nova(s), ${result.duplicates} já existente(s).`;
    renderRecebiveis();
  } catch (e) {
    status.textContent = 'Erro: ' + e.message;
  }
}

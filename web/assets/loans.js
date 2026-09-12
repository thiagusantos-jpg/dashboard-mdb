/* Empréstimos e financiamentos. Loaded before app.js and uses its shared
 * api(), esc(), money(), APP globals plus finance.js's dateBR()/financeError(). */
'use strict';

function addMonthsISO(iso, months) {
  const [y, m, d] = iso.split('-').map(Number);
  const total = y * 12 + (m - 1) + months;
  const ny = Math.floor(total / 12), nm = (total % 12) + 1;
  const lastDay = new Date(ny, nm, 0).getDate();
  return `${ny}-${String(nm).padStart(2, '0')}-${String(Math.min(d, lastDay)).padStart(2, '0')}`;
}

// Splits the principal into `count` equal cent amounts, folding the rounding
// remainder into the last installment so the parts always sum back to the total.
function buildEqualInstallments(principalCents, interestPerInstallmentCents, count, firstDueDate) {
  const base = Math.floor(principalCents / count);
  const remainder = principalCents - base * count;
  const installments = [];
  let due = firstDueDate;
  for (let n = 1; n <= count; n++) {
    installments.push({
      number: n, due_date: due,
      principal_cents: base + (n === count ? remainder : 0),
      interest_cents: interestPerInstallmentCents,
    });
    due = addMonthsISO(due, 1);
  }
  return installments;
}

async function renderEmprestimos(token) {
  token = token || beginPage();
  const title = '🏦 Empréstimos';
  const subtitle = 'Contratos, parcelas e posição de dívida por credor.';
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <div class="skeleton-block" aria-label="Carregando empréstimos"></div>`;
  let positions;
  try {
    positions = await api(`/api/companies/${APP.company}/finance/loans`);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;
    return financeError(title, subtitle, e, () => renderEmprestimos());
  }
  if (!APP.pageState.isCurrent(token)) return;
  const cards = positions.map(loanCard).join('') || '<div class="story-box">Nenhum empréstimo cadastrado.</div>';
  document.getElementById('content').innerHTML = `
    <div class="page-title">${title}</div>
    <div class="page-subtitle">${subtitle}</div>
    <hr class="divider">
    ${cards}
    <div class="settings-block">
      <h2>Novo empréstimo</h2>
      <form id="loan-create-form" class="settings-form">
        <div class="form-grid">
          <div><label class="field-label" for="loan-lender">Credor</label>
            <input id="loan-lender" class="login-input" maxlength="180" required></div>
          <div><label class="field-label" for="loan-purpose">Finalidade</label>
            <input id="loan-purpose" class="login-input" maxlength="240"></div>
          <div><label class="field-label" for="loan-principal">Principal (R$)</label>
            <input id="loan-principal" class="login-input" type="number" min="0.01" step="0.01" required></div>
          <div><label class="field-label" for="loan-net">Valor líquido recebido (R$)</label>
            <input id="loan-net" class="login-input" type="number" min="0.01" step="0.01" required></div>
          <div><label class="field-label" for="loan-count">Nº de parcelas</label>
            <input id="loan-count" class="login-input" type="number" min="1" step="1" value="12" required></div>
          <div><label class="field-label" for="loan-interest">Juros por parcela (R$)</label>
            <input id="loan-interest" class="login-input" type="number" min="0" step="0.01" value="0" required></div>
          <div><label class="field-label" for="loan-start">Data de início</label>
            <input id="loan-start" class="login-input" type="date" required></div>
          <div><label class="field-label" for="loan-first-due">1ª parcela vence em</label>
            <input id="loan-first-due" class="login-input" type="date" required></div>
        </div>
        <div class="settings-actions">
          <button class="btn-primary" type="submit">Cadastrar empréstimo</button>
          <span id="loan-create-status" class="sim-status" role="status" aria-live="polite"></span>
        </div>
      </form>
    </div>`;
  watchForm(document.getElementById('loan-create-form')).addEventListener('submit', onCreateLoan);
  document.querySelectorAll('[data-pay-form]').forEach((form) => form.addEventListener('submit', onPayInstallment));
}

function loanCard(position) {
  const loan = position.loan;
  const today = new Date().toISOString().slice(0, 10);
  const rows = position.installments.map((i) => `
    <tr>
      <td>${i.number}</td>
      <td>${dateBR(i.due_date)}</td>
      <td>${money(i.principal_cents)}</td>
      <td>${money(i.interest_cents)}</td>
      <td>${i.status === 'paid' ? 'Paga em ' + dateBR(i.paid_at) : 'Em aberto'}</td>
      <td>${i.status === 'open'
        ? `<form class="inline-form" data-pay-form="${esc(i.id)}"
               data-principal="${i.principal_cents}" data-interest="${i.interest_cents}">
             <input type="date" class="login-input" value="${today}" required>
             <button type="submit" class="btn-secondary">Pagar</button>
           </form>`
        : ''}</td>
    </tr>`).join('');
  return `
    <div class="settings-block">
      <div class="settings-heading">
        <div><h2>${esc(loan.lender)}</h2><p>${esc(loan.purpose || 'Sem finalidade registrada')}</p></div>
        <span class="periodo-badge">Principal em aberto: ${money(position.principal_cents)}</span>
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>#</th><th>Vencimento</th><th>Principal</th><th>Juros</th><th>Status</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
    </div>`;
}

async function onCreateLoan(event) {
  event.preventDefault();
  const status = document.getElementById('loan-create-status');
  status.textContent = 'Salvando…';
  const principalCents = Math.round(parseFloat(document.getElementById('loan-principal').value.replace(',', '.')) * 100);
  const netCents = Math.round(parseFloat(document.getElementById('loan-net').value.replace(',', '.')) * 100);
  const interestCents = Math.round(parseFloat(document.getElementById('loan-interest').value.replace(',', '.')) * 100);
  const count = parseInt(document.getElementById('loan-count').value, 10);
  const firstDue = document.getElementById('loan-first-due').value;
  try {
    await api(`/api/companies/${APP.company}/finance/loans`, {
      method: 'POST',
      body: JSON.stringify({
        lender: document.getElementById('loan-lender').value.trim(),
        purpose: document.getElementById('loan-purpose').value.trim(),
        principal_cents: principalCents,
        net_disbursement_cents: netCents,
        start_date: document.getElementById('loan-start').value,
        installments: buildEqualInstallments(principalCents, interestCents, count, firstDue),
      }),
    });
    clearDirty();
    renderEmprestimos();
  } catch (e) {
    status.textContent = 'Erro: ' + e.message;
  }
}

async function onPayInstallment(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const installmentId = form.dataset.payForm;
  const principalCents = parseInt(form.dataset.principal, 10);
  const interestCents = parseInt(form.dataset.interest, 10);
  const paidAt = form.querySelector('input[type="date"]').value;
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    await api(`/api/companies/${APP.company}/finance/loans/installments/${installmentId}/payments`, {
      method: 'POST',
      body: JSON.stringify({principal_cents: principalCents, interest_cents: interestCents, paid_at: paidAt}),
    });
    renderEmprestimos();
  } catch (e) {
    alert('Erro ao registrar pagamento: ' + e.message);
    button.disabled = false;
  }
}

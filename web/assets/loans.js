/* Empréstimos e financiamentos. Loaded before app.js and uses its shared
 * api(), esc(), money(), APP globals plus finance.js's dateBR()/financeError()
 * and finance-forms.js's drawers (openLoanForm, openRenegotiationForm,
 * openPaymentForm) when a page opens. Installments are paid through the
 * unified obligations endpoint, never the quarantined legacy route. */
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

const LOAN_STATUS_LABELS = {active: 'Ativo', settled: 'Quitado', cancelled: 'Cancelado'};

function installmentObligation(loan, installment) {
  const paidPrincipal = Number(installment.paid_principal_cents || 0);
  const paidInterest = Number(installment.paid_interest_cents || 0);
  const total = installment.principal_cents + installment.interest_cents;
  return {
    obligation: {
      kind: 'loan_installment', id: String(installment.id), version: installment.version,
      description: `Parcela ${installment.number} — ${loan.lender}`, due_date: installment.due_date,
      total_cents: total, paid_cents: paidPrincipal + paidInterest, open_cents: Math.max(0, total - paidPrincipal - paidInterest),
    },
    split: {
      principal_cents: Math.max(0, installment.principal_cents - paidPrincipal),
      interest_cents: Math.max(0, installment.interest_cents - paidInterest),
    },
  };
}

function installmentPayable(loan, installment) {
  return loan.status === 'active'
    && String(installment.schedule_id) === String(loan.active_schedule_id)
    && ['open', 'partially_paid'].includes(installment.status);
}

async function renderEmprestimos(token) {
  token = token || beginPage();
  const title = `${icon('store', {class: 'title-icon'})}Empréstimos`;
  const subtitle = 'Contratos, parcelas e posição de dívida por credor.';
  financeLoading(title, subtitle, 'Carregando empréstimos');
  let positions;
  try {
    positions = await api(`/api/companies/${APP.company}/finance/loans`);
  } catch (e) {
    if (!APP.pageState.isCurrent(token)) return;
    return financeError(title, subtitle, e, () => renderEmprestimos());
  }
  if (!APP.pageState.isCurrent(token)) return;
  const totalOpen = positions.reduce((sum, p) => sum + (p.loan.status === 'active' ? p.principal_cents : 0), 0);
  const cards = positions.map(loanCard).join('')
    || `<div class="empty-state">Nenhum empréstimo cadastrado.
         <div><button type="button" class="btn-primary" data-loan-new>Cadastrar empréstimo</button></div></div>`;
  document.getElementById('content').innerHTML = `
    <h1 class="page-title">${title}</h1>
    <div class="page-subtitle">${subtitle}</div>
    <div class="page-toolbar">
      <div>${kpi('Principal em aberto', money(totalOpen))}</div>
      <button type="button" class="btn-primary btn-wide" data-loan-new>Novo empréstimo</button>
    </div>
    ${cards}`;

  const refresh = () => refreshKeepingScroll(() => renderEmprestimos());
  const content = document.getElementById('content');
  const byId = Object.fromEntries(positions.map((p) => [String(p.loan.id), p]));
  content.querySelectorAll('[data-loan-new]').forEach((button) =>
    button.addEventListener('click', () => openLoanForm(null, {trigger: button, onSaved: refresh})));
  content.querySelectorAll('[data-loan-edit]').forEach((button) =>
    button.addEventListener('click', () => openLoanForm(byId[button.dataset.loanEdit], {trigger: button, onSaved: refresh})));
  content.querySelectorAll('[data-loan-renegotiate]').forEach((button) =>
    button.addEventListener('click', () => openRenegotiationForm(byId[button.dataset.loanRenegotiate], {trigger: button, onSaved: refresh})));
  content.querySelectorAll('[data-installment-pay]').forEach((button) => button.addEventListener('click', () => {
    const position = byId[button.dataset.loanId];
    const installment = position.installments.find((i) => String(i.id) === button.dataset.installmentPay);
    const {obligation, split} = installmentObligation(position.loan, installment);
    openPaymentForm(obligation, {trigger: button, split, onSaved: refresh});
  }));
}

function installmentStatus(installment) {
  if (installment.status === 'paid') return `<span class="badge-success">Paga${installment.paid_at ? ` em ${dateBR(installment.paid_at)}` : ''}</span>`;
  if (installment.status === 'partially_paid') return '<span class="badge-warning">Parcialmente paga</span>';
  const overdue = installment.due_date < todayISO();
  return overdue ? '<span class="badge-error">Vencida</span>' : '<span class="badge-muted">Em aberto</span>';
}

function loanCard(position) {
  const loan = position.loan;
  const payableCount = position.installments.filter((i) => installmentPayable(loan, i)).length;
  const rows = position.installments.map((i) => {
    const paid = Number(i.paid_principal_cents || 0) + Number(i.paid_interest_cents || 0);
    return `
    <tr>
      <td>${i.number}</td>
      <td>${dateBR(i.due_date)}</td>
      <td class="num">${money(i.principal_cents)}</td>
      <td class="num">${money(i.interest_cents)}</td>
      <td class="num">${paid ? money(paid) : '—'}</td>
      <td>${installmentStatus(i)}</td>
      <td>${installmentPayable(loan, i)
        ? `<button type="button" class="btn-secondary" data-installment-pay="${esc(i.id)}" data-loan-id="${esc(loan.id)}"
             aria-label="Pagar parcela ${i.number} — ${esc(loan.lender)}">Pagar</button>`
        : ''}</td>
    </tr>`;
  }).join('');
  return `
    <section class="settings-block" aria-labelledby="loan-${esc(loan.id)}-title">
      <div class="settings-heading">
        <div>
          <h2 id="loan-${esc(loan.id)}-title">${esc(loan.lender)}</h2>
          <p>${esc(loan.purpose || 'Sem finalidade registrada')} · ${esc(LOAN_STATUS_LABELS[loan.status] || loan.status)}</p>
        </div>
        <span class="periodo-badge">Principal em aberto: ${money(position.principal_cents)}</span>
      </div>
      <div class="row-actions">
        <button type="button" class="btn-secondary" data-loan-edit="${esc(loan.id)}">Editar credor e finalidade</button>
        ${loan.status === 'active' && payableCount ? `<button type="button" class="btn-secondary" data-loan-renegotiate="${esc(loan.id)}">Renegociar parcelas futuras</button>` : ''}
      </div>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th>#</th><th>Vencimento</th><th class="num">Principal</th><th class="num">Juros</th><th class="num">Pago</th><th>Status</th><th>Ação</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
    </section>`;
}

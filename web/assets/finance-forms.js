/* Shared financial forms (task C2): one drawer, one submit controller and one
 * request builder per operation, used by Custos e Despesas, Contas a Pagar and
 * Empréstimos. Builders are pure (values in, {path, method, body} or
 * {errors} out) so the money rules are unit-tested without a DOM; the drawer
 * and page wiring only read inputs and call them.
 *
 * Rules kept here on purpose:
 * - money is typed in pt-BR and converted to integer cents by string parsing,
 *   never by accumulating floats;
 * - ids stay strings end to end (they pass 2^53);
 * - nothing is written before submit, a submit in flight ignores repeat clicks,
 *   and a retry after a network failure reuses the same Idempotency-Key.
 * Loaded before finance.js/loans.js; uses app.js's api(), esc(), money(), APP
 * and loans.js's buildEqualInstallments() only when a form opens. `var` keeps
 * the state reachable from the vm-based unit tests. */
'use strict';

var FORM_STATE = {drawerSeq: 0, fieldSeq: 0};

var SERVER_FIELD_NAMES = {
  amount_cents: 'amount', total_cents: 'amount', principal_cents: 'principal', interest_cents: 'interest',
  cash_account_id: 'cash_account_id', existing_cash_event_id: 'existing_cash_event_id', paid_at: 'paid_at',
  reversed_at: 'reversed_at', reason: 'reason', description: 'description', account_id: 'account_id',
  counterparty_id: 'counterparty_id', competence: 'competence', due_date: 'due_date', due_day: 'due_day',
  start_competence: 'start_competence', end_competence: 'end_competence', count: 'count', first_due: 'first_due',
  notes: 'notes', lender: 'lender', purpose: 'purpose', installments: 'count', net_disbursement_cents: 'net',
  start_date: 'start_date',
};

var ENTRY_FIELD_ORDER = ['account_id', 'counterparty_id', 'description', 'amount_cents', 'competence', 'due_date', 'notes'];
var ENTRY_FORM_FIELD = {amount_cents: 'amount'};
var OPEN_EDITABLE = ENTRY_FIELD_ORDER;
var PARTIAL_EDITABLE = ['description', 'due_date', 'notes'];

var DRAWER_CLOSE_ICON = '<svg class="icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg>';

/* ---------------------------------------------------------------- money and dates */

function parseMoneyToCents(text) {
  let s = String(text == null ? '' : text).replace(/R\$|\s/g, '');
  if (!s) return null;
  if (s.includes(',')) {
    if (!/^(\d{1,3}(\.\d{3})+|\d+),\d{0,2}$/.test(s)) return null;
    s = s.replace(/\./g, '').replace(',', '.');
  } else if (/^\d{1,3}(\.\d{3})+$/.test(s)) {
    s = s.replace(/\./g, '');
  } else if (!/^\d+(\.\d{1,2})?$/.test(s)) {
    return null;
  }
  const parts = s.split('.');
  const cents = Number(parts[0]) * 100 + Number(((parts[1] || '') + '00').slice(0, 2));
  return Number.isSafeInteger(cents) ? cents : null;
}

function centsToMoneyInput(cents) {
  if (cents == null) return '';
  const negative = cents < 0;
  const abs = Math.abs(Math.trunc(cents));
  const whole = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `${negative ? '-' : ''}${whole},${String(abs % 100).padStart(2, '0')}`;
}

// Civil date in the store's time zone, not the browser's UTC offset.
function todayISO() {
  try {
    return new Intl.DateTimeFormat('en-CA', {timeZone: 'America/Sao_Paulo'}).format(new Date());
  } catch (e) {
    return new Date().toISOString().slice(0, 10);
  }
}

function newIdempotencyKey() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function financeBasePath() { return `/api/companies/${APP.company}/finance`; }

const isBlank = (v) => v == null || String(v).trim() === '';
const optionalId = (v) => (isBlank(v) ? null : String(v));

/* ---------------------------------------------------------------- submit controller */

function mapServerFields(fields) {
  return (fields || []).map((f) => SERVER_FIELD_NAMES[f] || f);
}

/* One per open form. submit() returns {ok, result|errors|error}; a repeat call
 * while a request is in flight is ignored. The Idempotency-Key survives a
 * network failure or timeout (the server may have stored the first attempt)
 * and is renewed after an answer: success, or a rejection that stored nothing. */
function createFormController(options) {
  let submitting = false;
  let key = newIdempotencyKey();
  return {
    async submit(values, ui) {
      if (submitting) return {ok: false, skipped: true};
      const request = options.buildRequest(values);
      if (request.errors) {
        const fields = Object.keys(request.errors);
        ui.showError(request.errors[fields[0]], fields);
        return {ok: false, errors: request.errors};
      }
      submitting = true;
      ui.setBusy(true);
      if (ui.clearError) ui.clearError();
      try {
        const headers = {'Idempotency-Key': key};
        const result = await api(request.path, {method: request.method || 'POST', body: JSON.stringify(request.body), headers});
        key = newIdempotencyKey();
        if (options.onSuccess) options.onSuccess(result, values);
        return {ok: true, result};
      } catch (e) {
        if (e && e.status) key = newIdempotencyKey();
        const message = e && e.status ? e.message
          : 'Não houve resposta do servidor. Tente de novo: o reenvio não duplica o registro.';
        ui.showError(message, mapServerFields(e && e.fields));
        return {ok: false, error: e};
      } finally {
        submitting = false;
        ui.setBusy(false);
      }
    },
  };
}

/* ---------------------------------------------------------------- request builders */

function paymentDefaults(obligation, today) {
  return {amount: centsToMoneyInput(obligation.open_cents), paid_at: today || todayISO()};
}

function buildPaymentRequest(obligation, values) {
  const errors = {};
  const amount = parseMoneyToCents(values.amount);
  if (amount == null || amount <= 0) errors.amount = 'Informe o valor pago.';
  else if (obligation.open_cents != null && amount > obligation.open_cents) {
    errors.amount = `O valor passa do saldo em aberto (${money(obligation.open_cents)}).`;
  }
  if (isBlank(values.paid_at)) errors.paid_at = 'Informe a data do pagamento.';
  const body = {amount_cents: amount, paid_at: values.paid_at, expected_version: obligation.version};
  if (obligation.kind === 'loan_installment') {
    const principal = parseMoneyToCents(values.principal);
    const interest = parseMoneyToCents(values.interest);
    if (principal == null) errors.principal = 'Informe o principal pago.';
    else if (interest == null) errors.interest = 'Informe os juros pagos (0 se não houver).';
    else if (amount != null && principal + interest !== amount) errors.interest = 'Principal + juros deve somar o valor pago.';
    body.principal_cents = principal;
    body.interest_cents = interest;
  }
  if (values.cash_mode === 'link') {
    if (isBlank(values.existing_cash_event_id)) errors.existing_cash_event_id = 'Escolha o movimento já registrado.';
    body.existing_cash_event_id = optionalId(values.existing_cash_event_id);
  } else {
    if (isBlank(values.cash_account_id)) errors.cash_account_id = 'Escolha a conta de onde saiu o dinheiro.';
    body.cash_account_id = optionalId(values.cash_account_id);
  }
  if (Object.keys(errors).length) return {errors};
  return {method: 'POST', path: `${financeBasePath()}/obligations/${obligation.kind}/${obligation.id}/payments`, body, idempotent: true};
}

function entryCandidate(values) {
  return {
    account_id: optionalId(values.account_id),
    counterparty_id: optionalId(values.counterparty_id),
    description: String(values.description || '').trim(),
    amount_cents: parseMoneyToCents(values.amount),
    competence: values.competence,
    due_date: values.due_date,
    notes: String(values.notes || '').trim(),
  };
}

function buildExpenseRequest(entry, values) {
  const errors = {};
  if (isBlank(values.description)) errors.description = 'Informe a descrição.';
  if (entry) return buildExpenseEdit(entry, values, errors);

  const amount = parseMoneyToCents(values.amount);
  if (isBlank(values.account_id)) errors.account_id = 'Escolha a categoria da despesa.';
  if (amount == null || amount <= 0) errors.amount = 'Informe um valor maior que zero.';
  const common = {account_id: String(values.account_id || ''), description: String(values.description || '').trim()};
  if (!isBlank(values.counterparty_id)) common.counterparty_id = String(values.counterparty_id);
  const base = financeBasePath();

  if (values.mode === 'recurring') {
    const dueDay = parseInt(values.due_day, 10);
    if (isBlank(values.start_competence)) errors.start_competence = 'Informe a primeira competência.';
    if (!(dueDay >= 1 && dueDay <= 31)) errors.due_day = 'O dia de vencimento vai de 1 a 31.';
    if (!isBlank(values.end_competence) && values.end_competence < values.start_competence) {
      errors.end_competence = 'A última competência vem depois da primeira.';
    }
    if (Object.keys(errors).length) return {errors};
    const body = Object.assign(common, {amount_cents: amount, start_competence: values.start_competence, due_day: dueDay});
    if (!isBlank(values.end_competence)) body.end_competence = values.end_competence;
    return {method: 'POST', path: `${base}/recurrences`, body};
  }

  if (values.mode === 'installments') {
    const count = parseInt(values.count, 10);
    if (!(count >= 1 && count <= 120)) errors.count = 'O número de parcelas vai de 1 a 120.';
    if (isBlank(values.first_due)) errors.first_due = 'Informe o vencimento da 1ª parcela.';
    if (values.competence_mode === 'single' && isBlank(values.competence)) errors.competence = 'Informe a competência.';
    if (Object.keys(errors).length) return {errors};
    const body = Object.assign(common, {
      total_cents: amount, count, first_due: values.first_due,
      competence_mode: values.competence_mode === 'distributed' ? 'distributed' : 'single', confirmed: true,
    });
    if (body.competence_mode === 'single') body.competence = values.competence;
    return {method: 'POST', path: `${base}/expense-schedules`, body};
  }

  if (isBlank(values.competence)) errors.competence = 'Informe a competência.';
  if (isBlank(values.due_date)) errors.due_date = 'Informe o vencimento.';
  if (Object.keys(errors).length) return {errors};
  const body = Object.assign(common, {amount_cents: amount, competence: values.competence, due_date: values.due_date});
  if (!isBlank(values.notes)) body.notes = String(values.notes).trim();
  return {method: 'POST', path: `${base}/entries`, body};
}

function buildExpenseEdit(entry, values, errors) {
  const allowed = entry.status === 'open' ? OPEN_EDITABLE : entry.status === 'partially_paid' ? PARTIAL_EDITABLE : [];
  const next = entryCandidate(values);
  const current = entryCandidate({
    account_id: entry.account_id, counterparty_id: entry.counterparty_id, description: entry.description,
    amount: centsToMoneyInput(entry.amount_cents), competence: entry.competence, due_date: entry.due_date, notes: entry.notes,
  });
  if (next.amount_cents == null || next.amount_cents <= 0) errors.amount = 'Informe um valor maior que zero.';
  const body = {expected_version: entry.version};
  ENTRY_FIELD_ORDER.forEach((field) => {
    if (next[field] === current[field]) return;
    const formField = ENTRY_FORM_FIELD[field] || field;
    if (!allowed.includes(field)) {
      errors[formField] = allowed.length
        ? 'Com pagamento registrado, só descrição, vencimento e observações podem mudar.'
        : 'Este lançamento não pode mais ser editado.';
      return;
    }
    body[field] = next[field];
  });
  if (Object.keys(errors).length) return {errors};
  if (Object.keys(body).length === 1) return {errors: {description: 'Nenhuma alteração para salvar.'}};
  return {method: 'PATCH', path: `${financeBasePath()}/entries/${entry.id}`, body};
}

function buildCancelRequest(entry, values) {
  const reason = String(values.reason || '').trim();
  if (!reason) return {errors: {reason: 'Informe o motivo do cancelamento.'}};
  return {method: 'POST', path: `${financeBasePath()}/entries/${entry.id}/cancel`, body: {expected_version: entry.version, reason}};
}

function buildConfirmForecastRequest(entry, values) {
  const amount = parseMoneyToCents(values.amount);
  if (amount == null || amount <= 0) return {errors: {amount: 'Informe o valor confirmado.'}};
  if (isBlank(values.competence)) return {errors: {competence: 'Informe a competência.'}};
  return {
    method: 'POST', path: `${financeBasePath()}/entries/${entry.id}/confirm`,
    body: {expected_version: entry.version, amount_cents: amount, competence: values.competence},
  };
}

function buildReversalRequest(payment, obligation, values) {
  const reason = String(values.reason || '').trim();
  const errors = {};
  if (reason.length < 3) errors.reason = 'Descreva o motivo do estorno (mínimo 3 caracteres).';
  if (isBlank(values.reversed_at)) errors.reversed_at = 'Informe a data do estorno.';
  if (Object.keys(errors).length) return {errors};
  return {
    method: 'POST', path: `${financeBasePath()}/payments/${payment.id}/reverse`,
    body: {reason, reversed_at: values.reversed_at, expected_version: obligation.version}, idempotent: true,
  };
}

function loanScheduleFromValues(principalCents, values, errors) {
  const count = parseInt(values.count, 10);
  const interest = parseMoneyToCents(values.interest);
  if (!(count >= 1 && count <= 360)) errors.count = 'O número de parcelas vai de 1 a 360.';
  if (isBlank(values.first_due)) errors.first_due = 'Informe o vencimento da 1ª parcela.';
  if (interest == null) errors.interest = 'Informe os juros por parcela (0 se não houver).';
  if (errors.count || errors.first_due || errors.interest || !(principalCents > 0)) return null;
  return buildEqualInstallments(principalCents, interest, count, values.first_due);
}

function buildLoanRequest(position, values) {
  const errors = {};
  const lender = String(values.lender || '').trim();
  const purpose = String(values.purpose || '').trim();
  if (!lender) errors.lender = 'Informe o credor.';
  if (position) {
    const loan = position.loan;
    const body = {expected_version: loan.version};
    if (lender !== loan.lender) body.lender = lender;
    if (purpose !== (loan.purpose || '')) body.purpose = purpose;
    if (Object.keys(errors).length) return {errors};
    if (Object.keys(body).length === 1) return {errors: {lender: 'Nenhuma alteração para salvar.'}};
    return {method: 'PATCH', path: `${financeBasePath()}/loans/${loan.id}`, body};
  }
  const principal = parseMoneyToCents(values.principal);
  const net = parseMoneyToCents(values.net);
  if (principal == null || principal <= 0) errors.principal = 'Informe o principal contratado.';
  if (net == null || net <= 0) errors.net = 'Informe o valor líquido recebido.';
  if (isBlank(values.start_date)) errors.start_date = 'Informe a data de início.';
  const installments = loanScheduleFromValues(principal, values, errors);
  if (Object.keys(errors).length) return {errors};
  return {
    method: 'POST', path: `${financeBasePath()}/loans`,
    body: {lender, purpose, principal_cents: principal, net_disbursement_cents: net, start_date: values.start_date, installments},
  };
}

function buildRenegotiationRequest(position, values) {
  const errors = {};
  const installments = loanScheduleFromValues(position.principal_cents, values, errors);
  const reason = String(values.reason || '').trim();
  if (!reason) errors.reason = 'Informe o motivo da renegociação.';
  if (Object.keys(errors).length) return {errors};
  return {method: 'POST', path: `${financeBasePath()}/loans/${position.loan.id}/renegotiate`, body: {installments, reason}};
}

/* ---------------------------------------------------------------- drawer */

/* Native <dialog>: focus stays inside, Escape closes, the page behind is
 * inert. The drawer removes itself on close and returns focus to the control
 * that opened it. Full page below 600px (see .drawer in style.css). */
function openDrawer(options) {
  const seq = ++FORM_STATE.drawerSeq;
  const titleId = `drawer-title-${seq}`;
  const trigger = options.trigger || document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.className = 'app-dialog drawer';
  dialog.setAttribute('aria-labelledby', titleId);
  dialog.innerHTML = `
    <div class="dialog-header">
      <h2 id="${titleId}">${esc(options.title)}</h2>
      <button type="button" class="dialog-close" data-drawer-close aria-label="Fechar">${DRAWER_CLOSE_ICON}</button>
    </div>
    <div class="drawer-body">${options.body}</div>`;
  document.body.appendChild(dialog);
  let closed = false;
  dialog.addEventListener('close', () => {
    if (closed) return;
    closed = true;
    if (typeof clearDirty === 'function') clearDirty();
    dialog.remove();
    if (trigger && typeof trigger.focus === 'function') trigger.focus();
    if (options.onClose) options.onClose();
  });
  dialog.addEventListener('click', (ev) => {
    if (ev.target && ev.target.closest && ev.target.closest('[data-drawer-close]')) dialog.close();
  });
  dialog.showModal();
  return {
    dialog,
    close() { if (dialog.open) dialog.close(); },
    setBody(html) { dialog.querySelector('.drawer-body').innerHTML = html; },
  };
}

function nextFieldId(name) { return `ff-${++FORM_STATE.fieldSeq}-${name}`; }

function formField(name, label, control, help) {
  const id = nextFieldId(name);
  const helpId = help ? `${id}-help` : '';
  return `<div class="form-field" data-field="${name}">
      <label class="field-label" for="${id}">${label}</label>
      ${control.replace(/^<(input|select|textarea)/, `<$1 id="${id}" name="${name}"${help ? ` aria-describedby="${helpId}"` : ''}`)}
      ${help ? `<p id="${helpId}" class="field-help">${help}</p>` : ''}
    </div>`;
}

const moneyInput = (value) => `<input class="login-input" type="text" inputmode="decimal" autocomplete="off" value="${esc(value || '')}">`;
const dateInput = (value, type) => `<input class="login-input" type="${type || 'date'}" value="${esc(value || '')}">`;
const textInput = (value, maxlength) => `<input class="login-input" maxlength="${maxlength || 240}" value="${esc(value || '')}">`;

function selectControl(options, selected, placeholder) {
  const opts = options.map((o) => `<option value="${esc(o.value)}"${String(o.value) === String(selected) ? ' selected' : ''}>${esc(o.label)}</option>`).join('');
  return `<select class="login-input">${placeholder != null ? `<option value="">${esc(placeholder)}</option>` : ''}${opts}</select>`;
}

function formActions(primaryLabel, extra) {
  return `<div class="form-error" data-form-error role="alert"></div>
    <div class="btn-row drawer-actions">
      <button type="submit" class="btn-primary btn-wide" value="save">${primaryLabel}</button>
      ${extra || ''}
      <button type="button" class="btn-secondary" data-drawer-close>Cancelar</button>
    </div>`;
}

function readFormValues(form) {
  const values = {};
  Array.from(form.elements).forEach((el) => {
    if (!el.name) return;
    if (el.type === 'radio') { if (el.checked) values[el.name] = el.value; return; }
    if (el.type === 'checkbox') { values[el.name] = el.checked; return; }
    values[el.name] = el.value;
  });
  return values;
}

function formUiFor(form) {
  const errorEl = form.querySelector('[data-form-error]');
  const buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
  return {
    setBusy(busy) {
      buttons.forEach((b) => { b.disabled = busy; });
      form.setAttribute('aria-busy', String(busy));
    },
    clearError() {
      errorEl.textContent = '';
      form.querySelectorAll('[aria-invalid="true"]').forEach((el) => el.setAttribute('aria-invalid', 'false'));
    },
    showError(message, fields) {
      this.clearError();
      errorEl.textContent = message;
      let first = null;
      (fields || []).forEach((name) => {
        const el = form.elements.namedItem(name);
        if (el && el.setAttribute) {
          el.setAttribute('aria-invalid', 'true');
          first = first || el;
        }
      });
      if (first && typeof first.focus === 'function') first.focus();
    },
  };
}

/* Wires one drawer form: dirty flag while typing, controller on submit,
 * after(result, intent) once saved. Values stay in the inputs on any error. */
function bindDrawerForm(drawer, buildRequest, after) {
  const form = drawer.dialog.querySelector('form');
  const ui = formUiFor(form);
  const controller = createFormController({buildRequest});
  const markDirty = () => { if (APP.pageState) APP.pageState.markDirty(true); };
  form.addEventListener('input', markDirty);
  form.addEventListener('change', markDirty);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const intent = ev.submitter && ev.submitter.value;
    const outcome = await controller.submit(readFormValues(form), ui);
    if (outcome.ok) {
      if (typeof clearDirty === 'function') clearDirty();
      after(outcome.result, intent, form);
    }
  });
  const first = form.querySelector('input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled])');
  if (first) first.focus();
  return {form, ui};
}

function summaryList(pairs) {
  return `<dl class="summary-list">${pairs.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>`;
}

async function financeLookups(needs) {
  const base = financeBasePath();
  const tasks = {
    accounts: () => api(`${base}/accounts`),
    counterparties: () => api(`${base}/counterparties`),
    cashAccounts: () => api(`${base}/cash-accounts`),
    cashEvents: () => api(`${base}/cash-events`),
  };
  const keys = needs.filter((k) => tasks[k]);
  const results = await Promise.all(keys.map((k) => tasks[k]()));
  return Object.fromEntries(keys.map((k, i) => [k, results[i]]));
}

function drawerLoadError(drawer, error, retry) {
  drawer.setBody(`<div class="story-box">Não foi possível carregar: ${esc(error.message)}</div>
    <div class="btn-row"><button type="button" class="btn-primary btn-wide" data-drawer-retry>Tentar novamente</button></div>`);
  drawer.dialog.querySelector('[data-drawer-retry]').addEventListener('click', retry);
}

/* ---------------------------------------------------------------- expense */

var EXPENSE_NATURES = ['operating_expense', 'financial_expense', 'tax_expense'];

async function openExpenseForm(entry, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({title: entry ? 'Editar despesa' : 'Nova despesa', body: '<div class="skeleton-block" aria-label="Carregando formulário"></div>', trigger: ctx.trigger});
  let lookups;
  try {
    lookups = ctx.accounts && ctx.counterparties ? ctx : await financeLookups(['accounts', 'counterparties']);
  } catch (e) {
    return drawerLoadError(drawer, e, () => { drawer.close(); openExpenseForm(entry, ctx); });
  }
  const accountOptions = lookups.accounts
    .filter((a) => EXPENSE_NATURES.includes(a.nature) && (!a.archived || (entry && String(a.id) === String(entry.account_id))))
    .map((a) => ({value: a.id, label: `${a.code} — ${a.name}`}));
  const counterpartyOptions = lookups.counterparties.map((c) => ({value: c.id, label: c.name}));
  const editable = !entry ? ENTRY_FIELD_ORDER
    : entry.source !== 'manual' ? []
      : entry.status === 'open' ? OPEN_EDITABLE : entry.status === 'partially_paid' ? PARTIAL_EDITABLE : [];
  const lock = (field) => (editable.includes(field) ? '' : ' disabled');
  const withLock = (html, field) => html.replace(/^<(input|select|textarea)/, `<$1${lock(field)}`);
  const v = entry || {};
  const period = APP.period || todayISO().slice(0, 7);

  const lockNote = !entry ? '' : !editable.length
    ? `<div class="story-box">${entry.source !== 'manual'
      ? `Lançamento de origem ${esc(entry.source)}: altere na origem ou cancele e lance de novo.`
      : 'Lançamento pago, cancelado ou estornado: não pode mais ser editado. Para corrigir um pagamento, use Contas a Pagar → Detalhes → Estornar.'}</div>`
    : editable === PARTIAL_EDITABLE ? '<div class="story-box">Já existe pagamento: só descrição, vencimento e observações podem mudar. Para alterar valor, estorne o pagamento antes.</div>' : '';

  const modeSwitch = entry ? '' : `
    <fieldset class="segmented" data-expense-mode>
      <legend class="field-label">Tipo de lançamento</legend>
      <label><input type="radio" name="mode" value="single" checked> Única</label>
      <label><input type="radio" name="mode" value="recurring"> Recorrente</label>
      <label><input type="radio" name="mode" value="installments"> Parcelada</label>
    </fieldset>`;

  drawer.setBody(`
    <form class="drawer-form" novalidate>
      ${lockNote}
      ${modeSwitch}
      ${formField('description', 'Descrição', withLock(textInput(v.description, 240), 'description'))}
      ${formField('account_id', 'Categoria da despesa', withLock(selectControl(accountOptions, v.account_id, 'Escolha…'), 'account_id'))}
      ${formField('amount', '<span data-amount-label>Valor (R$)</span>', withLock(moneyInput(entry ? centsToMoneyInput(v.amount_cents) : ''), 'amount_cents'), 'Use vírgula para centavos: 1.234,56')}
      <div data-mode-section="single">
        <div class="form-grid">
          ${formField('competence', 'Competência', withLock(dateInput(v.competence || period, 'month'), 'competence'), 'Mês em que a despesa pertence ao resultado.')}
          ${formField('due_date', 'Vencimento', withLock(dateInput(v.due_date), 'due_date'))}
        </div>
      </div>
      <div data-mode-section="recurring" hidden>
        <div class="form-grid">
          ${formField('start_competence', 'Primeira competência', dateInput(period, 'month'))}
          ${formField('end_competence', 'Última competência (opcional)', dateInput('', 'month'))}
          ${formField('due_day', 'Dia do vencimento', '<input class="login-input" type="number" min="1" max="31" step="1" value="10">')}
        </div>
        <p class="field-help">Gera valores previstos até ${esc(period)}; cada mês é confirmado antes de entrar no realizado.</p>
      </div>
      <div data-mode-section="installments" hidden>
        <div class="form-grid">
          ${formField('count', 'Número de parcelas', '<input class="login-input" type="number" min="1" max="120" step="1" value="3">')}
          ${formField('first_due', 'Vencimento da 1ª parcela', dateInput(''))}
          ${formField('competence_mode', 'Competência', selectControl([{value: 'single', label: 'Única (toda a despesa em um mês)'}, {value: 'distributed', label: 'Distribuída (uma por parcela)'}], 'single'))}
          ${formField('competence', 'Mês da competência', dateInput(period, 'month'))}
        </div>
        <div class="btn-row"><button type="button" class="btn-secondary" data-schedule-preview>Ver prévia das parcelas</button></div>
        <div data-schedule-preview-result aria-live="polite"></div>
      </div>
      <details class="form-details"${v.counterparty_id || v.notes ? ' open' : ''}>
        <summary>Fornecedor e observações</summary>
        ${formField('counterparty_id', 'Fornecedor ou favorecido', withLock(selectControl(counterpartyOptions, v.counterparty_id, 'Nenhum'), 'counterparty_id'))}
        ${formField('notes', 'Observações', withLock(`<textarea class="login-input" rows="3" maxlength="2000">${esc(v.notes || '')}</textarea>`, 'notes'))}
      </details>
      ${editable.length ? formActions(entry ? 'Salvar alterações' : 'Salvar', entry ? '' : '<button type="submit" class="btn-secondary" value="again">Salvar e adicionar outra</button>')
    : '<div class="btn-row drawer-actions"><button type="button" class="btn-secondary" data-drawer-close>Fechar</button></div>'}
    </form>`);
  if (!editable.length) return;

  const form = drawer.dialog.querySelector('form');
  let previewSignature = null;
  const scheduleSignature = (values) => JSON.stringify([values.amount, values.count, values.first_due, values.competence_mode, values.competence]);
  const syncMode = () => {
    const mode = entry ? 'single' : readFormValues(form).mode;
    form.querySelectorAll('[data-mode-section]').forEach((section) => {
      const active = section.dataset.modeSection === mode;
      section.hidden = !active;
      section.querySelectorAll('input, select').forEach((el) => { el.disabled = !active; });
    });
    form.querySelector('[data-amount-label]').textContent = mode === 'installments' ? 'Valor total (R$)' : 'Valor (R$)';
  };
  form.addEventListener('change', (ev) => { if (ev.target.name === 'mode') syncMode(); });
  syncMode();

  const previewButton = form.querySelector('[data-schedule-preview]');
  if (previewButton) {
    previewButton.addEventListener('click', async () => {
      const values = readFormValues(form);
      const target = form.querySelector('[data-schedule-preview-result]');
      const total = parseMoneyToCents(values.amount);
      if (!total) { target.textContent = 'Informe o valor total antes da prévia.'; return; }
      previewButton.disabled = true;
      try {
        const preview = await api(`${financeBasePath()}/expense-schedules/preview`, {
          method: 'POST',
          body: JSON.stringify({
            total_cents: total, count: parseInt(values.count, 10), first_due: values.first_due,
            competence_mode: values.competence_mode,
            competence: values.competence_mode === 'single' ? values.competence : null,
          }),
        });
        previewSignature = scheduleSignature(values);
        target.innerHTML = `<div class="table-wrap"><table class="data-table">
          <thead><tr><th>#</th><th>Vencimento</th><th>Competência</th><th class="num">Valor</th></tr></thead>
          <tbody>${preview.items.map((item, i) => `<tr><td>${i + 1}</td><td>${dateBR(item.due_date)}</td><td>${esc(item.competence)}</td><td class="num">${money(item.amount_cents)}</td></tr>`).join('')}</tbody>
        </table></div>`;
      } catch (e) {
        target.textContent = e.message;
      } finally {
        previewButton.disabled = false;
      }
    });
  }

  bindDrawerForm(drawer, (values) => {
    if (!entry && values.mode === 'installments' && previewSignature !== scheduleSignature(values)) {
      const request = buildExpenseRequest(null, values);
      return request.errors ? request : {errors: {count: 'Veja a prévia das parcelas antes de salvar.'}};
    }
    return buildExpenseRequest(entry, values);
  }, async (result, intent, savedForm) => {
    const values = readFormValues(savedForm);
    if (!entry && values.mode === 'recurring' && result && result.id) {
      try {
        await api(`${financeBasePath()}/recurrences/${result.id}/generate?through_competence=${encodeURIComponent(period)}`, {method: 'POST'});
      } catch (e) { /* the rule is saved; forecasts can be generated on the next visit */ }
    }
    drawer.close();
    if (ctx.onSaved) ctx.onSaved(result);
    if (intent === 'again') openExpenseForm(null, Object.assign({}, ctx, lookups, {trigger: ctx.trigger}));
  });
}

function openCancelEntryForm(entry, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({
    title: 'Cancelar lançamento', trigger: ctx.trigger,
    body: `<form class="drawer-form" novalidate>
      ${summaryList([['Lançamento', esc(entry.description)], ['Valor', money(entry.amount_cents)], ['Vencimento', dateBR(entry.due_date)]])}
      <div class="story-box">Efeito: o lançamento sai de Contas a Pagar e do resultado da competência. Nada é apagado: o histórico guarda o motivo.</div>
      ${formField('reason', 'Motivo do cancelamento', '<textarea class="login-input" rows="3" maxlength="500" required></textarea>')}
      ${formActions('Cancelar lançamento').replace('>Cancelar</button>', '>Voltar</button>')}
    </form>`,
  });
  bindDrawerForm(drawer, (values) => buildCancelRequest(entry, values), (result) => {
    drawer.close();
    if (ctx.onSaved) ctx.onSaved(result);
  });
}

function openConfirmForecastForm(entry, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({
    title: 'Confirmar valor previsto', trigger: ctx.trigger,
    body: `<form class="drawer-form" novalidate>
      ${summaryList([['Lançamento', esc(entry.description)], ['Previsto', money(entry.amount_cents)], ['Vencimento', dateBR(entry.due_date)]])}
      <p class="field-help">Ao confirmar, o valor passa a compor o realizado e aparece em Contas a Pagar.</p>
      ${formField('amount', 'Valor confirmado (R$)', moneyInput(centsToMoneyInput(entry.amount_cents)))}
      ${formField('competence', 'Competência', dateInput(entry.competence, 'month'))}
      ${formActions('Confirmar')}
    </form>`,
  });
  bindDrawerForm(drawer, (values) => buildConfirmForecastRequest(entry, values), (result) => {
    drawer.close();
    if (ctx.onSaved) ctx.onSaved(result);
  });
}

var HISTORY_FIELD_LABELS = {
  description: 'Descrição', amount_cents: 'Valor', due_date: 'Vencimento', competence: 'Competência',
  notes: 'Observações', account_id: 'Categoria', counterparty_id: 'Fornecedor', status: 'Status',
};
var HISTORY_ACTION_LABELS = {
  update: 'Alteração', cancel: 'Cancelamento', confirm: 'Confirmação', created: 'Criação', settled: 'Pagamento',
  reversed: 'Estorno', payment_reversed: 'Estorno de pagamento',
};

function historyValue(field, value) {
  if (value == null || value === '') return '—';
  if (field === 'amount_cents') return money(value);
  if (field === 'due_date') return dateBR(value);
  return esc(value);
}

async function openEntryHistory(entry, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({title: `Histórico — ${entry.description}`, body: '<div class="skeleton-block" aria-label="Carregando histórico"></div>', trigger: ctx.trigger});
  let history;
  try {
    history = await api(`${financeBasePath()}/entries/${entry.id}/history`);
  } catch (e) {
    return drawerLoadError(drawer, e, () => { drawer.close(); openEntryHistory(entry, ctx); });
  }
  const items = history.map((item) => {
    const when = dt(item.created_at);
    const label = HISTORY_ACTION_LABELS[item.action] || item.action;
    if (item.kind === 'event') {
      return `<li><strong>${esc(label)}</strong> — ${money(item.amount_cents)} <span class="muted">${when}</span></li>`;
    }
    const before = item.before || {};
    const after = item.after || {};
    const changes = Object.keys(HISTORY_FIELD_LABELS)
      .filter((field) => JSON.stringify(before[field]) !== JSON.stringify(after[field]))
      .map((field) => `<li>${HISTORY_FIELD_LABELS[field]}: ${historyValue(field, before[field])} → ${historyValue(field, after[field])}</li>`)
      .join('');
    return `<li><strong>${esc(label)}</strong> <span class="muted">${when}</span>
      ${item.reason ? `<div>Motivo: ${esc(item.reason)}</div>` : ''}
      ${changes ? `<ul class="history-changes">${changes}</ul>` : ''}</li>`;
  }).join('');
  drawer.setBody(`${items ? `<ol class="history-list">${items}</ol>` : '<p>Sem alterações registradas.</p>'}
    <div class="btn-row drawer-actions"><button type="button" class="btn-secondary" data-drawer-close>Fechar</button></div>`);
}

/* ---------------------------------------------------------------- payments */

async function openPaymentForm(obligation, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({title: 'Registrar pagamento', body: '<div class="skeleton-block" aria-label="Carregando contas de caixa"></div>', trigger: ctx.trigger});
  let lookups;
  try {
    lookups = ctx.cashAccounts && ctx.cashEvents ? ctx : await financeLookups(['cashAccounts', 'cashEvents']);
  } catch (e) {
    return drawerLoadError(drawer, e, () => { drawer.close(); openPaymentForm(obligation, ctx); });
  }
  const defaults = paymentDefaults(obligation);
  const accountOptions = lookups.cashAccounts.filter((a) => !a.archived).map((a) => ({value: a.id, label: a.name}));
  const outflows = lookups.cashEvents.filter((e) => e.amount_cents < 0 && !e.reversed_at);
  const eventOptions = outflows.map((e) => ({value: e.id, label: `${dateBR(e.occurred_at)} — ${e.description} — ${money(e.amount_cents)}`}));
  const isLoan = obligation.kind === 'loan_installment';
  const split = ctx.split || {};

  drawer.setBody(`
    <form class="drawer-form" novalidate>
      ${summaryList([
        ['Obrigação', esc(obligation.description)], ['Vencimento', dateBR(obligation.due_date)],
        ['Total', money(obligation.total_cents)], ['Já pago', money(obligation.paid_cents)],
        ['Saldo em aberto', `<strong>${money(obligation.open_cents)}</strong>`],
      ])}
      <div class="form-grid">
        ${formField('amount', 'Valor pago (R$)', moneyInput(defaults.amount), 'Pagamento parcial: informe só o valor pago agora.')}
        ${formField('paid_at', 'Data do pagamento', dateInput(defaults.paid_at))}
        ${isLoan ? formField('principal', 'Principal (R$)', moneyInput(split.principal_cents != null ? centsToMoneyInput(split.principal_cents) : '')) : ''}
        ${isLoan ? formField('interest', 'Juros (R$)', moneyInput(split.interest_cents != null ? centsToMoneyInput(split.interest_cents) : ''), 'Principal + juros = valor pago.') : ''}
      </div>
      <fieldset class="segmented">
        <legend class="field-label">Como o dinheiro saiu</legend>
        <label><input type="radio" name="cash_mode" value="generate" checked> Lançar saída na conta</label>
        <label><input type="radio" name="cash_mode" value="link"${eventOptions.length ? '' : ' disabled'}> Vincular movimento já registrado</label>
      </fieldset>
      <div data-cash-mode="generate">
        ${formField('cash_account_id', 'Conta de pagamento', selectControl(accountOptions, accountOptions.length === 1 ? accountOptions[0].value : '', 'Escolha…'),
          accountOptions.length ? 'Uma saída com este valor é lançada no fluxo de caixa.' : 'Nenhuma conta cadastrada: crie uma em Fluxo de Caixa.')}
      </div>
      <div data-cash-mode="link" hidden>
        ${formField('event_filter', 'Buscar movimento', '<input class="login-input" type="search" autocomplete="off" placeholder="Descrição, data ou valor">')}
        ${formField('existing_cash_event_id', 'Movimento importado ou lançado', selectControl(eventOptions, '', 'Escolha…'), 'Nenhuma nova saída é criada: o pagamento usa este movimento.')}
      </div>
      ${formActions('Registrar pagamento')}
    </form>`);

  const form = drawer.dialog.querySelector('form');
  const syncCashMode = () => {
    const mode = readFormValues(form).cash_mode;
    form.querySelectorAll('[data-cash-mode]').forEach((section) => {
      section.hidden = section.dataset.cashMode !== mode;
    });
  };
  form.addEventListener('change', (ev) => { if (ev.target.name === 'cash_mode') syncCashMode(); });
  const filter = form.elements.namedItem('event_filter');
  const eventSelect = form.elements.namedItem('existing_cash_event_id');
  filter.addEventListener('input', () => {
    const term = filter.value.trim().toLowerCase();
    Array.from(eventSelect.options).forEach((option) => {
      option.hidden = !!option.value && !!term && !option.textContent.toLowerCase().includes(term);
    });
  });
  bindDrawerForm(drawer, (values) => buildPaymentRequest(obligation, values), (result) => {
    drawer.close();
    if (ctx.onSaved) ctx.onSaved(result);
  });
}

async function openObligationDetails(kind, id, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({title: 'Detalhes da obrigação', body: '<div class="skeleton-block" aria-label="Carregando obrigação"></div>', trigger: ctx.trigger});
  let obligation;
  try {
    obligation = await api(`${financeBasePath()}/obligations/${kind}/${id}`);
  } catch (e) {
    return drawerLoadError(drawer, e, () => { drawer.close(); openObligationDetails(kind, id, ctx); });
  }
  const canWrite = obligation.allowed_actions.includes('pay') || obligation.allowed_actions.includes('edit_description');
  const payments = obligation.payments || [];
  const rows = payments.map((p) => `
    <tr>
      <td>${dateBR(p.paid_at)}</td>
      <td class="num">${money(p.amount_cents)}</td>
      <td>${p.cash_event_id ? (p.generated_cash_event ? 'Saída lançada' : 'Movimento vinculado') : '—'}</td>
      <td>${p.reversed_at ? `Estornado em ${dateBR(p.reversed_at)}${p.reversal_reason ? ` — ${esc(p.reversal_reason)}` : ''}`
        : `<button type="button" class="btn-secondary" data-reverse-payment="${esc(p.id)}">Estornar</button>`}</td>
    </tr>`).join('');
  const blockedPay = obligation.open_cents > 0 && !obligation.allowed_actions.includes('pay')
    ? '<p class="field-help">Pagamento indisponível: parcela de cronograma substituído, origem que não aceita pagamento direto ou sem permissão de escrita.</p>' : '';
  drawer.setBody(`
    ${summaryList([
      ['Obrigação', esc(obligation.description)], ['Vencimento', dateBR(obligation.due_date)],
      ['Competência', esc(obligation.competence || '—')], ['Status', esc(FINANCE_STATUS_LABELS[obligation.status] || obligation.status)],
      ['Total', money(obligation.total_cents)], ['Pago', money(obligation.paid_cents)], ['Saldo', `<strong>${money(obligation.open_cents)}</strong>`],
    ])}
    <h3 class="drawer-subtitle">Pagamentos</h3>
    ${rows ? `<div class="table-wrap"><table class="data-table">
      <thead><tr><th>Data</th><th class="num">Valor</th><th>Caixa</th><th>Situação</th></tr></thead><tbody>${rows}</tbody>
    </table></div>` : '<p>Nenhum pagamento registrado.</p>'}
    ${blockedPay}
    <div class="btn-row drawer-actions">
      ${obligation.allowed_actions.includes('pay') ? '<button type="button" class="btn-primary btn-wide" data-detail-pay>Registrar pagamento</button>' : ''}
      ${kind === 'entry' ? '<button type="button" class="btn-secondary" data-detail-history>Histórico</button>' : ''}
      <button type="button" class="btn-secondary" data-drawer-close>Fechar</button>
    </div>`);
  const reopen = (result) => { if (ctx.onSaved) ctx.onSaved(result); };
  const payButton = drawer.dialog.querySelector('[data-detail-pay]');
  if (payButton) payButton.addEventListener('click', () => { drawer.close(); openPaymentForm(obligation, Object.assign({}, ctx, {onSaved: reopen})); });
  const historyButton = drawer.dialog.querySelector('[data-detail-history]');
  if (historyButton) historyButton.addEventListener('click', () => { drawer.close(); openEntryHistory(obligation, ctx); });
  drawer.dialog.querySelectorAll('[data-reverse-payment]').forEach((button) => {
    if (!canWrite && !obligation.allowed_actions.includes('details')) button.disabled = true;
    button.addEventListener('click', () => {
      const payment = payments.find((p) => p.id === button.dataset.reversePayment);
      drawer.close();
      openReversalForm(payment, obligation, Object.assign({}, ctx, {onSaved: reopen}));
    });
  });
}

function openReversalForm(payment, obligation, ctx) {
  ctx = ctx || {};
  const reopened = obligation.open_cents + payment.amount_cents;
  const effect = payment.generated_cash_event
    ? 'Uma entrada inversa é lançada no caixa; a saída original continua no histórico.'
    : payment.cash_event_id ? 'O movimento vinculado fica livre para outro pagamento ou conciliação.' : '';
  const drawer = openDrawer({
    title: 'Estornar pagamento', trigger: ctx.trigger,
    body: `<form class="drawer-form" novalidate>
      ${summaryList([['Obrigação', esc(obligation.description)], ['Pagamento', `${money(payment.amount_cents)} em ${dateBR(payment.paid_at)}`], ['Saldo depois do estorno', money(reopened)]])}
      <div class="story-box">Efeito: só este pagamento é desfeito e a obrigação volta a ter ${money(reopened)} em aberto. ${effect}</div>
      ${formField('reversed_at', 'Data do estorno', dateInput(todayISO()))}
      ${formField('reason', 'Motivo', '<textarea class="login-input" rows="3" maxlength="500" required></textarea>')}
      ${formActions('Estornar pagamento').replace('>Cancelar</button>', '>Voltar</button>')}
    </form>`,
  });
  bindDrawerForm(drawer, (values) => buildReversalRequest(payment, obligation, values), (result) => {
    drawer.close();
    if (ctx.onSaved) ctx.onSaved(result);
  });
}

/* ---------------------------------------------------------------- loans */

function schedulePreviewTable(installments) {
  if (!installments || !installments.length) return '<p class="field-help">Preencha os campos para ver o cronograma.</p>';
  const total = installments.reduce((sum, i) => sum + i.principal_cents + i.interest_cents, 0);
  return `<div class="table-wrap"><table class="data-table">
    <thead><tr><th>#</th><th>Vencimento</th><th class="num">Principal</th><th class="num">Juros</th></tr></thead>
    <tbody>${installments.map((i) => `<tr><td>${i.number}</td><td>${dateBR(i.due_date)}</td><td class="num">${money(i.principal_cents)}</td><td class="num">${money(i.interest_cents)}</td></tr>`).join('')}</tbody>
    <tfoot><tr><td colspan="2">Total a pagar</td><td class="num" colspan="2">${money(total)}</td></tr></tfoot>
  </table></div>`;
}

function wireSchedulePreview(form, principalOf) {
  const target = form.querySelector('[data-loan-preview]');
  const refresh = () => {
    const values = readFormValues(form);
    const installments = loanScheduleFromValues(principalOf(values), values, {});
    target.innerHTML = schedulePreviewTable(installments);
  };
  form.addEventListener('input', refresh);
  refresh();
}

function openLoanForm(position, ctx) {
  ctx = ctx || {};
  const loan = position ? position.loan : {};
  const scheduleFields = position ? '' : `
    <div class="form-grid">
      ${formField('principal', 'Principal contratado (R$)', moneyInput(''))}
      ${formField('net', 'Valor líquido recebido (R$)', moneyInput(''), 'Principal menos tarifas e IOF.')}
      ${formField('start_date', 'Data de início', dateInput(todayISO()))}
      ${formField('count', 'Número de parcelas', '<input class="login-input" type="number" min="1" max="360" step="1" value="12">')}
      ${formField('first_due', '1ª parcela vence em', dateInput(''))}
      ${formField('interest', 'Juros por parcela (R$)', moneyInput('0,00'))}
    </div>
    <h3 class="drawer-subtitle">Prévia do cronograma</h3>
    <div data-loan-preview aria-live="polite"></div>`;
  const drawer = openDrawer({
    title: position ? 'Editar empréstimo' : 'Novo empréstimo', trigger: ctx.trigger,
    body: `<form class="drawer-form" novalidate>
      ${position ? `<div class="story-box">Valores e parcelas mudam só por Renegociar, que substitui as parcelas futuras e preserva as pagas.</div>` : ''}
      ${formField('lender', 'Credor', textInput(loan.lender, 180))}
      ${formField('purpose', 'Finalidade', textInput(loan.purpose, 240))}
      ${scheduleFields}
      ${formActions(position ? 'Salvar alterações' : 'Cadastrar empréstimo')}
    </form>`,
  });
  const {form} = bindDrawerForm(drawer, (values) => buildLoanRequest(position, values), (result) => {
    drawer.close();
    if (ctx.onSaved) ctx.onSaved(result);
  });
  if (!position) wireSchedulePreview(form, (values) => parseMoneyToCents(values.principal));
}

function openRenegotiationForm(position, ctx) {
  ctx = ctx || {};
  const drawer = openDrawer({
    title: `Renegociar — ${position.loan.lender}`, trigger: ctx.trigger,
    body: `<form class="drawer-form" novalidate>
      ${summaryList([['Principal em aberto', `<strong>${money(position.principal_cents)}</strong>`], ['Principal já pago', money(position.paid_principal_cents)]])}
      <div class="story-box">As parcelas em aberto são substituídas pelo novo cronograma. Parcelas pagas e seus pagamentos não mudam.</div>
      <div class="form-grid">
        ${formField('count', 'Novas parcelas', '<input class="login-input" type="number" min="1" max="360" step="1" value="12">')}
        ${formField('first_due', '1ª nova parcela vence em', dateInput(''))}
        ${formField('interest', 'Juros por parcela (R$)', moneyInput('0,00'))}
      </div>
      <h3 class="drawer-subtitle">Prévia do novo cronograma</h3>
      <div data-loan-preview aria-live="polite"></div>
      ${formField('reason', 'Motivo da renegociação', '<textarea class="login-input" rows="2" maxlength="500" required></textarea>')}
      ${formActions('Renegociar parcelas futuras')}
    </form>`,
  });
  const {form} = bindDrawerForm(drawer, (values) => buildRenegotiationRequest(position, values), (result) => {
    drawer.close();
    if (ctx.onSaved) ctx.onSaved(result);
  });
  wireSchedulePreview(form, () => position.principal_cents);
}

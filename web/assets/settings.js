/* Administration settings pages. Loaded before app.js and uses its shared
 * api(), esc(), APP and renderEmptyState() globals only when a route opens. */
'use strict';

const SETTINGS_LABELS = {
  empresa: 'Dados da empresa',
  usuarios: 'Usuários',
  cadastros: 'Categorias e favorecidos',
  calendario: 'Calendário',
  metas: 'Metas',
  alertas: 'Alertas',
  integracoes: 'Integrações e sincronização',
};

var BR_STATES = ['AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS', 'MG', 'PA', 'PB', 'PR', 'PE',
  'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO'];

function onlyDigits(value) { return String(value || '').replace(/\D/g, ''); }

function formatCnpj(value) {
  const d = onlyDigits(value).slice(0, 14);
  let out = d.slice(0, 2);
  if (d.length > 2) out += '.' + d.slice(2, 5);
  if (d.length > 5) out += '.' + d.slice(5, 8);
  if (d.length > 8) out += '/' + d.slice(8, 12);
  if (d.length > 12) out += '-' + d.slice(12, 14);
  return out;
}

// Check digits (mod 11), not only the length.
function isValidCnpj(value) {
  const d = onlyDigits(value);
  if (d.length !== 14 || /^(\d)\1{13}$/.test(d)) return false;
  const digit = (length) => {
    const weights = length === 12 ? [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2] : [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2];
    const rest = weights.reduce((sum, weight, i) => sum + Number(d[i]) * weight, 0) % 11;
    return rest < 2 ? 0 : 11 - rest;
  };
  return digit(12) === Number(d[12]) && digit(13) === Number(d[13]);
}

function formatPhone(value) {
  const d = onlyDigits(value).slice(0, 11);
  if (!d) return '';
  if (d.length <= 2) return `(${d}`;
  const rest = d.slice(2);
  if (rest.length <= 4) return `(${d.slice(0, 2)}) ${rest}`;
  const split = rest.length === 9 ? 5 : 4;
  return `(${d.slice(0, 2)}) ${rest.slice(0, split)}-${rest.slice(split)}`;
}

function settingsShell(section, body) {
  const tabs = Object.entries(SETTINGS_LABELS).map(([id, label]) =>
    `<a class="settings-tab ${id === section ? 'active' : ''}"
       href="#/configuracoes/${id}" ${id === section ? 'aria-current="page"' : ''}>${label}</a>`
  ).join('');
  return `
    <h1 class="page-title">${icon('sliders-horizontal', {class: 'title-icon'})}Configurações</h1>
    <div class="page-subtitle">Cadastros, acessos e regras usadas na gestão da empresa.</div>
    <nav class="settings-tabs" aria-label="Seções de configurações">${tabs}</nav>
    <section class="settings-panel">${body}</section>`;
}

function settingsLoading() {
  document.getElementById('content').innerHTML = settingsShell(
    APP.settingsSection,
    '<div class="skeleton-block" aria-label="Carregando configurações"></div>'
  );
}

async function renderSettingsPage(token) {
  token = token || beginPage();
  const section = APP.settingsSection || 'empresa';
  settingsLoading();
  try {
    if (section === 'empresa') return await renderCompanySettings(token);
    if (section === 'usuarios') return await renderUserSettings(token);
    if (section === 'cadastros') return await renderCatalogSettings(token);
    if (section === 'calendario') return await renderCalendarSettings(token);
    if (section === 'integracoes') return await renderIntegrationsSettings(token);
    if (section === 'metas') return await renderGoalsSettings(token);
    return renderPlannedSettings(section);
  } catch (error) {
    if (!APP.pageState.isCurrent(token)) return;  // usuário já saiu desta rota
    document.getElementById('content').innerHTML = settingsShell(
      section,
      `<div class="story-box">Não foi possível carregar esta configuração: ${esc(error.message)}</div>
       <div class="btn-row"><button type="button" class="btn-primary" id="settings-retry">Tentar novamente</button></div>`
    );
    document.getElementById('settings-retry').addEventListener('click', () => renderSettingsPage());
  }
}

async function renderCompanySettings(token) {
  token = token || beginPage();
  const profile = await api(`/api/companies/${APP.company}/settings/company`);
  if (!APP.pageState.isCurrent(token)) return;  // resposta obsoleta: descarta em silêncio
  const address = profile.address || {};
  const contacts = profile.contacts || {};
  const currentState = String(address.state || '').toUpperCase();
  const stateOptions = ['<option value="">Selecione</option>'].concat(BR_STATES.map((uf) =>
    `<option value="${uf}"${uf === currentState ? ' selected' : ''}>${uf}</option>`)).join('');

  document.getElementById('content').innerHTML = settingsShell('empresa', `
    <form id="company-settings-form" class="settings-form" novalidate>
      <div class="settings-heading">
        <div><h2>Dados da empresa</h2><p>Estes dados identificam a loja nos relatórios. O ID da Mobne é preservado e não pode ser editado.</p></div>
        <span class="periodo-badge muted">Mobne ID ${esc(APP.company)}</span>
      </div>
      <div class="form-grid">
        <div>
          <label for="settings-legal-name" class="field-label">Razão social</label>
          <input id="settings-legal-name" class="login-input" maxlength="180" autocomplete="organization" value="${esc(profile.legal_name)}">
        </div>
        <div>
          <label for="settings-trade-name" class="field-label">Nome fantasia</label>
          <input id="settings-trade-name" class="login-input" maxlength="180" value="${esc(profile.trade_name)}">
        </div>
        <div>
          <label for="settings-cnpj" class="field-label">CNPJ</label>
          <input id="settings-cnpj" class="login-input" maxlength="18" inputmode="numeric" aria-describedby="settings-cnpj-help" value="${esc(formatCnpj(profile.cnpj))}">
          <p id="settings-cnpj-help" class="field-help">14 dígitos; os dígitos verificadores são conferidos antes de salvar.</p>
        </div>
        <div>
          <label for="settings-logo-url" class="field-label">URL do logotipo</label>
          <input id="settings-logo-url" class="login-input" type="url" maxlength="500" value="${esc(profile.logo_url)}">
        </div>
        <div class="form-grid-wide">
          <label for="settings-address" class="field-label">Endereço</label>
          <input id="settings-address" class="login-input" maxlength="250" autocomplete="street-address" value="${esc(address.line || '')}">
        </div>
        <div>
          <label for="settings-city" class="field-label">Cidade</label>
          <input id="settings-city" class="login-input" maxlength="100" autocomplete="address-level2" value="${esc(address.city || '')}">
        </div>
        <div>
          <label for="settings-state" class="field-label">Estado (UF)</label>
          <select id="settings-state" class="login-input" autocomplete="address-level1">${stateOptions}</select>
        </div>
        <div>
          <label for="settings-phone" class="field-label">Telefone</label>
          <input id="settings-phone" class="login-input" type="tel" inputmode="tel" autocomplete="tel" maxlength="15" value="${esc(formatPhone(contacts.phone || ''))}">
        </div>
        <div>
          <label for="settings-contact-email" class="field-label">E-mail da empresa</label>
          <input id="settings-contact-email" class="login-input" type="email" autocomplete="email" value="${esc(contacts.email || '')}">
        </div>
      </div>
      <div class="settings-actions">
        <button class="btn-primary" type="submit">Salvar dados</button>
        <span id="company-settings-status" class="form-success" role="status" aria-live="polite"></span>
      </div>
    </form>`);

  const form = watchForm(document.getElementById('company-settings-form'));
  const cnpj = document.getElementById('settings-cnpj');
  const phone = document.getElementById('settings-phone');
  cnpj.addEventListener('input', () => { cnpj.value = formatCnpj(cnpj.value); });
  phone.addEventListener('input', () => { phone.value = formatPhone(phone.value); });
  let saving = false;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (saving) return;
    const status = document.getElementById('company-settings-status');
    const button = form.querySelector('button[type="submit"]');
    cnpj.setAttribute('aria-invalid', 'false');
    if (cnpj.value.trim() && !isValidCnpj(cnpj.value)) {
      status.className = 'form-error';
      status.textContent = 'CNPJ inválido: confira os 14 dígitos.';
      cnpj.setAttribute('aria-invalid', 'true');
      cnpj.focus();
      return;
    }
    saving = true;
    button.disabled = true;
    status.className = 'form-success';
    status.textContent = 'Salvando…';
    try {
      const saved = await api(`/api/companies/${APP.company}/settings/company`, {
        method: 'PUT',
        body: JSON.stringify({
          expected_version: profile.version,
          legal_name: document.getElementById('settings-legal-name').value.trim(),
          trade_name: document.getElementById('settings-trade-name').value.trim(),
          cnpj: cnpj.value.trim(),
          logo_url: document.getElementById('settings-logo-url').value.trim(),
          address: {
            line: document.getElementById('settings-address').value.trim(),
            city: document.getElementById('settings-city').value.trim(),
            state: document.getElementById('settings-state').value,
          },
          contacts: {
            phone: phone.value.trim(),
            email: document.getElementById('settings-contact-email').value.trim(),
          },
        }),
      });
      profile.version = saved.version;
      clearDirty();
      status.className = 'form-success';
      status.textContent = 'Dados salvos.';
    } catch (error) {
      status.className = 'form-error';
      status.textContent = error.message;
    } finally {
      saving = false;
      button.disabled = false;
    }
  });
}

async function renderUserSettings(token) {
  token = token || beginPage();
  const [users, roles] = await Promise.all([
    api(`/api/companies/${APP.company}/users`),
    api('/api/roles'),
  ]);
  if (!APP.pageState.isCurrent(token)) return;
  const rows = users.length ? users.map((user) => `
    <tr><td>${esc(user.name)}</td><td>${esc(user.email)}</td>
      <td>${esc(SETTINGS_ROLE_LABELS[user.role] || user.role)}</td>
      <td>${user.active ? 'Ativo' : 'Inativo'}</td>
      <td><div class="row-actions">${user.active ? `
        <button type="button" class="btn-secondary" data-user-edit="${esc(user.id)}" aria-label="Editar ${esc(user.name)}">Editar</button>
        <button type="button" class="btn-secondary" data-user-disable="${esc(user.id)}" aria-label="Desativar usuário ${esc(user.name)}">Desativar usuário</button>` : ''}</div></td></tr>`).join('') :
    '<tr><td colspan="5">Nenhum usuário vinculado.</td></tr>';
  const options = roles.map((role) =>
    `<option value="${esc(role.id)}">${esc(SETTINGS_ROLE_LABELS[role.id] || role.id)}</option>`
  ).join('');
  document.getElementById('content').innerHTML = settingsShell('usuarios', `
    <h2>Usuários e perfis</h2>
    <p class="field-help">Cada pessoa altera o próprio e-mail e senha em Minha conta. Não é possível alterar o próprio perfil nem remover o último administrador.</p>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>Nome</th><th>E-mail</th><th>Perfil</th><th>Status</th><th>Ações</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <form id="settings-user-form" class="settings-form mt-16">
      <h3 class="drawer-subtitle">Novo usuário</h3>
      <div class="form-grid">
        <div><label for="settings-user-name" class="field-label">Nome</label>
          <input id="settings-user-name" class="login-input" required maxlength="120"></div>
        <div><label for="settings-user-email" class="field-label">E-mail</label>
          <input id="settings-user-email" class="login-input" type="email" required maxlength="254"></div>
        <div><label for="settings-user-password" class="field-label">Senha inicial</label>
          <input id="settings-user-password" class="login-input" type="password" required minlength="8" autocomplete="new-password"></div>
        <div><label for="settings-user-role" class="field-label">Perfil</label>
          <select id="settings-user-role" class="login-input">${options}</select></div>
      </div>
      <div class="settings-actions"><button class="btn-primary" type="submit">Criar usuário</button>
        <span id="settings-user-status" class="form-error" role="status" aria-live="polite"></span></div>
    </form>`);
  watchForm(document.getElementById('settings-user-form')).addEventListener('submit', async (event) => {
    event.preventDefault();
    const status = document.getElementById('settings-user-status');
    const button = event.currentTarget.querySelector('button[type="submit"]');
    if (button.disabled) return;
    button.disabled = true;
    try {
      await api(`/api/companies/${APP.company}/users`, {
        method: 'POST',
        body: JSON.stringify({
          name: document.getElementById('settings-user-name').value.trim(),
          email: document.getElementById('settings-user-email').value.trim(),
          password: document.getElementById('settings-user-password').value,
          role: document.getElementById('settings-user-role').value,
        }),
      });
      clearDirty();
      await renderUserSettings();
    } catch (error) {
      status.textContent = error.message;
      button.disabled = false;
    }
  });
  const byId = Object.fromEntries(users.map((user) => [String(user.id), user]));
  document.querySelectorAll('[data-user-edit]').forEach((button) => button.addEventListener('click', () =>
    openUserEditForm(byId[button.dataset.userEdit], roles, button)));
  document.querySelectorAll('[data-user-disable]').forEach((button) => button.addEventListener('click', () =>
    openUserDisableForm(byId[button.dataset.userDisable], button)));
}

function openUserEditForm(user, roles, trigger) {
  const roleOptions = roles.map((role) => ({value: role.id, label: SETTINGS_ROLE_LABELS[role.id] || role.id}));
  const drawer = openDrawer({
    title: `Editar ${user.name}`, trigger,
    body: `<form class="drawer-form" novalidate>
      ${formField('name', 'Nome', textInput(user.name, 120))}
      ${formField('role', 'Perfil', selectControl(roleOptions, user.role))}
      <p class="field-help">E-mail e senha são alterados pela própria pessoa em Minha conta.</p>
      ${formActions('Salvar alterações')}
    </form>`,
  });
  const form = drawer.dialog.querySelector('form');
  const ui = formUiFor(form);
  let busy = false;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy) return;
    const values = readFormValues(form);
    const name = String(values.name || '').trim();
    if (!name) return ui.showError('Informe o nome.', ['name']);
    busy = true;
    ui.setBusy(true);
    ui.clearError();
    try {
      if (name !== user.name) {
        const saved = await api(`/api/companies/${APP.company}/users/${user.id}`, {
          method: 'PATCH', body: JSON.stringify({expected_version: user.version, name}),
        });
        user.version = saved.version;
        user.name = saved.name;
      }
      if (values.role !== user.role) {
        await api(`/api/companies/${APP.company}/users/${user.id}/role`, {method: 'PUT', body: JSON.stringify({role: values.role})});
      }
      drawer.close();
      renderUserSettings();
    } catch (e) {
      ui.showError(e.message, mapServerFields(e.fields));
    } finally {
      busy = false;
      ui.setBusy(false);
    }
  });
}

function openUserDisableForm(user, trigger) {
  const drawer = openDrawer({
    title: 'Desativar usuário', trigger,
    body: `<form class="drawer-form" novalidate>
      ${summaryList([['Usuário', esc(user.name)], ['E-mail', esc(user.email)]])}
      <div class="story-box">Efeito: o acesso desta pessoa é bloqueado em todas as empresas e as sessões abertas são encerradas. Lançamentos e histórico permanecem.</div>
      ${formActions('Desativar usuário').replace('>Cancelar</button>', '>Voltar</button>')}
    </form>`,
  });
  const form = drawer.dialog.querySelector('form');
  const ui = formUiFor(form);
  let busy = false;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    ui.setBusy(true);
    try {
      await api(`/api/companies/${APP.company}/users/${user.id}`, {method: 'DELETE'});
      drawer.close();
      renderUserSettings();
    } catch (e) {
      ui.showError(e.message, []);
    } finally {
      busy = false;
      ui.setBusy(false);
    }
  });
}

const SETTINGS_ROLE_LABELS = {
  administrator: 'Administrador',
  partner: 'Sócio',
  manager: 'Gerente',
  viewer: 'Consulta',
};

function calendarRequest(values, editing) {
  const errors = {};
  if (!values.date) errors.date = 'Informe a data.';
  if (!['open', 'closed'].includes(values.status)) errors.status = 'Escolha se a loja abre ou fecha.';
  if (Object.keys(errors).length) return {errors};
  return {
    method: 'PUT', path: `/api/companies/${APP.company}/settings/calendar`,
    body: {date: values.date, status: values.status, description: String(values.description || '').trim(), expected_version: editing ? editing.version : null},
  };
}

function calendarDeletePath(entry) {
  return `/api/companies/${APP.company}/settings/calendar/${encodeURIComponent(entry.date)}?expected_version=${entry.version}`;
}

async function renderCalendarSettings(token) {
  token = token || beginPage();
  const entries = await api(`/api/companies/${APP.company}/settings/calendar`);
  if (!APP.pageState.isCurrent(token)) return;
  const rows = entries.length ? entries.map((entry) => `
    <tr><td>${dateBR(entry.date)}</td><td>${entry.status === 'closed' ? 'Fechado' : 'Aberto'}</td>
      <td>${esc(entry.description)}</td>
      <td><div class="row-actions">
        <button type="button" class="btn-secondary" data-calendar-edit="${esc(entry.date)}" aria-label="Editar ${dateBR(entry.date)}">Editar</button>
        <button type="button" class="btn-secondary" data-calendar-remove="${esc(entry.date)}" aria-label="Remover ${dateBR(entry.date)}">Remover</button>
      </div></td></tr>`).join('') :
    '<tr><td colspan="4">Nenhuma exceção cadastrada.</td></tr>';
  document.getElementById('content').innerHTML = settingsShell('calendario', `
    <h2>Calendário de operação</h2>
    <p>Cadastre feriados, fechamentos extraordinários ou dias com abertura excepcional.</p>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>Data</th><th>Operação</th><th>Motivo</th><th>Ações</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <form id="settings-calendar-form" class="settings-form mt-16" novalidate>
      <h3 class="drawer-subtitle" id="calendar-form-title">Adicionar data</h3>
      <div class="form-grid">
        <div><label for="settings-calendar-date" class="field-label">Data</label>
          <input id="settings-calendar-date" name="date" class="login-input" type="date" required></div>
        <div><label for="settings-calendar-status" class="field-label">Operação</label>
          <select id="settings-calendar-status" name="status" class="login-input"><option value="closed">Fechado</option><option value="open">Aberto</option></select></div>
        <div class="form-grid-wide"><label for="settings-calendar-description" class="field-label">Motivo</label>
          <input id="settings-calendar-description" name="description" class="login-input" maxlength="200"></div>
      </div>
      <div class="form-error" data-form-error role="alert"></div>
      <div class="settings-actions">
        <button class="btn-primary" type="submit">Salvar data</button>
        <button type="button" class="btn-secondary" data-calendar-cancel hidden>Cancelar edição</button>
      </div>
    </form>`);
  const form = document.getElementById('settings-calendar-form');
  const dateInputEl = document.getElementById('settings-calendar-date');
  const cancel = form.querySelector('[data-calendar-cancel]');
  let editing = null;
  const stopEditing = () => {
    editing = null;
    form.reset();
    dateInputEl.readOnly = false;
    cancel.hidden = true;
    document.getElementById('calendar-form-title').textContent = 'Adicionar data';
  };
  bindForm(form, (values) => calendarRequest(values, editing), () => renderCalendarSettings());
  cancel.addEventListener('click', () => { stopEditing(); clearDirty(); });
  const byDate = Object.fromEntries(entries.map((entry) => [entry.date, entry]));
  document.querySelectorAll('[data-calendar-edit]').forEach((button) => button.addEventListener('click', () => {
    editing = byDate[button.dataset.calendarEdit];
    dateInputEl.value = editing.date;
    dateInputEl.readOnly = true;  // the date identifies the exception; remove and add to move it
    document.getElementById('settings-calendar-status').value = editing.status;
    document.getElementById('settings-calendar-description').value = editing.description || '';
    document.getElementById('calendar-form-title').textContent = `Editar ${dateBR(editing.date)}`;
    cancel.hidden = false;
    document.getElementById('settings-calendar-status').focus();
  }));
  document.querySelectorAll('[data-calendar-remove]').forEach((button) => button.addEventListener('click', () => {
    const entry = byDate[button.dataset.calendarRemove];
    const drawer = openDrawer({
      title: 'Remover data do calendário', trigger: button,
      body: `<form class="drawer-form" novalidate>
        ${summaryList([['Data', dateBR(entry.date)], ['Operação', entry.status === 'closed' ? 'Fechado' : 'Aberto'], ['Motivo', esc(entry.description || '—')]])}
        <div class="story-box">Efeito: a data volta a seguir o horário normal da loja. As demais datas não mudam.</div>
        <div class="form-error" data-form-error role="alert"></div>
        <div class="btn-row drawer-actions">
          <button type="submit" class="btn-primary btn-wide">Remover data</button>
          <button type="button" class="btn-secondary" data-drawer-close>Voltar</button>
        </div>
      </form>`,
    });
    const confirmForm = drawer.dialog.querySelector('form');
    const ui = formUiFor(confirmForm);
    let busy = false;
    confirmForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (busy) return;
      busy = true;
      ui.setBusy(true);
      try {
        await api(calendarDeletePath(entry), {method: 'DELETE'});
        drawer.close();
        renderCalendarSettings();
      } catch (e) {
        ui.showError(e.message, []);
      } finally {
        busy = false;
        ui.setBusy(false);
      }
    });
  }));
}

function renderPlannedSettings(section) {
  const descriptions = {
    alertas: 'Preferências, limites e destinatários dos alertas serão configurados aqui.',
  };
  document.getElementById('content').innerHTML = settingsShell(
    section,
    `<h2>${esc(SETTINGS_LABELS[section])}</h2><div class="story-box">${esc(descriptions[section])}</div>`
  );
}

async function renderIntegrationsSettings(token) {
  token = token || beginPage();
  let connections = [], cashAccounts = [], stoneError = null;
  try {
    [connections, cashAccounts] = await Promise.all([
      api(`/api/companies/${APP.company}/finance/open-finance/connections`),
      api(`/api/companies/${APP.company}/finance/cash-accounts`),
    ]);
  } catch (e) {
    stoneError = e;  // Stone unavailable (permission, outage) must not hide the Mobne sync panel
  }
  if (!APP.pageState.isCurrent(token)) return;
  const rows = connections.map((c) => `
    <tr>
      <td>${esc(c.provider)}</td>
      <td>${esc(c.status)}</td>
      <td>${c.last_balance_cents == null ? '—' : money(c.last_balance_cents)}</td>
      <td>${c.last_synced_at ? dt(c.last_synced_at) : '—'}</td>
      <td>${esc(c.last_error || '')}</td>
      <td>${c.status !== 'revoked' ? `
        <button type="button" class="btn-secondary" data-of-sync="${esc(c.id)}">Sincronizar</button>
        <button type="button" class="btn-secondary" data-of-revoke="${esc(c.id)}">Revogar</button>` : ''}</td>
    </tr>`).join('');
  const accountOptions = cashAccounts.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');

  document.getElementById('content').innerHTML = settingsShell('integracoes', `
    <div id="sync-action-status" class="form-success" role="status" aria-live="polite"></div>
    <section id="sync-panel" aria-labelledby="sync-title">${syncPanelHtml(APP.status)}</section>
    <section class="settings-block" aria-labelledby="stone-title">
    <div class="settings-heading">
      <div><h2 id="stone-title">Stone Open Finance</h2><p>Conecta uma conta de pagamento Stone para sincronizar saldo e extrato automaticamente.</p></div>
    </div>
    ${stoneError ? `<div class="story-box">Não foi possível carregar a integração Stone: ${esc(stoneError.message)}</div>` : ''}
    <div class="table-wrap"><table>
      <thead><tr><th>Provedor</th><th>Status</th><th>Último saldo</th><th>Última sincronização</th><th>Erro</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6">Nenhuma conexão configurada.</td></tr>'}</tbody>
    </table></div>
    <form id="of-consent-form" class="inline-form">
      <select id="of-cash-account" class="login-input" required>${accountOptions || '<option value="">Cadastre uma conta de caixa primeiro</option>'}</select>
      <button type="submit" class="btn-primary">Conectar conta Stone</button>
      <span id="of-consent-status" class="sim-status" role="status" aria-live="polite"></span>
    </form>
    <div id="of-consent-result"></div>
    </section>`);

  watchForm(document.getElementById('of-consent-form')).addEventListener('submit', onStartStoneConsent);
  document.querySelectorAll('[data-of-sync]').forEach((btn) => btn.addEventListener('click', onSyncStoneConnection));
  document.querySelectorAll('[data-of-revoke]').forEach((btn) => btn.addEventListener('click', onRevokeStoneConnection));
}

async function onStartStoneConsent(event) {
  event.preventDefault();
  const status = document.getElementById('of-consent-status');
  const result = document.getElementById('of-consent-result');
  status.textContent = 'Gerando link de consentimento…';
  result.innerHTML = '';
  try {
    const started = await api(`/api/companies/${APP.company}/finance/open-finance/consent`, {
      method: 'POST',
      body: JSON.stringify({
        cash_account_id: document.getElementById('of-cash-account').value,
        return_url: location.origin,
      }),
    });
    status.textContent = '';
    clearDirty();
    result.innerHTML = `<div class="story-box">Abra o link para autorizar na Stone: <a href="${esc(started.consent_url)}" target="_blank" rel="noopener">continuar na Stone</a>. Expira em ${dt(started.expires_at)}.</div>`;
  } catch (e) {
    status.textContent = 'Erro: ' + e.message;
  }
}

async function onSyncStoneConnection(event) {
  const connectionId = event.currentTarget.dataset.ofSync;
  try {
    await api(`/api/companies/${APP.company}/finance/open-finance/connections/${connectionId}/sync`, {method: 'POST'});
    renderIntegrationsSettings();
  } catch (e) {
    document.getElementById('of-consent-status').textContent = 'Não foi possível sincronizar a Stone: ' + e.message;
  }
}

async function onRevokeStoneConnection(event) {
  const connectionId = event.currentTarget.dataset.ofRevoke;
  try {
    await api(`/api/companies/${APP.company}/finance/open-finance/connections/${connectionId}/revoke`, {method: 'POST'});
    renderIntegrationsSettings();
  } catch (e) {
    document.getElementById('of-consent-status').textContent = 'Não foi possível revogar a conexão: ' + e.message;
  }
}

async function renderGoalsSettings(token) {
  token = token || beginPage();
  const progress = await api(`/api/companies/${APP.company}/goals/progress`);
  if (!APP.pageState.isCurrent(token)) return;  // resposta obsoleta: descarta em silêncio
  document.getElementById('content').innerHTML = settingsShell('metas', `
    <div class="settings-heading">
      <div><h2>Metas de faturamento e margem</h2><p>Usadas no Resumo executivo e em Projeções de vendas.</p></div>
    </div>
    ${progress ? `
      <div class="kpi-grid kpi-grid-3">
        ${kpi('Meta do mês', money(progress.target_cents))}
        ${kpi('Realizado', money(progress.achieved_cents))}
        ${kpi('Necessário/dia útil restante', progress.required_per_day == null ? '—' : money(progress.required_per_day))}
      </div>` : '<div class="story-box">Nenhuma meta de faturamento configurada para o período atual.</div>'}
    <form id="goals-form" class="settings-form">
      <div class="form-grid">
        <div><label class="field-label" for="goal-revenue">Meta de faturamento mensal (R$)</label>
          <input id="goal-revenue" class="login-input" type="number" min="0" step="0.01"></div>
        <div><label class="field-label" for="goal-margin">Meta de margem real após custo fixo (%)</label>
          <input id="goal-margin" class="login-input" type="number" min="0" max="100" step="0.1"></div>
        <div><label class="field-label" for="goal-effective-from">Válida a partir de</label>
          <input id="goal-effective-from" class="login-input" type="date" value="${new Date().toISOString().slice(0, 10)}"></div>
      </div>
      <div class="settings-actions">
        <button class="btn-primary" type="submit">Salvar metas</button>
        <span id="goals-status" class="sim-status" role="status" aria-live="polite"></span>
      </div>
    </form>`);
  watchForm(document.getElementById('goals-form')).addEventListener('submit', onSaveGoals);
}

async function onSaveGoals(event) {
  event.preventDefault();
  const status = document.getElementById('goals-status');
  status.textContent = 'Salvando…';
  const effectiveFrom = document.getElementById('goal-effective-from').value;
  const revenue = document.getElementById('goal-revenue').value;
  const margin = document.getElementById('goal-margin').value;
  try {
    if (revenue) {
      await api(`/api/companies/${APP.company}/goals`, {
        method: 'PUT',
        body: JSON.stringify({key: 'revenue', value: Math.round(parseFloat(revenue.replace(',', '.')) * 100), effective_from: effectiveFrom}),
      });
    }
    if (margin) {
      await api(`/api/companies/${APP.company}/goals`, {
        method: 'PUT',
        body: JSON.stringify({key: 'margin', value: parseFloat(margin.replace(',', '.')), effective_from: effectiveFrom}),
      });
    }
    clearDirty();
    renderGoalsSettings();
  } catch (e) {
    status.textContent = 'Erro: ' + e.message;
  }
}

/* ---------------------------------------------------------------- Mobne sync (Integrações) */

function jobBadge(state) {
  const map = {queued: 'badge-info', running: 'badge-info', completed: 'badge-success',
    completed_with_errors: 'badge-warning', failed: 'badge-error'};
  const label = {queued: 'Na fila', running: 'Em execução', completed: 'Concluído',
    completed_with_errors: 'Concluído com falhas', failed: 'Falhou'};
  return `<span class="${map[state] || 'badge-muted'}">${label[state] || state}</span>`;
}

const SYNC_MODE_LABELS = {recent: 'Recente', history: 'Histórico completo', reconcile: 'Reconciliação completa', month: 'Mês específico'};

/* The daily task is one button; the diagnosis (catalogs, periods, every run)
 * stays one click away under "Opções avançadas". Progress shows the job's own
 * counters as reported — never capped on screen to hide a miscount. */
function syncPanelHtml(s) {
  s = s || {};
  const jobs = s.jobs || [];
  const active = jobs.find((j) => j.state === 'queued' || j.state === 'running');
  const last = jobs.find((j) => j.state !== 'queued' && j.state !== 'running');
  const latest = (s.periods || [])[0];
  const catalogs = s.catalogs || {};
  const catalogLabels = {categories: 'Categorias', products: 'Produtos', stock: 'Estoque', prices: 'Preços'};
  const catalogRows = Object.keys(catalogLabels).map((key) => {
    const c = catalogs[key];
    return `<tr><td>${catalogLabels[key]}</td><td class="num">${c ? num(c.count) : '—'}</td><td>${c ? dt(c.updated_at) : 'Não sincronizado'}</td></tr>`;
  }).join('');
  const progress = active ? `
    <div class="sync-progress" role="status">
      <div><strong>${SYNC_MODE_LABELS[active.mode] || esc(active.mode)}</strong> ${jobBadge(active.state)}</div>
      ${active.total ? `<progress max="${active.total}" value="${active.completed}" aria-label="Progresso da sincronização"></progress>
        <div class="muted">${active.completed} de ${active.total} etapas</div>` : ''}
      <div class="job-detail">${esc(active.detail || 'Aguardando o início…')}</div>
    </div>` : '';
  const lastRun = last ? `
    <p>Última execução: ${jobBadge(last.state)} ${dt(last.updated_at)}${last.detail ? ` — ${esc(last.detail)}` : ''}</p>
    ${last.error ? `<div class="job-error">${esc(last.error)}</div>` : ''}` : '<p>Nenhuma execução ainda.</p>';
  const disabled = active ? ' disabled' : '';
  return `
    <div class="settings-heading">
      <div><h2 id="sync-title">Mobne — vendas, produtos e estoque</h2>
        <p>${esc(s.source || 'Mobne')} · atualização automática a cada ${s.interval_minutes || '—'} min${latest ? ` · último período salvo: ${esc(latest.period)} em ${dt(latest.updated_at)}` : ''}</p></div>
    </div>
    ${progress}
    ${lastRun}
    <div class="btn-row"><button type="button" class="btn-primary btn-wide" data-sync="recent"${disabled}>Sincronizar agora</button></div>
    <details class="form-details">
      <summary>Opções avançadas e diagnóstico</summary>
      <div class="btn-row">
        <button type="button" class="btn-secondary" data-sync="reconcile"${disabled}>Reconciliar todo o histórico</button>
        <button type="button" class="btn-secondary" data-sync="history"${disabled}>Carregar histórico completo</button>
      </div>
      <h3 class="drawer-subtitle">Catálogos</h3>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>Base</th><th class="num">Itens</th><th>Atualizado</th></tr></thead>
        <tbody>${catalogRows}</tbody></table></div>
      <h3 class="drawer-subtitle">Períodos de vendas sincronizados</h3>
      <div class="table-wrap table-scroll-md"><table class="data-table"><thead><tr><th>Período</th><th>Documentos</th><th>Atualizado</th><th>Versão</th></tr></thead>
        <tbody>${(s.periods || []).map((p) => `<tr><td>${esc(p.period)}</td>
          <td>${p.documents ? num(p.documents) : 'Sem vendas no Mobne'}</td>
          <td>${dt(p.updated_at)}</td><td>${p.version}</td></tr>`).join('') || '<tr><td colspan="4">Nenhum período ainda.</td></tr>'}</tbody></table></div>
      <h3 class="drawer-subtitle">Execuções recentes</h3>
      <div class="jobs-list">
        ${jobs.map((j) => `<div class="job-row">
            <div><strong>${SYNC_MODE_LABELS[j.mode] || esc(j.mode)}</strong> ${jobBadge(j.state)}
              <div class="job-detail">${esc(j.detail || '')}</div>
              ${j.error ? `<div class="job-error">${esc(j.error)}</div>` : ''}
            </div>
            <div class="job-detail">${dt(j.updated_at)}${j.total ? ` · ${j.completed}/${j.total}` : ''}</div>
          </div>`).join('') || '<div class="story-box">Nenhuma execução ainda.</div>'}
      </div>
    </details>`;
}

// Repaints only the sync panel (status polling), keeping the Stone form intact.
function refreshSyncPanel() {
  const panel = document.getElementById('sync-panel');
  if (!panel) return;
  const details = panel.querySelector('details');
  const wasOpen = !!(details && details.open);
  panel.innerHTML = syncPanelHtml(APP.status);
  if (wasOpen) panel.querySelector('details').open = true;
}

/* ---------------------------------------------------------------- Categorias e favorecidos */

var CATALOG_NATURE_LABELS = {
  operating_expense: 'Despesa operacional', tax_expense: 'Impostos', financial_expense: 'Despesa financeira',
  cost_of_goods: 'Custo de mercadoria', profit_distribution: 'Distribuição de lucros', financing_inflow: 'Entrada de financiamento',
  loan_principal: 'Amortização de principal', transfer: 'Transferência', revenue: 'Receita',
};

async function renderCatalogSettings(token) {
  token = token || beginPage();
  const base = `/api/companies/${APP.company}/finance`;
  const [categories, counterparties] = await Promise.all([
    api(`${base}/accounts?include_archived=true`),
    api(`${base}/counterparties`),
  ]);
  if (!APP.pageState.isCurrent(token)) return;
  const categoryRows = categories.map((c) => `
    <tr>
      <td>${esc(c.name)}<div class="muted">${esc(c.code)}</div></td>
      <td>${esc(CATALOG_NATURE_LABELS[c.nature] || c.nature)}</td>
      <td>${c.archived ? '<span class="badge-muted">Arquivada</span>' : '<span class="badge-success">Ativa</span>'}</td>
      <td><div class="row-actions">
        <button type="button" class="btn-secondary" data-category-rename="${esc(c.id)}" aria-label="Renomear ${esc(c.name)}">Renomear</button>
        ${c.system_key ? '<span class="muted">Padrão do sistema</span>'
          : `<button type="button" class="btn-secondary" data-category-archive="${esc(c.id)}" aria-label="${c.archived ? 'Reativar' : 'Arquivar'} ${esc(c.name)}">${c.archived ? 'Reativar' : 'Arquivar'}</button>`}
      </div></td>
    </tr>`).join('');
  const counterpartyRows = counterparties.map((c) => `
    <tr>
      <td>${esc(c.name)}</td>
      <td>${esc(COUNTERPARTY_KIND_LABELS[c.kind] || c.kind)}</td>
      <td>${esc(c.document || '—')}</td>
      <td><div class="row-actions">
        <button type="button" class="btn-secondary" data-counterparty-rename="${esc(c.id)}" aria-label="Renomear ${esc(c.name)}">Renomear</button>
        <button type="button" class="btn-secondary" data-counterparty-archive="${esc(c.id)}" aria-label="Arquivar ${esc(c.name)}">Arquivar</button>
      </div></td>
    </tr>`).join('');
  const natureOptions = ['operating_expense', 'tax_expense', 'financial_expense']
    .map((value) => ({value, label: CATALOG_NATURE_LABELS[value]}));
  const kindOptions = Object.entries(COUNTERPARTY_KIND_LABELS).map(([value, label]) => ({value, label}));

  document.getElementById('content').innerHTML = settingsShell('cadastros', `
    <section aria-labelledby="catalog-categories-title">
      <h2 id="catalog-categories-title">Categorias da despesa</h2>
      <p class="field-help">Arquivar tira a categoria de novos lançamentos; relatórios e lançamentos antigos a mantêm. Categorias padrão só podem ser renomeadas.</p>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th>Categoria</th><th>Natureza</th><th>Status</th><th>Ações</th></tr></thead>
        <tbody>${categoryRows || '<tr><td colspan="4">Nenhuma categoria.</td></tr>'}</tbody>
      </table></div>
      <form id="catalog-category-form" class="settings-form" novalidate>
        <h3 class="drawer-subtitle">Nova categoria</h3>
        <div class="form-grid">
          ${formField('name', 'Nome', textInput('', 160))}
          ${formField('nature', 'Natureza', selectControl(natureOptions, 'operating_expense'))}
        </div>
        <div class="form-error" data-form-error role="alert"></div>
        <div class="settings-actions"><button class="btn-primary" type="submit">Adicionar categoria</button></div>
      </form>
    </section>
    <section class="settings-block" aria-labelledby="catalog-counterparties-title">
      <h2 id="catalog-counterparties-title">Fornecedores e favorecidos</h2>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th>Nome</th><th>Tipo</th><th>Documento</th><th>Ações</th></tr></thead>
        <tbody>${counterpartyRows || '<tr><td colspan="4">Nenhum favorecido cadastrado.</td></tr>'}</tbody>
      </table></div>
      <form id="catalog-counterparty-form" class="settings-form" novalidate>
        <h3 class="drawer-subtitle">Novo favorecido</h3>
        <div class="form-grid">
          ${formField('name', 'Nome', textInput('', 180))}
          ${formField('kind', 'Tipo', selectControl(kindOptions, 'supplier'))}
          ${formField('document', 'CPF ou CNPJ (opcional)', textInput('', 30))}
        </div>
        <div class="form-error" data-form-error role="alert"></div>
        <div class="settings-actions"><button class="btn-primary" type="submit">Adicionar favorecido</button></div>
      </form>
    </section>`);

  const refresh = () => renderCatalogSettings();
  bindForm(document.getElementById('catalog-category-form'), (values) => {
    const name = String(values.name || '').trim();
    if (!name) return {errors: {name: 'Informe o nome da categoria.'}};
    return {method: 'POST', path: `${base}/accounts`, body: {name, nature: values.nature}};
  }, refresh);
  bindForm(document.getElementById('catalog-counterparty-form'), (values) => buildCounterpartyRequest(values), refresh);

  const categoriesById = Object.fromEntries(categories.map((c) => [String(c.id), c]));
  const counterpartiesById = Object.fromEntries(counterparties.map((c) => [String(c.id), c]));
  document.querySelectorAll('[data-category-rename]').forEach((button) => button.addEventListener('click', () =>
    openRenameForm('category', categoriesById[button.dataset.categoryRename], {trigger: button, onSaved: refresh})));
  document.querySelectorAll('[data-category-archive]').forEach((button) => button.addEventListener('click', () => {
    const category = categoriesById[button.dataset.categoryArchive];
    openArchiveForm('category', category, !category.archived, {trigger: button, onSaved: refresh});
  }));
  document.querySelectorAll('[data-counterparty-rename]').forEach((button) => button.addEventListener('click', () =>
    openRenameForm('counterparty', counterpartiesById[button.dataset.counterpartyRename], {trigger: button, onSaved: refresh})));
  document.querySelectorAll('[data-counterparty-archive]').forEach((button) => button.addEventListener('click', () =>
    openArchiveForm('counterparty', counterpartiesById[button.dataset.counterpartyArchive], true, {trigger: button, onSaved: refresh})));
}

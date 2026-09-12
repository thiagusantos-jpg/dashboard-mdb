/* Administration settings pages. Loaded before app.js and uses its shared
 * api(), esc(), APP and renderEmptyState() globals only when a route opens. */
'use strict';

const SETTINGS_LABELS = {
  empresa: 'Empresa e lojas',
  usuarios: 'Usuários',
  calendario: 'Calendário',
  metas: 'Metas',
  alertas: 'Alertas',
  integracoes: 'Integrações',
};

function settingsShell(section, body) {
  const tabs = Object.entries(SETTINGS_LABELS).map(([id, label]) =>
    `<a class="settings-tab ${id === section ? 'active' : ''}"
       href="#/configuracoes/${id}" ${id === section ? 'aria-current="page"' : ''}>${label}</a>`
  ).join('');
  return `
    <div class="page-title">⚙️ Configurações</div>
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
    if (section === 'calendario') return await renderCalendarSettings(token);
    if (section === 'integracoes') return await renderIntegrationsSettings(token);
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
  const [profile, stores] = await Promise.all([
    api(`/api/companies/${APP.company}/settings/company`),
    api(`/api/companies/${APP.company}/settings/stores`),
  ]);
  if (!APP.pageState.isCurrent(token)) return;  // resposta obsoleta: descarta em silêncio
  const address = profile.address || {};
  const contacts = profile.contacts || {};
  const storeRows = stores.length ? stores.map((store) => `
    <tr>
      <td>${esc(store.name)}</td>
      <td>${store.mobne_id == null ? 'Local' : 'Mobne ' + esc(store.mobne_id)}</td>
      <td>${esc(store.timezone)}</td>
      <td>${store.active ? 'Ativa' : 'Inativa'}</td>
    </tr>`).join('') : '<tr><td colspan="4">Nenhuma loja local cadastrada.</td></tr>';

  document.getElementById('content').innerHTML = settingsShell('empresa', `
    <form id="company-settings-form" class="settings-form">
      <div class="settings-heading">
        <div><h2>Dados cadastrais</h2><p>O ID da Mobne é preservado e não pode ser editado.</p></div>
        <span class="periodo-badge muted">Mobne ID ${esc(APP.company)}</span>
      </div>
      <div class="form-grid">
        <div>
          <label for="settings-legal-name" class="field-label">Razão social</label>
          <input id="settings-legal-name" class="login-input" maxlength="180" value="${esc(profile.legal_name)}">
        </div>
        <div>
          <label for="settings-trade-name" class="field-label">Nome fantasia</label>
          <input id="settings-trade-name" class="login-input" maxlength="180" value="${esc(profile.trade_name)}">
        </div>
        <div>
          <label for="settings-cnpj" class="field-label">CNPJ</label>
          <input id="settings-cnpj" class="login-input" maxlength="18" inputmode="numeric" value="${esc(profile.cnpj)}">
        </div>
        <div>
          <label for="settings-logo-url" class="field-label">URL do logotipo</label>
          <input id="settings-logo-url" class="login-input" maxlength="500" value="${esc(profile.logo_url)}">
        </div>
        <div class="form-grid-wide">
          <label for="settings-address" class="field-label">Endereço</label>
          <input id="settings-address" class="login-input" maxlength="250" value="${esc(address.line || '')}">
        </div>
        <div>
          <label for="settings-city" class="field-label">Cidade</label>
          <input id="settings-city" class="login-input" maxlength="100" value="${esc(address.city || '')}">
        </div>
        <div>
          <label for="settings-state" class="field-label">Estado</label>
          <input id="settings-state" class="login-input" maxlength="2" value="${esc(address.state || '')}">
        </div>
        <div>
          <label for="settings-phone" class="field-label">Telefone</label>
          <input id="settings-phone" class="login-input" autocomplete="tel" value="${esc(contacts.phone || '')}">
        </div>
        <div>
          <label for="settings-contact-email" class="field-label">E-mail da empresa</label>
          <input id="settings-contact-email" class="login-input" type="email" autocomplete="email" value="${esc(contacts.email || '')}">
        </div>
      </div>
      <div class="settings-actions">
        <button class="btn-primary" type="submit">Salvar dados</button>
        <span id="company-settings-status" class="sim-status" role="status" aria-live="polite"></span>
      </div>
    </form>

    <div class="settings-block">
      <h2>Lojas</h2>
      <div class="table-wrap"><table><thead><tr><th>Nome</th><th>Origem</th><th>Fuso</th><th>Status</th></tr></thead>
        <tbody>${storeRows}</tbody></table></div>
      <form id="store-create-form" class="inline-form">
        <div><label for="settings-store-name" class="field-label">Nova loja</label>
          <input id="settings-store-name" class="login-input" maxlength="180" required></div>
        <button class="btn-secondary" type="submit">Adicionar loja</button>
        <span id="store-create-status" class="sim-status" role="status" aria-live="polite"></span>
      </form>
    </div>`);

  watchForm(document.getElementById('company-settings-form')).addEventListener('submit', async (event) => {
    event.preventDefault();
    const status = document.getElementById('company-settings-status');
    status.textContent = 'Salvando…';
    try {
      const saved = await api(`/api/companies/${APP.company}/settings/company`, {
        method: 'PUT',
        body: JSON.stringify({
          expected_version: profile.version,
          legal_name: document.getElementById('settings-legal-name').value.trim(),
          trade_name: document.getElementById('settings-trade-name').value.trim(),
          cnpj: document.getElementById('settings-cnpj').value.trim(),
          logo_url: document.getElementById('settings-logo-url').value.trim(),
          address: {
            line: document.getElementById('settings-address').value.trim(),
            city: document.getElementById('settings-city').value.trim(),
            state: document.getElementById('settings-state').value.trim().toUpperCase(),
          },
          contacts: {
            phone: document.getElementById('settings-phone').value.trim(),
            email: document.getElementById('settings-contact-email').value.trim(),
          },
        }),
      });
      profile.version = saved.version;
      clearDirty();
      status.className = 'sim-status ok';
      status.textContent = 'Dados salvos.';
    } catch (error) {
      status.className = 'sim-status error';
      status.textContent = error.message;
    }
  });

  watchForm(document.getElementById('store-create-form')).addEventListener('submit', async (event) => {
    event.preventDefault();
    const status = document.getElementById('store-create-status');
    status.textContent = 'Salvando…';
    try {
      await api(`/api/companies/${APP.company}/settings/stores`, {
        method: 'POST',
        body: JSON.stringify({name: document.getElementById('settings-store-name').value.trim()}),
      });
      clearDirty();
      await renderCompanySettings();
    } catch (error) {
      status.className = 'sim-status error';
      status.textContent = error.message;
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
      <td>${user.active ? 'Ativo' : 'Inativo'}</td></tr>`).join('') :
    '<tr><td colspan="4">Nenhum usuário vinculado.</td></tr>';
  const options = roles.map((role) =>
    `<option value="${esc(role.id)}">${esc(SETTINGS_ROLE_LABELS[role.id] || role.id)}</option>`
  ).join('');
  document.getElementById('content').innerHTML = settingsShell('usuarios', `
    <h2>Usuários e perfis</h2>
    <div class="table-wrap"><table><thead><tr><th>Nome</th><th>E-mail</th><th>Perfil</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <form id="settings-user-form" class="settings-form mt-16">
      <div class="form-grid">
        <div><label for="settings-user-name" class="field-label">Nome</label>
          <input id="settings-user-name" class="login-input" required maxlength="120"></div>
        <div><label for="settings-user-email" class="field-label">E-mail</label>
          <input id="settings-user-email" class="login-input" type="email" required maxlength="254"></div>
        <div><label for="settings-user-password" class="field-label">Senha inicial</label>
          <input id="settings-user-password" class="login-input" type="password" required minlength="8"></div>
        <div><label for="settings-user-role" class="field-label">Perfil</label>
          <select id="settings-user-role" class="login-input">${options}</select></div>
      </div>
      <div class="settings-actions"><button class="btn-primary" type="submit">Criar usuário</button>
        <span id="settings-user-status" class="sim-status" role="status" aria-live="polite"></span></div>
    </form>`);
  watchForm(document.getElementById('settings-user-form')).addEventListener('submit', async (event) => {
    event.preventDefault();
    const status = document.getElementById('settings-user-status');
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
      status.className = 'sim-status error';
      status.textContent = error.message;
    }
  });
}

const SETTINGS_ROLE_LABELS = {
  administrator: 'Administrador',
  partner: 'Sócio',
  manager: 'Gerente',
  viewer: 'Consulta',
};

async function renderCalendarSettings(token) {
  token = token || beginPage();
  const entries = await api(`/api/companies/${APP.company}/settings/calendar`);
  if (!APP.pageState.isCurrent(token)) return;
  const rows = entries.length ? entries.map((entry) => `
    <tr><td>${esc(entry.date)}</td><td>${entry.status === 'closed' ? 'Fechado' : 'Aberto'}</td>
      <td>${esc(entry.description)}</td><td>v${entry.version}</td></tr>`).join('') :
    '<tr><td colspan="4">Nenhuma exceção cadastrada.</td></tr>';
  document.getElementById('content').innerHTML = settingsShell('calendario', `
    <h2>Calendário de operação</h2>
    <p>Cadastre feriados, fechamentos extraordinários ou dias com abertura excepcional.</p>
    <div class="table-wrap"><table><thead><tr><th>Data</th><th>Operação</th><th>Motivo</th><th>Versão</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <form id="settings-calendar-form" class="inline-form mt-16">
      <div><label for="settings-calendar-date" class="field-label">Data</label>
        <input id="settings-calendar-date" class="login-input" type="date" required></div>
      <div><label for="settings-calendar-status" class="field-label">Operação</label>
        <select id="settings-calendar-status" class="login-input"><option value="closed">Fechado</option><option value="open">Aberto</option></select></div>
      <div><label for="settings-calendar-description" class="field-label">Motivo</label>
        <input id="settings-calendar-description" class="login-input" maxlength="200"></div>
      <button class="btn-secondary" type="submit">Adicionar data</button>
      <span id="settings-calendar-message" class="sim-status" role="status" aria-live="polite"></span>
    </form>`);
  watchForm(document.getElementById('settings-calendar-form')).addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = document.getElementById('settings-calendar-message');
    try {
      await api(`/api/companies/${APP.company}/settings/calendar`, {
        method: 'PUT',
        body: JSON.stringify({
          date: document.getElementById('settings-calendar-date').value,
          status: document.getElementById('settings-calendar-status').value,
          description: document.getElementById('settings-calendar-description').value.trim(),
          expected_version: null,
        }),
      });
      clearDirty();
      await renderCalendarSettings();
    } catch (error) {
      message.className = 'sim-status error';
      message.textContent = error.message;
    }
  });
}

function renderPlannedSettings(section) {
  const descriptions = {
    metas: 'Metas de faturamento, margem e resultado serão configuradas aqui.',
    alertas: 'Preferências, limites e destinatários dos alertas serão configurados aqui.',
  };
  document.getElementById('content').innerHTML = settingsShell(
    section,
    `<h2>${esc(SETTINGS_LABELS[section])}</h2><div class="story-box">${esc(descriptions[section])}</div>`
  );
}

async function renderIntegrationsSettings(token) {
  token = token || beginPage();
  const [connections, cashAccounts] = await Promise.all([
    api(`/api/companies/${APP.company}/finance/open-finance/connections`),
    api(`/api/companies/${APP.company}/finance/cash-accounts`),
  ]);
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
    <div class="settings-heading">
      <div><h2>Stone Open Finance</h2><p>Conecta uma conta de pagamento Stone para sincronizar saldo e extrato automaticamente.</p></div>
    </div>
    <div class="table-wrap"><table>
      <thead><tr><th>Provedor</th><th>Status</th><th>Último saldo</th><th>Última sincronização</th><th>Erro</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6">Nenhuma conexão configurada.</td></tr>'}</tbody>
    </table></div>
    <form id="of-consent-form" class="inline-form">
      <select id="of-cash-account" class="login-input" required>${accountOptions || '<option value="">Cadastre uma conta de caixa primeiro</option>'}</select>
      <button type="submit" class="btn-primary">Conectar conta Stone</button>
      <span id="of-consent-status" class="sim-status" role="status" aria-live="polite"></span>
    </form>
    <div id="of-consent-result"></div>`);

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
    alert('Erro ao sincronizar: ' + e.message);
  }
}

async function onRevokeStoneConnection(event) {
  const connectionId = event.currentTarget.dataset.ofRevoke;
  try {
    await api(`/api/companies/${APP.company}/finance/open-finance/connections/${connectionId}/revoke`, {method: 'POST'});
    renderIntegrationsSettings();
  } catch (e) {
    alert('Erro ao revogar: ' + e.message);
  }
}

/* Administration settings pages. Loaded before app.js and uses its shared
 * api(), esc(), APP and renderEmptyState() globals only when a route opens. */
'use strict';

const SETTINGS_LABELS = {
  empresa: 'Empresa e lojas',
  usuarios: 'Usuários',
  calendario: 'Calendário',
  metas: 'Metas',
  alertas: 'Alertas',
  integracoes: 'Integrações e sincronização',
};

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

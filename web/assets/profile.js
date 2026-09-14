/* Minha conta (task C1): the signed-in user edits their own name, e-mail and
 * password. Uses the native <dialog> (focus containment, Escape and an inert
 * background for free) and app.js's api()/APP. Passwords live only in the
 * input fields: never in APP, storage or the URL, and are wiped on close.
 * `var` (not const) so the state is reachable from the vm-based unit tests. */
'use strict';

var PROFILE_PASSWORD_INPUTS = ['profile-current-password', 'profile-new-password', 'profile-confirm-password', 'profile-email-password'];
var PROFILE_FIELD_IDS = {name: 'profile-name', email: 'profile-email', current_password: 'profile-email-password'};
var PASSWORD_FIELD_IDS = {current_password: 'profile-current-password', new_password: 'profile-new-password'};

var profileState = {user: null, savingProfile: false, savingPassword: false};

function profileEl(id) { return document.getElementById(id); }

function buildProfileBody(original, values) {
  const email = String(values.email || '').trim().toLowerCase();
  const body = {name: String(values.name || '').trim(), email, expected_version: original.version};
  if (email !== original.email) body.current_password = values.currentPassword || '';
  return body;
}

function validateNewPassword(password, confirmation) {
  if (password.length < 8) return 'A nova senha deve ter pelo menos 8 caracteres.';
  if (password.length > 200) return 'A nova senha deve ter no máximo 200 caracteres.';
  if (password !== confirmation) return 'As senhas não conferem.';
  return null;
}

function markInvalid(fieldIds, fields) {
  Object.keys(fieldIds).forEach((field) => {
    const input = profileEl(fieldIds[field]);
    if (!input) return;
    if ((fields || []).includes(field)) input.setAttribute('aria-invalid', 'true');
    else input.setAttribute('aria-invalid', 'false');
  });
}

function togglePasswordVisibility(button) {
  const input = profileEl(button.dataset.target);
  const show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  button.setAttribute('aria-pressed', String(show));
  button.textContent = show ? 'Ocultar' : 'Mostrar';
}

function clearProfilePasswords() {
  PROFILE_PASSWORD_INPUTS.forEach((id) => {
    const input = profileEl(id);
    if (!input) return;
    input.value = '';
    input.type = 'password';
    input.setAttribute('aria-invalid', 'false');
  });
  document.querySelectorAll('[data-password-toggle]').forEach((button) => {
    button.setAttribute('aria-pressed', 'false');
    button.textContent = 'Mostrar';
  });
}

function syncEmailPasswordField() {
  const user = profileState.user;
  const changed = !!user && profileEl('profile-email').value.trim().toLowerCase() !== user.email;
  profileEl('profile-email-password-group').hidden = !changed;
  profileEl('profile-email-password').required = changed;
}

function clearProfileMessages() {
  ['profile-error', 'profile-success', 'profile-password-error', 'profile-password-success']
    .forEach((id) => { profileEl(id).textContent = ''; });
}

async function openProfile(section) {
  const dialog = profileEl('profile-dialog');
  clearProfileMessages();
  clearProfilePasswords();
  profileEl('profile-load-error').textContent = '';
  if (!dialog.open) dialog.showModal();
  try {
    profileState.user = await api('/api/me');
  } catch (e) {
    profileEl('profile-load-error').textContent = e.message;
    return;
  }
  profileEl('profile-name').value = profileState.user.name;
  profileEl('profile-email').value = profileState.user.email;
  syncEmailPasswordField();
  profileEl(section === 'password' ? 'profile-current-password' : 'profile-name').focus();
}

function closeProfile() {
  clearProfilePasswords();
  const dialog = profileEl('profile-dialog');
  if (dialog.open) dialog.close();
}

async function submitProfile(ev) {
  ev.preventDefault();
  if (profileState.savingProfile || !profileState.user) return;
  const error = profileEl('profile-error');
  const success = profileEl('profile-success');
  const button = profileEl('profile-submit');
  error.textContent = '';
  success.textContent = '';
  markInvalid(PROFILE_FIELD_IDS, []);
  const body = buildProfileBody(profileState.user, {
    name: profileEl('profile-name').value,
    email: profileEl('profile-email').value,
    currentPassword: profileEl('profile-email-password').value,
  });
  if (!body.name) {
    error.textContent = 'Informe seu nome.';
    markInvalid(PROFILE_FIELD_IDS, ['name']);
    return;
  }
  if ('current_password' in body && !body.current_password) {
    error.textContent = 'Informe sua senha atual para trocar o e-mail.';
    markInvalid(PROFILE_FIELD_IDS, ['current_password']);
    return;
  }
  profileState.savingProfile = true;
  button.disabled = true;
  try {
    const result = await api('/api/me', {method: 'PATCH', body: JSON.stringify(body)});
    if (result.csrf) APP.csrf = result.csrf;
    profileState.user = {id: result.id, name: result.name, email: result.email, version: result.version};
    profileEl('profile-name').value = result.name;
    profileEl('profile-email').value = result.email;
    profileEl('profile-email-password').value = '';
    syncEmailPasswordField();
    APP.userName = result.name;  // the Resumo greeting uses the name just saved
    const sidebarUser = profileEl('sidebar-user');
    if (sidebarUser) sidebarUser.textContent = result.email;
    success.textContent = result.csrf
      ? 'Dados salvos. As outras sessões desta conta foram encerradas.'
      : 'Dados salvos.';
  } catch (e) {
    error.textContent = e.message;
    markInvalid(PROFILE_FIELD_IDS, e.fields);
  } finally {
    profileState.savingProfile = false;
    button.disabled = false;
  }
}

async function submitPassword(ev) {
  ev.preventDefault();
  if (profileState.savingPassword || !profileState.user) return;
  const error = profileEl('profile-password-error');
  const success = profileEl('profile-password-success');
  const button = profileEl('profile-password-submit');
  error.textContent = '';
  success.textContent = '';
  markInvalid(PASSWORD_FIELD_IDS, []);
  const current = profileEl('profile-current-password').value;
  const next = profileEl('profile-new-password').value;
  const confirmation = profileEl('profile-confirm-password').value;
  const problem = current ? validateNewPassword(next, confirmation) : 'Informe sua senha atual.';
  if (problem) {
    error.textContent = problem;
    markInvalid(PASSWORD_FIELD_IDS, current ? ['new_password'] : ['current_password']);
    return;
  }
  profileState.savingPassword = true;
  button.disabled = true;
  try {
    const result = await api('/api/me/password', {
      method: 'POST',
      body: JSON.stringify({current_password: current, new_password: next, expected_version: profileState.user.version}),
    });
    APP.csrf = result.csrf;
    profileState.user.version = result.version;
    clearProfilePasswords();
    success.textContent = 'Senha alterada. As outras sessões desta conta foram encerradas.';
  } catch (e) {
    error.textContent = e.message;
    markInvalid(PASSWORD_FIELD_IDS, e.fields);
  } finally {
    profileState.savingPassword = false;
    button.disabled = false;
  }
}

function initProfile() {
  document.querySelectorAll('[data-profile-open]').forEach((button) => {
    button.addEventListener('click', () => openProfile(button.dataset.profileOpen));
  });
  document.querySelectorAll('[data-profile-close]').forEach((button) => {
    button.addEventListener('click', closeProfile);
  });
  document.querySelectorAll('[data-password-toggle]').forEach((button) => {
    button.addEventListener('click', () => togglePasswordVisibility(button));
  });
  // Escape closes a native dialog without going through closeProfile().
  profileEl('profile-dialog').addEventListener('close', clearProfilePasswords);
  profileEl('profile-email').addEventListener('input', syncEmailPasswordField);
  profileEl('profile-form').addEventListener('submit', submitProfile);
  profileEl('profile-password-form').addEventListener('submit', submitPassword);
}

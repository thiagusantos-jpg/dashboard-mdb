/* Minha conta (task C1). Same style as the other frontend tests here: the
 * browser source runs in a vm context with hand-made DOM stubs, and api() is
 * a spy — assertions are on the requests actually sent and on field state. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'web/assets/profile.js'), 'utf8');

function makeElement(id) {
  const attributes = {};
  return {
    id, value: '', textContent: '', type: 'password', disabled: false, hidden: false, open: false,
    dataset: {},
    classList: {add() {}, remove() {}, toggle() {}},
    setAttribute(name, value) { attributes[name] = String(value); },
    getAttribute(name) { return name in attributes ? attributes[name] : null; },
    addEventListener() {},
    focus() {},
    showModal() { this.open = true; },
    close() { this.open = false; },
    reset() {},
  };
}

function load(apiImpl) {
  const elements = {};
  const byId = (id) => elements[id] || (elements[id] = makeElement(id));
  const calls = [];
  const context = vm.createContext({
    console,
    APP: {csrf: 'old-csrf'},
    document: {getElementById: byId, querySelectorAll: () => [], addEventListener() {}},
    api: async (url, opts) => {
      calls.push({url, opts: opts || {}});
      return apiImpl(url, opts || {}, calls.length);
    },
  });
  vm.runInContext(source, context);
  return {context, byId, calls};
}

const event = () => ({preventDefault() {}});

test('the footer opens Minha conta through a real dialog, without inline handlers', () => {
  assert.match(html, /data-profile-open/);
  assert.match(html, /<dialog[^>]+id="profile-dialog"[^>]+aria-labelledby="profile-dialog-title"/);
  assert.match(html, /<h2 id="profile-dialog-title"/);
  assert.match(html, /<label[^>]+for="profile-current-password"/);
  assert.match(html, /<label[^>]+for="profile-new-password"/);
  assert.match(html, /<label[^>]+for="profile-confirm-password"/);
  assert.match(html, /assets\/profile\.js/);
  assert.ok(html.indexOf('assets/profile.js') < html.indexOf('assets/app.js'));
  assert.doesNotMatch(html + source, /onclick=/);
});

test('profile body sends current password only when the e-mail changes', () => {
  const {context} = load(async () => ({}));
  const original = {name: 'Ana', email: 'ana@loja.test', version: 3};

  const nameOnly = context.buildProfileBody(original, {name: ' Ana Maria ', email: 'ana@loja.test', currentPassword: 'x'});
  const emailChange = context.buildProfileBody(original, {name: 'Ana', email: ' ANA2@loja.test ', currentPassword: 'segredo'});

  assert.equal(JSON.stringify(nameOnly), JSON.stringify({name: 'Ana Maria', email: 'ana@loja.test', expected_version: 3}));
  assert.equal(emailChange.current_password, 'segredo');
  assert.equal(emailChange.email, 'ana2@loja.test');
});

test('new password rules are checked before any request', () => {
  const {context} = load(async () => ({}));

  assert.match(context.validateNewPassword('curta', 'curta'), /8/);
  assert.match(context.validateNewPassword('senha-longa-1', 'senha-longa-2'), /não conferem/);
  assert.equal(context.validateNewPassword('senha-longa-1', 'senha-longa-1'), null);
});

test('wrong current password keeps typed values and re-enables submit; double click sends one request', async () => {
  let reject;
  const {context, byId, calls} = load(() => new Promise((_, r) => { reject = r; }));
  context.profileState.user = {id: '1', name: 'Ana', email: 'ana@loja.test', version: 3};
  byId('profile-current-password').value = 'errada';
  byId('profile-new-password').value = 'nova-senha-forte';
  byId('profile-confirm-password').value = 'nova-senha-forte';

  const first = context.submitPassword(event());
  const second = context.submitPassword(event());
  assert.equal(byId('profile-password-submit').disabled, true);
  const error = new Error('Senha atual incorreta.');
  error.status = 422;
  error.fields = ['current_password'];
  reject(error);
  await Promise.all([first, second]);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/me/password');
  assert.equal(JSON.parse(calls[0].opts.body).new_password, 'nova-senha-forte');
  assert.equal(JSON.parse(calls[0].opts.body).expected_version, 3);
  assert.equal(byId('profile-new-password').value, 'nova-senha-forte');
  assert.equal(byId('profile-password-submit').disabled, false);
  assert.equal(byId('profile-password-error').textContent, 'Senha atual incorreta.');
  assert.equal(byId('profile-current-password').getAttribute('aria-invalid'), 'true');
});

test('successful password change stores the renewed CSRF token and clears every password field', async () => {
  const {context, byId} = load(async () => ({ok: true, csrf: 'new-csrf', version: 4}));
  context.profileState.user = {id: '1', name: 'Ana', email: 'ana@loja.test', version: 3};
  byId('profile-current-password').value = 'antiga-senha';
  byId('profile-new-password').value = 'nova-senha-forte';
  byId('profile-confirm-password').value = 'nova-senha-forte';

  await context.submitPassword(event());

  assert.equal(context.APP.csrf, 'new-csrf');
  assert.equal(context.profileState.user.version, 4);
  for (const id of ['profile-current-password', 'profile-new-password', 'profile-confirm-password']) {
    assert.equal(byId(id).value, '');
  }
  assert.match(byId('profile-password-success').textContent, /Senha alterada/);
});

test('mismatched confirmation never reaches the server', async () => {
  const {context, byId, calls} = load(async () => ({}));
  context.profileState.user = {id: '1', name: 'Ana', email: 'ana@loja.test', version: 3};
  byId('profile-current-password').value = 'antiga-senha';
  byId('profile-new-password').value = 'nova-senha-forte';
  byId('profile-confirm-password').value = 'outra-senha-forte';

  await context.submitPassword(event());

  assert.equal(calls.length, 0);
  assert.match(byId('profile-password-error').textContent, /não conferem/);
});

test('closing the dialog clears passwords, and show/hide toggles aria-pressed', () => {
  const {context, byId} = load(async () => ({}));
  byId('profile-dialog').open = true;
  byId('profile-current-password').value = 'segredo';
  byId('profile-new-password').value = 'nova-senha-forte';
  byId('profile-confirm-password').value = 'nova-senha-forte';

  const toggle = byId('profile-toggle-new');
  toggle.dataset.target = 'profile-new-password';
  context.togglePasswordVisibility(toggle);
  assert.equal(toggle.getAttribute('aria-pressed'), 'true');
  assert.equal(byId('profile-new-password').type, 'text');

  context.closeProfile();
  assert.equal(byId('profile-dialog').open, false);
  for (const id of ['profile-current-password', 'profile-new-password', 'profile-confirm-password']) {
    assert.equal(byId(id).value, '');
    assert.equal(byId(id).type, 'password');
  }
});

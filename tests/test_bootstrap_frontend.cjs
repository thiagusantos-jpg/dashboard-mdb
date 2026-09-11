const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('web/assets/app.js', 'utf8');

function setup(jobs) {
  const elements = {
    'bootstrap-company-id': {value: '218'},
    'bootstrap-submit': {disabled: false},
    'bootstrap-status': {textContent: '', className: ''},
  };
  const opened = [];
  let reads = 0;
  const context = vm.createContext({
    URLSearchParams, Intl, console,
    document: {addEventListener() {}, getElementById: id => elements[id]},
    window: {addEventListener() {}},
    setTimeout(fn) {fn();},
  });
  // Exercise the submit handler without running the page's DOM initialization.
  vm.runInContext(source.replace(/\nboot\(\);\s*$/, ''), context);
  context.api = async path => {
    if (path.endsWith('/sync')) return {job_id: 9};
    if (path === '/api/sync-jobs/9') return jobs[Math.min(reads++, jobs.length - 1)];
    if (path === '/api/session') return {companies: [{id: 1}, {id: 218}]};
    throw new Error('Unexpected request: ' + path);
  };
  context.onAuthenticated = async session => opened.push({session, reads});
  return {context, elements, opened};
}

test('does not open dashboard until sales sync completes; opens requested company', async () => {
  const x = setup([{state: 'running'}, {state: 'running'}, {state: 'completed'}]);
  await x.context.onBootstrapSubmit({preventDefault() {}});
  assert.equal(x.opened.length, 1);
  assert.equal(x.opened[0].reads, 3);
  assert.equal(x.opened[0].session.companies[0].id, 218);
});

test('shows the actual upstream failure and enables retry', async () => {
  const x = setup([{state: 'failed', error: 'Mobne indisponível'}]);
  await x.context.onBootstrapSubmit({preventDefault() {}});
  assert.equal(x.opened.length, 0);
  assert.match(x.elements['bootstrap-status'].textContent, /Mobne indisponível/);
  assert.equal(x.elements['bootstrap-submit'].disabled, false);
});

test('long-running sync is not mislabeled as an unauthorized company', async () => {
  const x = setup([{state: 'running'}]);
  await x.context.onBootstrapSubmit({preventDefault() {}});
  assert.equal(x.opened.length, 0);
  assert.match(x.elements['bootstrap-status'].textContent, /em andamento/);
  assert.equal(x.elements['bootstrap-submit'].disabled, false);
});

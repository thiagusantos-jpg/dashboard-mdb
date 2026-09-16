/* Todos os arquivos de web/assets/ compartilham um único escopo global, na ordem de
 * carregamento de web/index.html. Dois arquivos que declarem o mesmo nome no topo não
 * dão erro: o último a carregar simplesmente apaga o primeiro, e a falha aparece longe
 * dali. Já aconteceu duas vezes — `EXPENSE_NATURES` e `bindDrawerForm`, que fez todos os
 * formulários do financeiro fecharem sem salvar. Este teste tranca a porta. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const assetsDir = path.join(__dirname, '..', 'web', 'assets');

/* Nomes declarados na primeira coluna, que é onde mora o escopo global destes arquivos. */
function topLevelNames(source) {
  const names = new Set();
  const pattern = /^(?:function|var|const|let)\s+([A-Za-z_$][\w$]*)/gm;
  let match;
  while ((match = pattern.exec(source))) names.add(match[1]);
  return names;
}

test('no two asset files declare the same global name', () => {
  const owners = new Map();
  fs.readdirSync(assetsDir).filter((f) => f.endsWith('.js')).sort().forEach((file) => {
    topLevelNames(fs.readFileSync(path.join(assetsDir, file), 'utf8')).forEach((name) => {
      if (!owners.has(name)) owners.set(name, []);
      owners.get(name).push(file);
    });
  });

  const collisions = Array.from(owners)
    .filter(([, files]) => files.length > 1)
    .map(([name, files]) => `${name}: ${files.join(', ')}`);

  assert.deepEqual(collisions, [],
    `O último arquivo a carregar apaga a declaração do primeiro. Renomeie um dos dois:\n  ${collisions.join('\n  ')}`);
});

test('every asset in index.html loads, and each one only once', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
  const loaded = Array.from(html.matchAll(/<script src="assets\/([\w-]+\.js)\?v=/g)).map((m) => m[1]);
  const twice = loaded.filter((file, i) => loaded.indexOf(file) !== i);
  assert.deepEqual(twice, [], 'arquivo carregado duas vezes redeclara tudo o que ele define');
});

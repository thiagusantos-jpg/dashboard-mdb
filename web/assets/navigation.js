/* Two modules in one application (task C3): Análises (sales, margin, mix,
 * stock) and Financeiro (result, obligations, cash). Pure route rules only —
 * app.js applies them to the menu, the top bar and the router. Loaded before
 * app.js. Session storage keeps just the last route of each module (page,
 * period and non-sensitive filters), never credentials. `var` keeps the rules
 * reachable from the vm-based unit tests. */
'use strict';

var MODULE_PAGES = {
  analises: ['resumo', 'precos', 'mapa', 'diagnostico', 'sazonalidade', 'visao', 'estoque', 'reposicao', 'produto', 'acoes'],
  financeiro: ['financeiro', 'contas-pagar', 'despesas', 'fluxo-caixa', 'conciliacao', 'recebiveis'],
};

var MODULE_HOME = {analises: '#/resumo', financeiro: '#/financeiro'};

// Financeiro pages that read one competence month; the others filter by due
// date, horizon or account on the page itself.
var COMPETENCE_PAGES = ['financeiro', 'despesas'];

// Retired destinations keep working as links: bookmarks, history, old e-mails.
var LEGACY_ROUTES = {
  sync: () => '#/configuracoes/integracoes',
  emprestimos: () => '#/contas-pagar?tipo=emprestimo',
};

var MODULE_ROUTES_KEY = 'mdb.moduleRoutes';

function moduleForPage(page) {
  if (MODULE_PAGES.analises.includes(page)) return 'analises';
  if (MODULE_PAGES.financeiro.includes(page)) return 'financeiro';
  return null;
}

function canonicalRoute(hash) {
  const match = String(hash || '').match(/^#\/?([^/?]+)/);
  if (!match || !LEGACY_ROUTES[match[1]]) return hash;
  return LEGACY_ROUTES[match[1]]();
}

// Pages of a module that do not depend on the chosen month (their data is always current).
var PERIODLESS_PAGES = ['acoes'];

function periodModeForPage(page) {
  if (PERIODLESS_PAGES.includes(page)) return 'none';
  if (MODULE_PAGES.analises.includes(page)) return 'sales';
  if (COMPETENCE_PAGES.includes(page)) return 'competence';
  return 'none';
}

function shiftMonth(period, delta) {
  const [y, m] = period.split('-').map(Number);
  const total = y * 12 + (m - 1) + delta;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, '0')}`;
}

function currentMonth() {
  try {
    return new Intl.DateTimeFormat('en-CA', {timeZone: 'America/Sao_Paulo', year: 'numeric', month: '2-digit'})
      .format(new Date()).slice(0, 7);
  } catch (e) {
    return new Date().toISOString().slice(0, 7);
  }
}

// Reading the storage accessor itself can throw (blocked site data).
function safeSessionStorage() {
  try {
    return typeof sessionStorage !== 'undefined' ? sessionStorage : null;
  } catch (e) {
    return null;
  }
}

function createModuleRoutes(storage) {
  let routes = {};
  try {
    routes = JSON.parse((storage && storage.getItem(MODULE_ROUTES_KEY)) || '{}') || {};
  } catch (e) {
    routes = {};
  }
  return {
    remember(hash) {
      const match = String(hash || '').match(/^#\/?([^/?]+)/);
      const module = match && moduleForPage(match[1]);
      if (!module) return;
      routes[module] = hash;
      try {
        if (storage) storage.setItem(MODULE_ROUTES_KEY, JSON.stringify(routes));
      } catch (e) { /* the in-memory route still works for this visit */ }
    },
    routeFor(module) {
      return routes[module] || MODULE_HOME[module];
    },
  };
}

/* Mercado duBairro — page-load coordinator (task A4).
 *
 * Two things used to go wrong on this dashboard:
 *   1. a slow /dashboard or /finance/* response for a page the user had
 *      already navigated away from landed anyway and repainted #content with
 *      the wrong page's data (the render code read the mutable APP after the
 *      await, so it could not tell "my response" from "someone else's");
 *   2. the 60s background refresh repainted the page under a form the user was
 *      still typing into, throwing the typed values away.
 *
 * createPageState() answers both with a monotonic counter plus a dirty flag:
 * every render path calls begin(key) first and keeps the returned token, and
 * before writing content (or an error) to the DOM it asks isCurrent(token).
 * An older token is never current again, so an obsolete response is simply
 * dropped. canRefresh() is false while a form is dirty, so background refresh
 * shows "Novos dados disponíveis" instead of replacing the fields.
 *
 * markDirty only ever records a boolean — no field value (and certainly no
 * password) is stored here or anywhere else persistent.
 *
 * Loaded as a plain script before app.js (see web/index.html); the CSP is
 * script-src 'self', so this is a script-order global, not a module. */
'use strict';

function createPageState() {
  let issued = 0;     // tokens handed out so far (monotonic, never reused)
  let current = 0;    // the only token allowed to write to the DOM (0 = none)
  let dirty = false;  // a form on the current page has unsaved edits

  return {
    /* Starts a render attempt and invalidates the previous one. `key`
     * identifies the route (e.g. 'resumo/2026-09', 'configuracoes/empresa')
     * and is carried on the token for debugging only — identity is the id. */
    begin(key) {
      issued += 1;
      current = issued;
      return {id: issued, key: key == null ? '' : String(key)};
    },

    /* True only for the token of the render attempt still in charge. */
    isCurrent(token) {
      return !!token && token.id === current;
    },

    markDirty(value) { dirty = !!value; },
    isDirty() { return dirty; },

    /* Background refresh/polling asks this before repainting the page. */
    canRefresh() { return !dirty; },

    /* Invalidates every outstanding token (no response may render) and drops
     * the dirty flag. Used on logout and before a new login, so a request in
     * flight from the previous session can never paint over the new one. */
    reset() {
      issued += 1;
      current = 0;
      dirty = false;
    },
  };
}

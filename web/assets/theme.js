/* Modo escuro opcional: o claro é o padrão e o sócio liga o escuro no rodapé do menu.
 * Carregado no <head>, antes de o CSS pintar a página, para quem escolheu o escuro não
 * ver um clarão branco a cada abertura. A escolha fica neste navegador (localStorage). */
(function () {
  var KEY = 'mdb.theme';

  function storedTheme() {
    try { return localStorage.getItem(KEY) === 'dark' ? 'dark' : 'light'; } catch (e) { return 'light'; }
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    var buttons = document.querySelectorAll('[data-theme-toggle]');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute('aria-pressed', String(theme === 'dark'));
      buttons[i].title = theme === 'dark' ? 'Voltar ao tema claro' : 'Usar tema escuro';
    }
  }

  window.currentTheme = function () {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  };

  window.toggleTheme = function () {
    var next = window.currentTheme() === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem(KEY, next); } catch (e) { /* vale só para esta aba */ }
    applyTheme(next);
    // Gráficos são canvas e leem a paleta ao montar: redesenha a página aberta.
    if (typeof renderPage === 'function' && typeof APP !== 'undefined' && APP.company != null) renderPage();
  };

  applyTheme(storedTheme());
  document.addEventListener('DOMContentLoaded', function () {
    applyTheme(window.currentTheme());
    var buttons = document.querySelectorAll('[data-theme-toggle]');
    for (var i = 0; i < buttons.length; i++) buttons[i].addEventListener('click', window.toggleTheme);
  });
})();

(function () {
  'use strict';

  const storageKey = 'rag:theme';
  const themes = new Set(['dark', 'light']);
  let current = 'dark';

  try {
    const saved = localStorage.getItem(storageKey);
    if (themes.has(saved)) current = saved;
  } catch { /* Storage can be disabled. */ }

  function apply(theme, persist = false) {
    current = themes.has(theme) ? theme : 'dark';
    const root = document.documentElement;
    root.dataset.theme = current;
    root.style.colorScheme = current;
    document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', current);

    const target = current === 'dark' ? 'light' : 'dark';
    const control = document.querySelector('#theme-toggle');
    if (control) {
      const translation = `theme.activate_${target}`;
      control.setAttribute('data-i18n-aria-label', translation);
      control.setAttribute('data-i18n-title', translation);
      control.setAttribute('aria-pressed', String(current === 'light'));
      document.querySelector('#theme-icon')?.setAttribute('href', target === 'light' ? '#i-sun' : '#i-moon');
    }

    if (persist) {
      try { localStorage.setItem(storageKey, current); } catch { /* Storage can be disabled. */ }
    }
    window.dispatchEvent(new CustomEvent('rag:themechange', {detail: {theme: current}}));
  }

  apply(current);
  window.addEventListener('DOMContentLoaded', () => {
    apply(current);
    document.querySelector('#theme-toggle')?.addEventListener('click', () => {
      apply(current === 'dark' ? 'light' : 'dark', true);
    });
  }, {once: true});

  window.ragTheme = {get: () => current, set: theme => apply(theme, true)};
})();

// Local, plain-text translations. Document/model content never passes through this module.
const supported = ['es', 'en'];
const storageKey = 'rag:language';
let catalogs = {}, language = 'es', preference = 'auto';
let messagePatterns = [];

export function resolveLanguage(choice, languages = []) {
  if (supported.includes(choice)) return choice;
  return languages.map(value => String(value).toLowerCase().split(/[-_]/)[0]).find(value => supported.includes(value)) || 'en';
}

export function formatMessage(catalog, fallback, key, values = {}) {
  const template = Object.hasOwn(catalog, key) ? catalog[key] : Object.hasOwn(fallback, key) ? fallback[key] : key;
  return template.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);
}

export const getLanguage = () => language;
export const getPreference = () => preference;
export const intlLocale = () => language === 'es' ? 'es-ES' : 'en-US';
export const t = (key, values = {}) => formatMessage(catalogs[language] || {}, catalogs.es || {}, key, values);

export function translateStatic(root = document) {
  for (const element of root.querySelectorAll('[data-i18n]')) element.textContent = t(element.dataset.i18n);
  for (const attribute of ['aria-label', 'title', 'placeholder', 'alt']) {
    for (const element of root.querySelectorAll(`[data-i18n-${attribute}]`)) {
      element.setAttribute(attribute, t(element.getAttribute(`data-i18n-${attribute}`)));
    }
  }
  const select = root.querySelector('#language-selection');
  if (select) select.value = preference;
  const flag = root.querySelector('#language-flag');
  if (flag) flag.src = `/assets/icons/flags/${language}.svg`;
}

export function setLanguage(choice, {persist = true} = {}) {
  preference = supported.includes(choice) ? choice : 'auto';
  language = resolveLanguage(preference, navigator.languages || [navigator.language]);
  if (!catalogs[language]) language = catalogs.es ? 'es' : 'en';
  if (persist) try { localStorage.setItem(storageKey, preference); } catch { /* Storage can be disabled. */ }
  document.documentElement.lang = language;
  translateStatic();
  window.dispatchEvent(new CustomEvent('rag:languagechange', {detail: {language, preference}}));
}

export async function initI18n() {
  const loaded = await Promise.allSettled(supported.map(async code => {
    const response = await fetch(`/assets/locales/${code}.json`);
    if (!response.ok) throw new Error(`Locale ${code}: HTTP ${response.status}`);
    const catalog = await response.json();
    if (!catalog || Array.isArray(catalog) || Object.values(catalog).some(value => typeof value !== 'string')) throw new Error(`Invalid locale: ${code}`);
    return [code, catalog];
  }));
  catalogs = Object.fromEntries(loaded.filter(item => item.status === 'fulfilled').map(item => item.value));
  if (!Object.keys(catalogs).length) throw new Error('Local translation files are unavailable. Reload the application.');
  messagePatterns = Object.entries(catalogs).flatMap(([, catalog]) => Object.entries(catalog).map(([key, source]) => {
    const names = [];
    const expression = source.split(/(\{\w+\})/).map(part => {
      if (/^\{\w+\}$/.test(part)) { names.push(part.slice(1, -1)); return '([\\s\\S]*?)'; }
      return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }).join('');
    return {key, names, weight: (names.length ? 0 : 10000) + source.replace(/\{\w+\}/g, '').length,
            expression: new RegExp(`^${expression}$`)};
  })).sort((left, right) => right.weight - left.weight);
  let choice = 'auto';
  try { choice = localStorage.getItem(storageKey) || 'auto'; } catch { /* Use the browser language. */ }
  setLanguage(choice, {persist: false});
  document.querySelector('#language-selection')?.addEventListener('change', event => setLanguage(event.target.value));
  window.addEventListener('languagechange', () => { if (preference === 'auto') setLanguage('auto', {persist: false}); });
}

// Translate only known server messages, never answer text, filenames or raw logs.
export function serverText(message) {
  if (typeof message !== 'string') return message;
  for (const {key, names, expression} of messagePatterns) {
    const match = message.match(expression);
    if (match) return t(key, Object.fromEntries(names.map((name, index) => [name, match[index + 1]])));
  }
  return message;
}

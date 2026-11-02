// Interface language: Indonesian (default) and English. Texts live in locales/<area>.js, each exporting {id: {...}, en: {...}},
// read with t('area.key', {param}). Parameters are HTML-escaped; wrap trusted markup with raw().
import activity from '../locales/activity.js';
import auth from '../locales/auth.js';
import common from '../locales/common.js';
import jobs from '../locales/jobs.js';
import settings from '../locales/settings.js';
import sites from '../locales/sites.js';

const AREAS = { activity, auth, common, jobs, settings, sites };

export const LANGUAGES = {
  id: { name: 'Bahasa Indonesia', short: 'ID', locale: 'id-ID' },
  en: { name: 'English', short: 'EN', locale: 'en-GB' },
};
const STORAGE_KEY = 'rollout-lang';

function detect() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && LANGUAGES[saved]) return saved;
  } catch (e) { /* private mode */ }
  const preferred = (navigator.languages || [navigator.language || 'id']).map((l) => String(l).toLowerCase());
  const first = preferred.find((l) => /^(id|ms|en)/.test(l)) || 'id';
  return first.startsWith('en') ? 'en' : 'id';
}

let current = detect();
document.documentElement.lang = current;

export const lang = () => current;
export const locale = () => LANGUAGES[current].locale;

/** Switch the language; the choice is kept in this browser and sent to the server (cookie + X-Locale header). */
export function setLang(code) {
  if (!LANGUAGES[code]) return;
  current = code;
  document.documentElement.lang = code;
  try { localStorage.setItem(STORAGE_KEY, code); } catch (e) { /* private mode */ }
  document.cookie = `lang=${code}; path=/; max-age=31536000; samesite=lax`;
}
document.cookie = `lang=${current}; path=/; max-age=31536000; samesite=lax`;

const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/** Mark a parameter as trusted HTML (not escaped). */
export const raw = (html) => ({ __html: String(html) });

function lookup(language, key) {
  const [area, ...path] = key.split('.');
  let value = AREAS[area] && AREAS[area][language];
  for (const part of path) {
    if (value === undefined || value === null) return undefined;
    value = value[part];
  }
  return value;
}

/** Text for key in the current language (falls back to Indonesian, then to the key). */
export function t(key, params = {}) {
  let value = lookup(current, key);
  if (value === undefined) value = lookup('id', key);
  if (value === undefined) return key;
  if (typeof value !== 'string') return value;
  return value.replace(/\{(\w+)\}/g, (match, name) => {
    if (!(name in params)) return match;
    const param = params[name];
    return param && typeof param === 'object' && '__html' in param ? param.__html : escape(param);
  });
}

/** Illustration with text inside it: assets/img/<name>.svg (Indonesian) or <name>-en.svg (English). */
export const illustration = (name) => `assets/img/${name}${current === 'en' ? '-en' : ''}.svg`;

/** Array or object value (lists of features, tips, FAQ...). */
export function tList(key) {
  const value = t(key);
  return Array.isArray(value) ? value : [];
}

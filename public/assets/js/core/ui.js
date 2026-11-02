// UI helpers: HTML escaping, number/date formatting in the interface language, SVG icons, avatars, toasts, dialogs,
// per-field form errors, password strength.
import { locale, t } from './i18n.js';

export { locale };

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || value === '' || Number.isNaN(Number(value))) return '–';
  return Number(value).toLocaleString(locale(), { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export const formatPercent = (p, digits = 1) => (p === null || p === undefined ? '–' : formatNumber(p * 100, digits) + '%');

export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return '–';
  if (bytes >= 1073741824) return formatNumber(bytes / 1073741824, 1) + ' GB';
  return bytes >= 1048576 ? formatNumber(bytes / 1048576, 1) + ' MB' : formatNumber(bytes / 1024, 0) + ' KB';
}

/** Duration between two ISO times as "1 min 05 s" / "42 s". */
export function duration(startIso, endIso) {
  const start = toDate(startIso), end = endIso ? toDate(endIso) : new Date();
  if (!startIso || Number.isNaN(start.getTime())) return '–';
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  return seconds >= 60 ? `${Math.floor(seconds / 60)} min ${String(seconds % 60).padStart(2, '0')} s` : `${seconds} s`;
}

const toDate = (iso) => new Date(String(iso || '').replace(' ', 'T'));

export function formatDateTime(iso, short = false) {
  const date = toDate(iso);
  if (!iso || Number.isNaN(date.getTime())) return iso || '–';
  return date.toLocaleString(locale(), short
    ? { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }
    : { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function formatDate(iso) {
  const date = toDate(iso);
  if (!iso || Number.isNaN(date.getTime())) return '–';
  return date.toLocaleDateString(locale(), { day: 'numeric', month: 'long', year: 'numeric' });
}

/** "just now", "5 min ago", "3 days ago", then the date. */
export function timeAgo(iso) {
  const date = toDate(iso);
  if (!iso || Number.isNaN(date.getTime())) return '–';
  const seconds = (Date.now() - date.getTime()) / 1000;
  if (seconds < 60) return t('common.time.just_now');
  if (seconds < 3600) return t('common.time.minutes', { n: Math.floor(seconds / 60) });
  if (seconds < 86400) return t('common.time.hours', { n: Math.floor(seconds / 3600) });
  if (seconds < 7 * 86400) return t('common.time.days', { n: Math.floor(seconds / 86400) });
  return formatDate(iso);
}

export function debounce(fn, ms = 400) {
  let timer = null;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

// 24x24 line icons (Feather Icons style, MIT)
const ICONS = {
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
  'eye-off': '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  'check-circle': '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  'x-circle': '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
  alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  'user-plus': '<path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="20" y1="8" x2="20" y2="14"/><line x1="23" y1="11" x2="17" y2="11"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  'shield-check': '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/>',
  sun: '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  'arrow-right': '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
  'arrow-left': '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
  'chevron-down': '<polyline points="6 9 12 15 18 9"/>',
  refresh: '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  'log-out': '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
  'log-in': '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  unlock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
  list: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
  server: '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
  book: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  layers: '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
  activity: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
  grid: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
  zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
  filter: '<polygon points="22 3 2 3 10 12.46 10 19 13 21 13 12.46 22 3"/>',
  home: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  edit: '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
  ban: '<circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>',
  globe: '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
  shuffle: '<polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/>',
  'git-branch': '<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
  'git-commit': '<circle cx="12" cy="12" r="4"/><line x1="1.05" y1="12" x2="7" y2="12"/><line x1="17.01" y1="12" x2="22.96" y2="12"/>',
  rollback: '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
  package: '<line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
  terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
  archive: '<polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/>',
  'external-link': '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  github: '<path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>',
  'hard-drive': '<line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/>',
  play: '<polygon points="5 3 19 12 5 21 5 3"/>',
  'upload-cloud': '<polyline points="16 16 12 12 8 16"/><line x1="12" y1="12" x2="12" y2="21"/><path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3"/>',
  folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  key: '<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>',
};

export function icon(name, className = '') {
  return `<svg class="icon ${className}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${ICONS[name] || ''}</svg>`;
}

/** Round avatar: the profile photo, or the initials with a stable colour per account. */
export function avatar(user, size = 40, className = '') {
  const name = (user && user.name) || '?';
  if (user && user.avatar_url) {
    return `<img class="avatar ${className}" src="${esc(user.avatar_url)}" alt="" width="${size}" height="${size}" style="width:${size}px;height:${size}px" loading="lazy">`;
  }
  const words = name.trim().split(/\s+/);
  const initials = ((words[0] || '?')[0] + (words.length > 1 ? words[words.length - 1][0] : '')).toUpperCase();
  const colour = (user && user.id ? Number(user.id) : name.length) % 6;
  return `<span class="avatar avatar--initials avatar--c${colour} ${className}" style="width:${size}px;height:${size}px;font-size:${Math.round(size * 0.38)}px" aria-hidden="true">${esc(initials)}</span>`;
}

// ---------- toasts ----------
/** type: info, success, warning, error */
export function toast(message, type = 'info', duration = 4500) {
  const container = document.getElementById('toast');
  if (!container) return;
  const el = document.createElement('div');
  el.className = `toast toast--${type}`;
  el.setAttribute('role', type === 'error' ? 'alert' : 'status');
  const name = type === 'success' ? 'check' : type === 'info' ? 'info' : 'alert';
  el.innerHTML = `${icon(name)}<div>${esc(message).replace(/\n/g, '<br>')}</div><button type="button" class="toast__close" aria-label="${esc(t('common.close'))}">${icon('x')}</button>`;
  const close = () => { el.classList.add('toast--leaving'); setTimeout(() => el.remove(), 250); };
  el.querySelector('button').addEventListener('click', close);
  container.appendChild(el);
  setTimeout(close, duration);
}

// ---------- confirmation dialog ----------
export function confirmDialog({ title, message, confirmLabel = t('common.yes'), danger = false }) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay overlay--dialog';
    overlay.innerHTML = `<div class="dialog" role="alertdialog" aria-modal="true" aria-labelledby="dialog-title">
      <h2 id="dialog-title">${esc(title)}</h2><p>${esc(message)}</p>
      <div class="dialog__actions"><button type="button" class="btn btn--outline" data-answer="0">${esc(t('common.cancel'))}</button>
      <button type="button" class="btn ${danger ? 'btn--danger' : 'btn--primary'}" data-answer="1">${esc(confirmLabel)}</button></div></div>`;
    const close = (answer) => { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(answer); };
    const onKey = (e) => { if (e.key === 'Escape') close(false); };
    overlay.addEventListener('click', (e) => {
      const button = e.target.closest('[data-answer]');
      if (button) close(button.dataset.answer === '1'); else if (e.target === overlay) close(false);
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    overlay.querySelector('[data-answer="1"]').focus();
  });
}

/**
 * Dialog containing a form. body = HTML inside the <form>. onSubmit(form) runs on save; throw an ApiError to keep the
 * dialog open (field errors are marked automatically). Resolves with onSubmit's result, or null when cancelled.
 */
export function formDialog({ title, body, submitLabel = t('common.save'), danger = false, wide = false, onSubmit }) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay overlay--dialog';
    overlay.innerHTML = `<form class="dialog ${wide ? 'dialog--wide' : ''}" role="dialog" aria-modal="true" aria-labelledby="form-dialog-title" novalidate>
      <div class="dialog__header"><h2 id="form-dialog-title">${esc(title)}</h2>
        <button type="button" class="icon-btn" data-cancel aria-label="${esc(t('common.close'))}">${icon('x')}</button></div>
      <div class="dialog__body">${body}</div>
      <div class="box box--red" data-form-error hidden></div>
      <div class="dialog__actions"><button type="button" class="btn btn--outline" data-cancel>${esc(t('common.cancel'))}</button>
        <button type="submit" class="btn ${danger ? 'btn--danger' : 'btn--primary'}">${esc(submitLabel)}</button></div></form>`;
    const form = overlay.querySelector('form');
    const close = (result) => { overlay.remove(); document.removeEventListener('keydown', onKey); document.body.classList.remove('locked'); resolve(result); };
    const onKey = (e) => { if (e.key === 'Escape') close(null); };
    overlay.addEventListener('click', (e) => { if (e.target.closest('[data-cancel]') || e.target === overlay) close(null); });
    document.addEventListener('keydown', onKey);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearErrors(form);
      const button = form.querySelector('[type=submit]');
      button.disabled = true;
      try {
        close(await onSubmit(form));
      } catch (error) {
        showErrors(form, error);
      } finally {
        button.disabled = false;
      }
    });
    document.body.appendChild(overlay);
    document.body.classList.add('locked');
    const first = form.querySelector('input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea');
    if (first) first.focus();
  });
}

// ---------- per-field errors ----------
export function clearErrors(form) {
  form.querySelectorAll('.field--error').forEach((el) => el.classList.remove('field--error'));
  form.querySelectorAll('.field__error').forEach((el) => el.remove());
  const box = form.querySelector('[data-form-error]');
  if (box) box.hidden = true;
}

/** Mark the fields named in an API error ({fields: {name: message}}); other messages go to the form's error box. */
export function showErrors(form, error) {
  const fields = (error && error.fields) || {};
  const rest = [];
  let first = null;
  Object.entries(fields).forEach(([name, message]) => {
    const input = form.querySelector(`[name="${name}"]`);
    const wrapper = input && (input.closest('.field') || input.closest('label'));
    if (!wrapper) { rest.push(message); return; }
    wrapper.classList.add('field--error');
    wrapper.insertAdjacentHTML('beforeend', `<span class="field__error">${icon('alert')}${esc(message)}</span>`);
    if (!first) first = input;
  });
  const box = form.querySelector('[data-form-error]');
  const general = Object.keys(fields).length ? rest : [(error && error.message) || t('common.save_failed')];
  if (box && general.length) {
    box.hidden = false;
    box.innerHTML = `${icon('alert')}<div>${general.map(esc).join('<br>')}</div>`;
  }
  if (first) first.focus(); else if (box && !box.hidden) box.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// ---------- passwords ----------
/** Score 0–4 + label for the strength meter (a rough browser estimate; the server enforces the real rules). */
export function passwordStrength(password) {
  if (!password) return { score: 0, label: '' };
  let score = 0;
  if (password.length >= 8) score++;
  if (password.length >= 12) score++;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score++;
  if (/\d/.test(password)) score += 0.5;
  if (/[^A-Za-z0-9]/.test(password)) score += 0.5;
  if (!/[A-Za-z]/.test(password) || !/\d/.test(password) || password.length < 8) score = Math.min(score, 1);
  score = Math.min(4, Math.round(score));
  return { score, label: t('common.password_strength')[score] };
}

/** Show/hide button for every input[type=password] inside root. */
export function attachPasswordToggles(root) {
  root.querySelectorAll('input[type=password]').forEach((input) => {
    if (input.dataset.toggle) return;
    input.dataset.toggle = '1';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'show-password';
    button.setAttribute('aria-label', t('common.password_show'));
    button.innerHTML = icon('eye');
    button.addEventListener('click', () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.innerHTML = icon(show ? 'eye-off' : 'eye');
      button.setAttribute('aria-label', t(show ? 'common.password_hide' : 'common.password_show'));
    });
    const wrapper = document.createElement('span');
    wrapper.className = 'field__password';
    input.parentNode.insertBefore(wrapper, input);
    wrapper.append(input, button);
  });
}

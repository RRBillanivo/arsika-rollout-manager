// Activity log: administrator actions (deployments, rollbacks, settings) and sign-ins.
import { get } from '../core/api.js';
import { t } from '../core/i18n.js';
import { esc, formatDateTime, icon } from '../core/ui.js';

export async function render(main, _params, _app, isCurrent) {
  main.innerHTML = '<div aria-busy="true"><div class="skeleton skeleton--title"></div><div class="skeleton skeleton--table"></div></div>';
  const { data } = await get('activity');
  if (!isCurrent()) return null;
  const a = (key, params) => t('activity.' + key, params);
  const c = (key) => a('columns.' + key);
  const actionLabel = (code) => { const label = a('actions.' + code); return label === 'activity.actions.' + code ? code : label; };
  let tab = 'actions';
  main.innerHTML = `
<header class="page-title">
  <div><h1>${a('title')}</h1><p class="muted">${a('summary', { ok: data.summary.succeeded_24h, failed: data.summary.failed_24h })}</p></div>
  <div class="tabs" role="tablist">
    <button type="button" role="tab" class="tabs__item tabs__item--active" data-tab="actions" aria-selected="true">${icon('activity')}${a('tab_actions')}</button>
    <button type="button" role="tab" class="tabs__item" data-tab="logins" aria-selected="false">${icon('log-in')}${a('tab_logins')}</button>
  </div>
</header>
<section class="card card--table" data-content></section>`;
  const content = main.querySelector('[data-content]');
  const draw = () => {
    if (tab === 'logins') {
      content.innerHTML = data.logins.length ? `<div class="table-scroll"><table class="table">
        <thead><tr><th>${c('time')}</th><th>${c('account')}</th><th>${c('result')}</th><th>${c('device')}</th><th>${c('ip')}</th></tr></thead>
        <tbody>${data.logins.map((l) => `<tr>
          <td data-label="${c('time')}" class="muted">${formatDateTime(l.attempted_at, true)}</td>
          <td data-label="${c('account')}"><b>${esc(l.identity)}</b>${l.user_name ? '' : `<small class="block muted">${a('unknown')}</small>`}</td>
          <td data-label="${c('result')}"><span class="badge ${l.success ? 'badge--normal' : 'badge--danger'}">${a(l.success ? 'success' : 'failed')}</span></td>
          <td data-label="${c('device')}">${esc(l.device || '–')}</td>
          <td data-label="${c('ip')}" class="mono">${esc(l.ip)}</td></tr>`).join('')}</tbody></table></div>`
        : `<div class="empty">${icon('log-in', 'icon--large')}<p>${a('no_logins')}</p></div>`;
    } else {
      content.innerHTML = data.actions.length ? `<div class="table-scroll"><table class="table">
        <thead><tr><th>${c('time')}</th><th>${c('admin')}</th><th>${c('action')}</th><th>${c('details')}</th></tr></thead>
        <tbody>${data.actions.map((row) => `<tr>
          <td data-label="${c('time')}" class="muted">${formatDateTime(row.created_at, true)}</td>
          <td data-label="${c('admin')}">${row.actor_name ? esc(row.actor_name) : a('system')}</td>
          <td data-label="${c('action')}"><b>${esc(actionLabel(row.action))}</b>${row.target ? `<small class="block muted mono">${esc(row.target)}</small>` : ''}</td>
          <td data-label="${c('details')}" class="small">${esc(row.details || '')}${row.ip ? `<small class="block muted mono">${esc(row.ip)}</small>` : ''}</td></tr>`).join('')}</tbody></table></div>`
        : `<div class="empty">${icon('activity', 'icon--large')}<p>${a('no_actions')}</p></div>`;
    }
  };
  main.querySelector('.tabs').addEventListener('click', (e) => {
    const button = e.target.closest('[data-tab]');
    if (!button) return;
    tab = button.dataset.tab;
    main.querySelectorAll('[data-tab]').forEach((b) => { b.classList.toggle('tabs__item--active', b === button); b.setAttribute('aria-selected', String(b === button)); });
    draw();
  });
  draw();
  return null;
}

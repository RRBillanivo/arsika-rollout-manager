// Site details: status, quick actions (health check, rollback, backup) and the tabs Versions, History, Backups, Settings.
import { del, get, put } from '../core/api.js';
import { t } from '../core/i18n.js';
import { attachPasswordToggles, clearErrors, duration, esc, formatBytes, formatDate, formatDateTime, icon, showErrors, toast } from '../core/ui.js';
import { actionDialog, checkbox, healthBadge, jobStatusBadge, jobType, localizeFields, passwordField, startJob, stateBadge, versionPill } from './shared/bits.js';
import { attachSiteForm, readSiteForm, siteFormHtml } from './shared/site-form.js';

const TABS = [['versions', 'git-branch'], ['history', 'clock'], ['backups', 'archive'], ['settings', 'settings']];

function header(site) {
  const d = (key, params) => t('sites.detail.' + key, params);
  const busy = !!site.active_job;
  const ready = site.state === 'ready';
  return `<header class="site-header card">
    <div class="site-header__text">
      <h1>${esc(site.name)}</h1>
      <p class="site-header__links">
        ${site.domain ? `<a href="https://${esc(site.domain)}" target="_blank" rel="noopener">${icon('globe')}${esc(site.domain)}</a>` : ''}
        <a href="https://github.com/${esc(site.repo)}" target="_blank" rel="noopener">${icon('github')}${esc(site.repo)}</a>
        <span class="mono small">${icon('folder')}${esc(site.path)}</span></p>
      <div class="badge-row">${stateBadge(site.state)} ${healthBadge(site.health)} ${versionPill(site.current_version, 'current')}
        ${site.disk ? `<span class="small muted">${icon('hard-drive')}${d('disk', { free: formatBytes(site.disk.free), total: formatBytes(site.disk.total) })}</span>` : ''}</div>
    </div>
    <div class="site-header__actions">
      <button type="button" class="btn btn--outline btn--small" data-health>${icon('activity')}${d('check_health')}</button>
      ${ready && site.rollback_version ? `<button type="button" class="btn btn--outline btn--small" data-rollback="${esc(site.rollback_version)}" ${busy ? 'disabled' : ''}>${icon('rollback')}${d('rollback_to', { version: site.rollback_version })}</button>` : ''}
      ${ready ? `<button type="button" class="btn btn--outline btn--small" data-backup ${busy ? 'disabled' : ''}>${icon('archive')}${d('backup_now')}</button>` : ''}
    </div>
  </header>
  ${site.state !== 'ready' ? `<div class="box box--${site.state === 'missing' ? 'red' : 'yellow'}">${icon('info')}<div>${t('sites.state.' + site.state + '_hint')}</div></div>` : ''}
  ${busy ? `<div class="box box--blue">${icon('activity')}<div>${t('jobs.in_progress')} ${jobStatusBadge(site.active_job.status)}
    <a href="#/jobs/${site.active_job.id}">${t('sites.overview.view_log')}</a></div></div>` : ''}`;
}

// ---------- Versions ----------
function releaseItem(site, release) {
  const v = (key, params) => t('sites.versions.' + key, params);
  const busy = !!site.active_job;
  const flags = [release.current ? `<span class="badge badge--normal">${icon('check')}${v('current')}</span>` : '',
    release.installed && !release.current ? `<span class="badge badge--admin">${icon('hard-drive')}${v('installed')}</span>` : '',
    release.prerelease ? '<span class="badge badge--yellow">pre-release</span>' : ''].join('');
  let actions = '';
  if (site.state === 'classic' || busy) {
    actions = '';
  } else if (release.current) {
    actions = '';
  } else if (release.installed) {
    actions = `<button type="button" class="btn btn--outline btn--small" data-activate="${esc(release.version)}">${icon('rollback')}${v('activate')}</button>
      <button type="button" class="icon-btn icon-btn--danger" data-delete-release="${esc(release.version)}" title="${v('delete')}" aria-label="${v('delete')}">${icon('trash')}</button>`;
  } else if (release.asset) {
    actions = `<button type="button" class="btn btn--primary btn--small" data-deploy="${esc(release.version)}">${icon('upload-cloud')}${v('deploy')}</button>`;
  } else {
    actions = `<span class="small muted">${v('no_asset', { pattern: site.settings.asset_pattern })}</span>`;
  }
  return `<li class="release${release.current ? ' release--current' : ''}">
    <div class="release__main">
      <div class="release__title">${versionPill(release.version, release.current ? 'current' : '')}<b>${esc(release.name && release.name !== release.tag ? release.name : '')}</b>${flags}</div>
      <small class="muted">${release.local_only ? v('local_only') : v('published', { date: formatDate(release.published_at) })}${release.asset ? ' · ' + esc(release.asset.name) + ' · ' + formatBytes(release.asset.size) : ''}</small>
      ${release.notes ? `<details class="release__notes"><summary>${v('notes')}</summary><pre>${esc(release.notes)}</pre></details>` : ''}
    </div>
    <div class="release__actions">${actions}</div></li>`;
}

async function versionsTab(container, site, isCurrent, refresh = false) {
  const v = (key, params) => t('sites.versions.' + key, params);
  container.innerHTML = '<div class="skeleton skeleton--table"></div>';
  const { data } = await get(`sites/${site.id}/releases${refresh ? '?refresh=1' : ''}`);
  if (!isCurrent()) return;
  const releases = data.releases;
  const withAsset = releases.filter((r) => r.asset);
  container.innerHTML = `
  ${site.state === 'classic' ? `<section class="card adopt-card">
    <h2 class="card__title">${icon('package')}${t('sites.adopt.title')}</h2><p>${t('sites.adopt.text')}</p>
    <label class="field"><span class="field__label">${t('sites.adopt.version')}</span>
      <select name="adopt_version">${withAsset.map((r) => `<option value="${esc(r.version)}">${esc(r.version)} — ${esc(r.name || r.tag)}</option>`).join('')}</select>
      <span class="field__help">${t('sites.adopt.version_help')}</span></label>
    <button type="button" class="btn btn--primary" data-adopt ${withAsset.length && !site.active_job ? '' : 'disabled'}>${icon('package')}${t('sites.adopt.submit')}</button>
  </section>` : ''}
  ${site.pre_rollout ? `<section class="card danger-zone"><div><h2>${icon('folder')}${t('sites.pre_rollout.title')}</h2><p class="muted">${t('sites.pre_rollout.text')}</p></div>
    <button type="button" class="btn btn--danger-outline" data-cleanup ${site.active_job ? 'disabled' : ''}>${icon('trash')}${t('sites.pre_rollout.submit')}</button></section>` : ''}
  <section class="card">
    <div class="card__header"><h2 class="card__title">${icon('github')}${v('title')}</h2>
      <button type="button" class="btn btn--ghost btn--small" data-refresh-releases>${icon('refresh')}${v('refresh')}</button></div>
    ${data.error ? `<div class="box box--red">${icon('alert')}<div>${v('github_error', { message: data.error })}</div></div>` : ''}
    ${releases.length ? `<ul class="release-list">${releases.map((r) => releaseItem(site, r)).join('')}</ul>` : `<p class="muted">${v('none')}</p>`}
  </section>`;
}

// ---------- History ----------
async function historyTab(container, site, isCurrent) {
  const h = (key) => t('sites.history.columns.' + key);
  container.innerHTML = '<div class="skeleton skeleton--table"></div>';
  const { data: jobs } = await get(`jobs?site=${site.id}&limit=100`);
  if (!isCurrent()) return;
  container.innerHTML = `<section class="card card--table">${jobs.length ? `<div class="table-scroll"><table class="table">
    <thead><tr><th>${h('job')}</th><th>${h('type')}</th><th>${h('version')}</th><th>${h('status')}</th><th>${h('actor')}</th><th>${h('started')}</th><th class="right">${h('duration')}</th><th>${h('result')}</th></tr></thead>
    <tbody>${jobs.map((j) => `<tr>
      <td data-label="${h('job')}"><a href="#/jobs/${j.id}" class="mono">#${j.id}</a></td>
      <td data-label="${h('type')}">${jobType(j.type)}</td>
      <td data-label="${h('version')}" class="mono">${j.from_version && j.to_version && j.from_version !== j.to_version ? `${esc(j.from_version)} → ${esc(j.to_version)}` : esc(j.to_version || j.version || '–')}</td>
      <td data-label="${h('status')}">${jobStatusBadge(j.status)}</td>
      <td data-label="${h('actor')}">${esc(j.actor || '–')}</td>
      <td data-label="${h('started')}" class="muted">${formatDateTime(j.started_at || j.created_at, true)}</td>
      <td data-label="${h('duration')}" class="right muted">${j.started_at ? duration(j.started_at, j.finished_at) : '–'}</td>
      <td data-label="${h('result')}" class="small">${esc(j.message || '')}</td></tr>`).join('')}</tbody></table></div>`
    : `<div class="empty">${icon('clock', 'icon--large')}<p>${t('sites.history.empty')}</p></div>`}</section>`;
}

// ---------- Backups ----------
async function backupsTab(container, site, isCurrent) {
  const b = (key, params) => t('sites.backups.' + key, params);
  const c = (key) => b('columns.' + key);
  container.innerHTML = '<div class="skeleton skeleton--table"></div>';
  const { data: backups } = await get(`sites/${site.id}/backups`);
  if (!isCurrent()) return;
  const configured = site.settings.backup_env_file || (site.settings.backup_paths || []).length;
  container.innerHTML = `<section class="card card--table">
    <div class="card__header card__header--padded"><div><h2 class="card__title">${icon('archive')}${b('title')}</h2>
      <p class="small muted">${b('lead', { keep: site.settings.backup_keep })}</p></div>
      ${site.state === 'ready' ? `<button type="button" class="btn btn--primary btn--small" data-backup ${site.active_job || !configured ? 'disabled' : ''}>${icon('archive')}${b('create')}</button>` : ''}</div>
    ${!configured ? `<div class="box box--yellow">${icon('info')}<div>${b('not_configured')}</div></div>` : ''}
    ${backups.length ? `<div class="table-scroll"><table class="table">
      <thead><tr><th>${c('time')}</th><th>${c('version')}</th><th>${c('contents')}</th><th class="right">${c('size')}</th><th>${c('reason')}</th><th></th></tr></thead>
      <tbody>${backups.map((x) => `<tr>
        <td data-label="${c('time')}"><b>${formatDateTime(x.created_at, true)}</b><small class="block muted mono">${esc(x.id)}</small></td>
        <td data-label="${c('version')}">${versionPill(x.version)}</td>
        <td data-label="${c('contents')}">${x.has_database ? `<span class="badge badge--admin">${icon('database')}${b('database')}</span>` : ''}
          ${x.has_files ? `<span class="badge badge--failed">${icon('folder')}${b('files')}</span>` : ''}</td>
        <td data-label="${c('size')}" class="right">${formatBytes(x.size_bytes)}</td>
        <td data-label="${c('reason')}" class="small muted">${esc(x.reason || '')}</td>
        <td class="right cell-actions"><button type="button" class="btn btn--outline btn--small" data-restore="${esc(x.id)}" ${site.active_job ? 'disabled' : ''}>${icon('rollback')}${b('restore')}</button>
          <button type="button" class="icon-btn icon-btn--danger" data-delete-backup="${esc(x.id)}" aria-label="${t('common.delete')}" title="${t('common.delete')}">${icon('trash')}</button></td>
      </tr>`).join('')}</tbody></table></div>` : `<div class="empty">${icon('archive', 'icon--large')}<p>${b('empty')}</p></div>`}
  </section>`;
}

// ---------- Settings ----------
function settingsTab(container, site, app) {
  const f = (key, params) => t('sites.form.' + key, params);
  container.innerHTML = `<form class="card site-form" novalidate>
    <h2 class="card__title">${icon('settings')}${f('edit_title')}</h2>
    ${siteFormHtml(site.settings, [], { isNew: false })}
    <div class="form-two form-confirm">${passwordField()}</div>
    <div class="box box--red" data-form-error hidden></div>
    <div class="form-actions"><button type="submit" class="btn btn--primary">${icon('check')}${f('save')}</button></div>
  </form>
  <section class="card danger-zone"><div><h2>${icon('trash')}${f('danger')}</h2><p class="muted">${f('danger_text')}</p></div>
    <button type="button" class="btn btn--danger-outline" data-delete-site ${site.active_job ? 'disabled' : ''}>${icon('trash')}${f('delete')}</button></section>`;
  const form = container.querySelector('form');
  attachSiteForm(form, [], { isNew: false });
  attachPasswordToggles(form.querySelector('.form-confirm'));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearErrors(form);
    const button = form.querySelector('[type=submit]');
    button.disabled = true;
    try {
      await put(`sites/${site.id}`, { ...readSiteForm(form), password: form.elements.password.value });
      toast(f('saved'), 'success');
      app.reloadPage();
    } catch (error) {
      showErrors(form, localizeFields(error));
    } finally {
      button.disabled = false;
    }
  });
}

export async function render(main, [siteId, tabName], app, isCurrent) {
  const tab = tabName || 'versions';
  main.innerHTML = '<div aria-busy="true"><div class="skeleton skeleton--title"></div><div class="skeleton skeleton--table"></div></div>';
  const { data: site } = await get('sites/' + siteId);
  if (!isCurrent()) return null;
  main.innerHTML = `<a class="back-link" href="#/">${icon('arrow-left')}${t('sites.detail.back')}</a>
    <div data-header>${header(site)}</div>
    <nav class="tabs tabs--page" role="tablist">${TABS.map(([key, name]) => `<a role="tab" class="tabs__item${key === tab ? ' tabs__item--active' : ''}" aria-selected="${key === tab}"
      href="#/sites/${esc(site.id)}/${key}">${icon(name)}${t('sites.detail.tabs.' + key)}</a>`).join('')}</nav>
    <div data-tab></div>`;
  const container = main.querySelector('[data-tab]');
  app.reloadPage = () => render(main, [siteId, tabName], app, isCurrent);
  if (tab === 'versions') await versionsTab(container, site, isCurrent);
  else if (tab === 'history') await historyTab(container, site, isCurrent);
  else if (tab === 'backups') await backupsTab(container, site, isCurrent);
  else settingsTab(container, site, app);

  const v = (key, params) => t('sites.versions.' + key, params);
  const job = (payload) => (form) => startJob(app, site.id, { ...payload(form), password: form.elements.password ? form.elements.password.value : undefined });

  const onClick = async (e) => {
    const target = e.target.closest('button');
    if (!target || target.disabled) return;
    const d = target.dataset;
    if ('health' in d) {
      target.disabled = true;
      try {
        const { data } = await get(`sites/${site.id}/health`);
        if (!data) return;
        toast(data.ok ? t('sites.detail.health_ok', { ms: data.ms }) : t('sites.detail.health_failed', { error: data.error }), data.ok ? 'success' : 'error');
        site.health = data;
        main.querySelector('[data-header]').innerHTML = header(site);
      } catch (error) { toast(error.message, 'error'); } finally { target.disabled = false; }
      return;
    }
    if ('refreshReleases' in d) { await versionsTab(container, site, isCurrent, true); return; }
    if (d.deploy) {
      await actionDialog({ title: v('deploy_title', { version: d.deploy }), message: v('deploy_text', { version: d.deploy }),
        extra: site.state === 'ready' ? checkbox('backup', v('deploy_backup'), site.settings.backup_before_deploy) : '',
        submitLabel: v('deploy_submit'),
        onSubmit: job((form) => ({ type: 'deploy', version: d.deploy, params: { backup: form.elements.backup ? form.elements.backup.checked : false } })) });
      return;
    }
    if (d.activate || d.rollback) {
      const version = d.activate || d.rollback;
      await actionDialog({ title: v('activate_title', { version }), message: v('activate_text', { version }), submitLabel: v('activate'),
        onSubmit: job(() => ({ type: 'rollback', version })) });
      return;
    }
    if (d.deleteRelease) {
      await actionDialog({ title: v('delete_title', { version: d.deleteRelease }), message: v('delete_text', { version: d.deleteRelease }),
        password: true, danger: true, submitLabel: t('common.delete'), onSubmit: job(() => ({ type: 'delete-release', version: d.deleteRelease })) });
      return;
    }
    if ('adopt' in d) {
      const version = main.querySelector('[name=adopt_version]').value;
      await actionDialog({ title: t('sites.adopt.confirm_title', { version }), message: t('sites.adopt.text'),
        extra: checkbox('backup', t('sites.adopt.backup'), true), password: true, submitLabel: t('sites.adopt.submit'),
        onSubmit: job((form) => ({ type: 'adopt', version, params: { backup: form.elements.backup.checked } })) });
      return;
    }
    if ('cleanup' in d) {
      await actionDialog({ title: t('sites.pre_rollout.confirm_title'), message: t('sites.pre_rollout.text'), password: true, danger: true,
        submitLabel: t('sites.pre_rollout.submit'), onSubmit: job(() => ({ type: 'cleanup-adopt' })) });
      return;
    }
    if ('backup' in d) {
      await actionDialog({ title: t('sites.backups.create') + '?', message: t('sites.backups.lead', { keep: site.settings.backup_keep }),
        submitLabel: t('sites.backups.create'), onSubmit: job(() => ({ type: 'backup', params: { reason: 'manual backup' } })) });
      return;
    }
    if (d.restore) {
      await actionDialog({ title: t('sites.backups.restore_title', { id: d.restore }), message: t('sites.backups.restore_text'),
        extra: checkbox('safety_backup', t('sites.backups.safety_backup'), true), password: true, danger: true, submitLabel: t('sites.backups.restore'),
        onSubmit: job((form) => ({ type: 'restore', params: { backup_id: d.restore, safety_backup: form.elements.safety_backup.checked } })) });
      return;
    }
    if (d.deleteBackup) {
      const done = await actionDialog({ title: t('sites.backups.delete_title', { id: d.deleteBackup }), message: t('sites.backups.delete_text'),
        password: true, danger: true, submitLabel: t('common.delete'),
        onSubmit: (form) => del(`sites/${site.id}/backups/${encodeURIComponent(d.deleteBackup)}`, { password: form.elements.password.value }) });
      if (done) { toast(t('sites.backups.deleted'), 'success'); await backupsTab(container, site, isCurrent); }
      return;
    }
    if ('deleteSite' in d) {
      const done = await actionDialog({ title: t('sites.form.delete_title', { name: site.name }), message: t('sites.form.danger_text'),
        password: true, danger: true, submitLabel: t('sites.form.delete'),
        onSubmit: (form) => del(`sites/${site.id}`, { password: form.elements.password.value }) });
      if (done) { toast(t('sites.form.deleted'), 'success'); app.go('/'); }
    }
  };
  main.addEventListener('click', onClick);
  return () => main.removeEventListener('click', onClick);
}

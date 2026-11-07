// Site details: status, quick actions (health check, rollback, backup) and the tabs Versions, History, Backups, Settings.
// Sites whose versions are uploaded here (zip, rar ...) get an upload box; hosting sites the .env editor, Laravel sites
// on a hosting a box for artisan commands.
import { del, get, post, put, sendBytes } from '../core/api.js';
import { t, tList } from '../core/i18n.js';
import { attachPasswordToggles, clearErrors, duration, esc, formatBytes, formatDate, formatDateTime, formDialog, icon, showErrors, toast } from '../core/ui.js';
import { actionDialog, checkbox, healthBadge, jobStatusBadge, jobType, localizeFields, passwordField, startJob, stateBadge, versionPill } from './shared/bits.js';
import { attachSiteForm, readSiteForm, siteFormHtml } from './shared/site-form.js';
import { serverBadge } from './overview.js';

const TABS = [['versions', 'git-branch'], ['history', 'clock'], ['backups', 'archive'], ['settings', 'settings']];
const ARCHIVES = '.zip,.rar,.7z,.tar,.tgz,.tbz2,.txz,.gz,.bz2,.xz';
const uploaded = (site) => (site.settings ? site.settings.source : site.source) === 'upload';
const envName = (file) => String(file || '.env').replace(/^@shared\//, '');        // Laravel: kept in the agent's data folder
const laravel = (site) => site.deploy_mode === 'hosting' && site.settings && site.settings.hosting_app === 'laravel';

function header(site) {
  const d = (key, params) => t('sites.detail.' + key, params);
  const busy = !!site.active_job;
  const ready = site.state === 'ready';
  return `<header class="site-header card">
    <div class="site-header__text">
      <h1>${esc(site.name)}</h1>
      <p class="site-header__links">
        ${site.domain ? `<a href="https://${esc(site.domain)}" target="_blank" rel="noopener">${icon('globe')}${esc(site.domain)}</a>` : ''}
        ${site.repo ? `<a href="https://github.com/${esc(site.repo)}" target="_blank" rel="noopener">${icon('github')}${esc(site.repo)}</a>` : ''}
        ${site.path ? `<span class="mono small">${icon('folder')}${esc(site.path)}</span>` : ''}
        ${site.hosting && site.hosting.url ? `<span class="mono small">${icon('external-link')}${esc(site.hosting.url)}</span>` : ''}</p>
      <div class="badge-row">${site.server && !site.server.local ? serverBadge(site.server) : ''} ${stateBadge(site.state)} ${healthBadge(site.health)} ${versionPill(site.current_version, 'current')}
        ${site.deploy_mode === 'compose' ? `<span class="badge badge--failed">${icon('layers')}Docker Compose</span>` : ''}
        ${site.deploy_mode === 'hosting' ? `<span class="badge badge--server">${icon('upload-cloud')}Hosting${laravel(site) ? ' · Laravel' : ''}</span>` : ''}
        ${uploaded(site) ? `<span class="badge badge--admin">${icon('upload')}${t('sites.upload.badge')}</span>` : ''}
        ${site.hosting && site.hosting.insecure ? `<span class="badge badge--yellow">${icon('alert')}${t('sites.hosting.insecure')}</span>` : ''}
        ${site.disk ? `<span class="small muted">${icon('hard-drive')}${d('disk', { free: formatBytes(site.disk.free), total: formatBytes(site.disk.total) })}</span>` : ''}</div>
    </div>
    <div class="site-header__actions">
      <button type="button" class="btn btn--outline btn--small" data-health>${icon('activity')}${d('check_health')}</button>
      ${ready && site.rollback_version ? `<button type="button" class="btn btn--outline btn--small" data-rollback="${esc(site.rollback_version)}" ${busy ? 'disabled' : ''}>${icon('rollback')}${d('rollback_to', { version: site.rollback_version })}</button>` : ''}
      ${ready || site.state === 'classic' ? `<button type="button" class="btn btn--outline btn--small" data-backup ${busy ? 'disabled' : ''}>${icon('archive')}${d('backup_now')}</button>` : ''}
    </div>
  </header>
  ${site.state !== 'ready' ? `<div class="box box--${site.state === 'missing' || site.state === 'unreachable' ? 'red' : 'yellow'}">${icon('info')}<div>${t('sites.state.' + site.state + '_hint')}
    ${site.state === 'unreachable' && site.hosting && site.hosting.error ? `<br><small>${esc(site.hosting.error)}</small>` : ''}</div></div>` : ''}
  ${site.deploy_mode === 'hosting' && site.state === 'unpaired' ? connectCard(site) : ''}
  ${busy ? `<div class="box box--blue">${icon('activity')}<div>${t('jobs.in_progress')} ${jobStatusBadge(site.active_job.status)}
    <a href="#/sites/${esc(site.id)}/jobs/${site.active_job.id}">${t('sites.overview.view_log')}</a></div></div>` : ''}`;
}

/**
 * Hosting site that is not connected: what is ready (an uploaded version, the .env of a Laravel site), then the agent
 * file: download it, put it in the website folder, open it (the page waits for the connection).
 */
function connectCard(site) {
  const hs = (key, params) => t('sites.hosting.' + key, params);
  const check = (ok, text) => `<li class="${ok ? 'text-success' : 'text-warning'}">${icon(ok ? 'check' : 'alert')} ${text}</li>`;
  const prep = [];
  if (uploaded(site)) prep.push(check(!!site.latest_version, site.latest_version ? hs('prep_upload_ok', { version: esc(site.latest_version) }) : hs('prep_upload_none')));
  if (laravel(site)) prep.push(check(!!(site.env && site.env.pending), site.env && site.env.pending ? hs('prep_env_ok') : hs('prep_env_none')));
  return `<section class="card connect-card"><h2 class="card__title">${icon('upload-cloud')}${hs('title')}</h2><p>${hs('lead')}</p>
    ${prep.length ? `<p><b>${hs('prep_title')}</b></p><ul class="check-list">${prep.join('')}</ul>` : ''}
    <ol class="bullet-list">${tList('sites.hosting.steps').map((x) => `<li>${x}</li>`).join('')}</ol>
    <div class="action-row"><button type="button" class="btn btn--primary" data-agent-file>${icon('download')}${hs('download')}</button>
      <span class="small muted live-note">${icon('refresh')}${hs('waiting')}</span></div></section>`;
}

/** The database changes a release brings to a hosting site, and how to apply them (adoption: they are there already). */
async function dbChanges(site, version, adopt) {
  if (site.deploy_mode !== 'hosting') return '';
  const v = (key, params) => t('sites.versions.' + key, params);
  let plan;
  try {
    plan = (await get(`sites/${site.id}/plan?version=${encodeURIComponent(version)}`)).data;
  } catch (error) {
    return `<div class="box box--yellow">${icon('alert')}<div>${esc(error.message)}</div></div>`;
  }
  const isLaravel = plan.kind === 'laravel';
  if (!plan.migrations.length) return `<p class="small muted">${icon('database')} ${v('db_none')}</p>`;
  return `<div class="db-changes"><p><b>${icon('database')} ${v(isLaravel ? 'db_title_laravel' : 'db_title', { n: plan.migrations.length, folder: plan.folder })}</b></p>
    ${plan.error ? `<p class="small text-warning">${v('db_unknown', { error: esc(plan.error) })}</p>` : ''}
    ${plan.migrations.map((m) => `<details class="release__notes"><summary class="mono">${esc(m.name)}${m.status === 'changed' ? ` · <span class="text-warning">${v('db_changed')}</span>` : ''}</summary>
      <pre>${esc(m.sql)}${m.truncated ? '\n…' : ''}</pre></details>`).join('')}
    <label class="checkbox"><input type="radio" name="migrations" value="auto" ${adopt ? '' : 'checked'}><span>${v(isLaravel ? 'db_auto_laravel' : 'db_auto')}</span></label>
    <label class="checkbox"><input type="radio" name="migrations" value="manual" ${adopt ? 'checked' : ''}><span>${v(adopt ? 'db_manual_adopt' : (isLaravel ? 'db_manual_laravel' : 'db_manual'))}</span></label></div>`;
}

const migrationsChoice = (form) => (form.elements.migrations ? form.elements.migrations.value : undefined);

/** absensi#003.rar -> 003, shop-v1.2.0.zip -> 1.2.0, otherwise the date and time (as the runner suggests it). */
export function suggestVersion(name) {
  const base = String(name || '').replace(/\.(zip|rar|7z|tar|tgz|tbz2|txz|tar\.gz|tar\.bz2|tar\.xz)$/i, '');
  const match = base.match(/(?:^|[^0-9A-Za-z])v?(\d+(?:\.\d+)*)$/);
  if (match) return match[1];
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

// ---------- Versions ----------
function releaseItem(site, release) {
  const v = (key, params) => t('sites.versions.' + key, params);
  const busy = !!site.active_job;
  const flags = [release.current ? `<span class="badge badge--normal">${icon('check')}${v('current')}</span>` : '',
    release.installed && !release.current ? `<span class="badge badge--admin">${icon('hard-drive')}${v('installed')}</span>` : '',
    release.prerelease ? '<span class="badge badge--yellow">pre-release</span>' : ''].join('');
  const removeUpload = release.uploaded && !release.current && !busy
    ? `<button type="button" class="icon-btn icon-btn--danger" data-delete-upload="${esc(release.version)}" title="${v('delete_upload')}" aria-label="${v('delete_upload')}">${icon('trash')}</button>` : '';
  let actions = '';
  if (site.state === 'classic' || site.state === 'unpaired' || site.state === 'unreachable' || busy) {
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
  const when = release.local_only ? v('local_only') : release.uploaded
    ? v('uploaded', { date: formatDateTime(release.published_at, true), actor: esc(release.actor || '–') })
    : v('published', { date: formatDate(release.published_at) });
  const asset = !release.asset ? '' : ' · ' + (release.asset.source ? v('source_code') : esc(release.asset.name) + ' · ' + formatBytes(release.asset.size));
  return `<li class="release${release.current ? ' release--current' : ''}">
    <div class="release__main">
      <div class="release__title">${versionPill(release.version, release.current ? 'current' : '')}<b>${esc(release.name && release.name !== release.tag && !release.uploaded ? release.name : '')}</b>${flags}</div>
      <small class="muted">${when}${asset}${release.uploaded && release.files ? ' · ' + v('files', { n: release.files }) : ''}${release.php ? ' · PHP ' + esc(release.php) : ''}</small>
      ${release.uploaded && release.removed_count ? `<details class="release__notes"><summary>${v('removed', { n: release.removed_count })}</summary><pre>${esc((release.removed || []).join('\n'))}${release.removed_count > (release.removed || []).length ? '\n…' : ''}</pre></details>` : ''}
      ${release.notes ? `<details class="release__notes"><summary>${v('notes')}</summary><pre>${esc(release.notes)}</pre></details>` : ''}
    </div>
    <div class="release__actions">${actions}${removeUpload}</div></li>`;
}

/** Upload a zip, rar, 7z or tar archive as a new version (sent in pieces; the runner unpacks and checks it). */
function uploadCard(site) {
  const u = (key, params) => t('sites.upload.' + key, params);
  return `<section class="card upload-card">
    <h2 class="card__title">${icon('upload')}${u('title')}</h2><p class="small muted">${u('lead')}</p>
    <form data-upload-form class="form-two" novalidate>
      <label class="field field--full"><span class="field__label">${u('file')}</span>
        <input type="file" name="file" accept="${ARCHIVES}"><span class="field__help">${u('file_help')}</span></label>
      <label class="field"><span class="field__label">${u('version')}</span>
        <input type="text" name="version" class="mono" placeholder="1.0.0" autocomplete="off" spellcheck="false"><span class="field__help">${u('version_help')}</span></label>
      <div class="field field--full"><progress max="100" value="0" hidden></progress><span class="small muted" data-upload-note></span></div>
      <div class="form-actions field--full"><button type="submit" class="btn btn--primary" ${site.active_job ? 'disabled' : ''}>${icon('upload')}${u('submit')}</button></div>
    </form></section>`;
}

/** The .env of a hosting site: on the hosting, or prepared here before the hosting is connected. */
function envCard(site) {
  const e = (key, params) => t('sites.env.' + key, params);
  const env = site.env || {};
  const status = env.on_hosting ? e('status_hosting') : env.pending ? e('status_dashboard') : e('status_none');
  return `<section class="card env-card"><div class="card__header"><div><h2 class="card__title">${icon('key')}${e('title', { file: esc(envName(env.file)) })}</h2>
      <p class="small ${env.on_hosting || env.pending ? 'muted' : 'text-warning'}">${status}</p></div>
    <button type="button" class="btn btn--outline btn--small" data-edit-env>${icon('edit')}${e('edit')}</button></div>
    ${laravel(site) ? `<p class="small muted">${e('lead_laravel')}</p>` : ''}</section>`;
}

/** artisan commands of the live release, run inside PHP on the hosting (the hosting has no terminal). */
function artisanCard(site) {
  const a = (key, params) => t('sites.artisan.' + key, params);
  return `<section class="card artisan-card"><h2 class="card__title">${icon('terminal')}${a('title')}</h2><p class="small muted">${a('lead')}</p>
    <form data-artisan-form class="artisan-row" novalidate><span class="mono muted">php artisan</span>
      <input type="text" name="command" class="mono" placeholder="migrate:status" autocomplete="off" spellcheck="false">
      <button type="submit" class="btn btn--outline btn--small" ${site.active_job || site.state !== 'ready' ? 'disabled' : ''}>${icon('terminal')}${a('run')}</button></form>
    <p class="small muted">${a('examples')}</p></section>`;
}

async function versionsTab(container, site, isCurrent, refresh = false) {
  const v = (key, params) => t('sites.versions.' + key, params);
  container.innerHTML = '<div class="skeleton skeleton--table"></div>';
  const { data } = await get(`sites/${site.id}/releases${refresh ? '?refresh=1' : ''}`);
  if (!isCurrent()) return;
  const releases = data.releases;
  const withAsset = releases.filter((r) => r.asset);
  const fromUploads = uploaded(site);
  container.innerHTML = `
  ${fromUploads ? uploadCard(site) : ''}
  ${site.deploy_mode === 'hosting' && site.env && (laravel(site) || site.settings.backup_env_file) ? envCard(site) : ''}
  ${site.state === 'classic' ? `<section class="card adopt-card">
    <h2 class="card__title">${icon('package')}${t('sites.adopt.title')}</h2><p>${t(site.deploy_mode === 'hosting' ? 'sites.adopt.text_hosting' : 'sites.adopt.text')}</p>
    <label class="field"><span class="field__label">${t('sites.adopt.version')}</span>
      <select name="adopt_version">${withAsset.map((r) => `<option value="${esc(r.version)}">${esc(r.version)} — ${esc(r.name || r.tag)}</option>`).join('')}</select>
      <span class="field__help">${t('sites.adopt.version_help')}</span></label>
    <button type="button" class="btn btn--primary" data-adopt ${withAsset.length && !site.active_job ? '' : 'disabled'}>${icon('package')}${t('sites.adopt.submit')}</button>
  </section>` : ''}
  ${site.pre_rollout ? `<section class="card danger-zone"><div><h2>${icon('folder')}${t('sites.pre_rollout.title')}</h2><p class="muted">${t('sites.pre_rollout.text')}</p></div>
    <button type="button" class="btn btn--danger-outline" data-cleanup ${site.active_job ? 'disabled' : ''}>${icon('trash')}${t('sites.pre_rollout.submit')}</button></section>` : ''}
  <section class="card">
    <div class="card__header"><h2 class="card__title">${icon(fromUploads ? 'package' : 'github')}${v(fromUploads ? 'title_upload' : 'title')}</h2>
      <button type="button" class="btn btn--ghost btn--small" data-refresh-releases>${icon('refresh')}${v(fromUploads ? 'refresh_upload' : 'refresh')}</button></div>
    ${data.error ? `<div class="box box--red">${icon('alert')}<div>${v('github_error', { message: data.error })}</div></div>` : ''}
    ${releases.length ? `<ul class="release-list">${releases.map((r) => releaseItem(site, r)).join('')}</ul>` : `<p class="muted">${v(fromUploads ? 'none_upload' : 'none')}</p>`}
  </section>
  ${laravel(site) && site.state === 'ready' ? artisanCard(site) : ''}`;
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
      <td data-label="${h('job')}"><a href="#/sites/${esc(site.id)}/jobs/${j.id}" class="mono">#${j.id}</a></td>
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
  const st = site.settings;
  const configured = st.backup_env_file || st.backup_db_service || ['mysql', 'postgres'].includes(st.backup_db) || (st.backup_paths || []).length
    || (st.backup_volumes || []).length;
  container.innerHTML = `<section class="card card--table">
    <div class="card__header card__header--padded"><div><h2 class="card__title">${icon('archive')}${b('title')}</h2>
      <p class="small muted">${b('lead', { keep: site.settings.backup_keep })}</p></div>
      ${site.state === 'ready' || site.state === 'classic' ? `<button type="button" class="btn btn--primary btn--small" data-backup ${site.active_job || !configured ? 'disabled' : ''}>${icon('archive')}${b('create')}</button>` : ''}</div>
    ${!configured ? `<div class="box box--yellow">${icon('info')}<div>${b('not_configured')}</div></div>` : ''}
    ${backups.length ? `<div class="table-scroll"><table class="table">
      <thead><tr><th>${c('time')}</th><th>${c('version')}</th><th>${c('contents')}</th><th class="right">${c('size')}</th><th>${c('reason')}</th><th></th></tr></thead>
      <tbody>${backups.map((x) => `<tr>
        <td data-label="${c('time')}"><b>${formatDateTime(x.created_at, true)}</b><small class="block muted mono">${esc(x.id)}</small></td>
        <td data-label="${c('version')}">${versionPill(x.version)}</td>
        <td data-label="${c('contents')}">${x.has_database ? `<span class="badge badge--admin">${icon('database')}${b('database')}</span>` : ''}
          ${x.has_files ? `<span class="badge badge--failed">${icon('folder')}${b('files')}</span>` : ''}
          ${x.volumes ? `<span class="badge badge--failed">${icon('hard-drive')}${b('volumes', { n: x.volumes })}</span>` : ''}</td>
        <td data-label="${c('size')}" class="right">${formatBytes(x.size_bytes)}</td>
        <td data-label="${c('reason')}" class="small muted">${esc(x.reason || '')}</td>
        <td class="right cell-actions">${site.state === 'ready' ? `<button type="button" class="btn btn--outline btn--small" data-restore="${esc(x.id)}" ${site.active_job ? 'disabled' : ''}>${icon('rollback')}${b('restore')}</button>` : ''}
          <button type="button" class="icon-btn icon-btn--danger" data-delete-backup="${esc(x.id)}" aria-label="${t('common.delete')}" title="${t('common.delete')}">${icon('trash')}</button></td>
      </tr>`).join('')}</tbody></table></div>` : `<div class="empty">${icon('archive', 'icon--large')}<p>${b('empty')}</p></div>`}
  </section>`;
}

// ---------- Settings ----------
function settingsTab(container, site, app) {
  const f = (key, params) => t('sites.form.' + key, params);
  container.innerHTML = `<form class="card site-form" novalidate>
    <h2 class="card__title">${icon('settings')}${f('edit_title')}</h2>
    ${siteFormHtml(site.settings, [], { isNew: false, token: { set: site.github_token_set, hint: site.github_token_hint } })}
    <div class="form-two form-confirm">${passwordField()}</div>
    <div class="box box--red" data-form-error hidden></div>
    <div class="form-actions"><button type="submit" class="btn btn--primary">${icon('check')}${f('save')}</button></div>
  </form>
  <section class="card danger-zone"><div><h2>${icon('trash')}${f('danger')}</h2><p class="muted">${f('danger_text')}</p></div>
    <button type="button" class="btn btn--danger-outline" data-delete-site ${site.active_job ? 'disabled' : ''}>${icon('trash')}${f('delete')}</button></section>`;
  const form = container.querySelector('form');
  attachSiteForm(form, [], { isNew: false, siteId: site.id });
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

/** Send the chosen archive in pieces, then let the runner turn it into a version (a job with its own log). */
async function uploadVersion(site, form, app) {
  const u = (key, params) => t('sites.upload.' + key, params);
  const file = form.elements.file.files[0];
  clearErrors(form);
  if (!file) { showErrors(form, { message: u('choose_file'), fields: { file: u('choose_file') } }); return; }
  const button = form.querySelector('[type=submit]');
  const bar = form.querySelector('progress');
  const note = form.querySelector('[data-upload-note]');
  button.disabled = true;
  bar.hidden = false;
  try {
    const { data: start } = await post(`sites/${site.id}/uploads`, { name: file.name, size: file.size, version: form.elements.version.value.trim() });
    let offset = 0;
    while (offset < file.size) {
      const piece = file.slice(offset, offset + start.chunk_size);
      let answer = null;
      for (let attempt = 0; attempt < 3 && !answer; attempt += 1) {
        try {
          answer = (await sendBytes(`sites/${site.id}/uploads/${start.id}?offset=${offset}`, piece)).data;
        } catch (error) {
          if (attempt === 2 || error.status === 422 || error.status === 409) throw error;
          await new Promise((resolve) => setTimeout(resolve, 1500));
        }
      }
      offset = answer.received;
      bar.value = Math.round((offset / file.size) * 100);
      note.textContent = u('progress', { done: formatBytes(offset), total: formatBytes(file.size) });
    }
    note.textContent = u('processing');
    const { data: job } = await post(`sites/${site.id}/uploads/${start.id}/finish`, {});
    toast(u('uploaded', { version: job.version }), 'success');
    app.go(`/sites/${site.id}/jobs/${job.id}`);
  } catch (error) {
    note.textContent = '';
    bar.hidden = true;
    showErrors(form, localizeFields(error));
  } finally {
    button.disabled = false;
  }
}

/** Open the .env (password), edit it in a wide dialog and save it to the hosting (or keep it here until it connects). */
async function editEnv(site, app) {
  const e = (key, params) => t('sites.env.' + key, params);
  let password = '';
  const env = await actionDialog({ title: e('open_title'), message: e('open_text'), password: true, submitLabel: e('open'),
    onSubmit: async (form) => { password = form.elements.password.value; return (await post(`sites/${site.id}/env/read`, { password })).data; } });
  if (!env) return;
  const text = env.exists ? env.content : (env.template || '');
  const where = env.exists ? e(env.where === 'hosting' ? 'where_hosting' : 'where_dashboard') : e(env.template ? 'template' : 'empty');
  const saved = await formDialog({ title: e('title', { file: envName(env.file) }), wide: true, submitLabel: e('save'),
    body: `<p class="small muted">${where}</p>${env.laravel ? `<p class="small muted">${e('laravel_hint')}</p>` : ''}
      <label class="field"><textarea name="content" rows="18" class="mono env-editor" spellcheck="false" autocomplete="off">${esc(text)}</textarea></label>`,
    onSubmit: async (form) => (await put(`sites/${site.id}/env`, { content: form.elements.content.value, password })).data });
  if (saved) {
    toast(e(saved.where === 'hosting' ? 'saved_hosting' : 'saved_dashboard'), 'success', 7000);
    app.reloadPage();
  }
}

export async function render(main, [siteId, tabName], app, isCurrent) {
  if (main.siteCleanup) main.siteCleanup();          // reloadPage() renders again: the listeners of the last render go first
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
      target.disabled = true;
      const changes = await dbChanges(site, d.deploy, false);
      target.disabled = false;
      await actionDialog({ title: v('deploy_title', { version: d.deploy }), message: v(uploaded(site) ? 'deploy_text_upload' : 'deploy_text', { version: d.deploy }),
        extra: changes + (site.state === 'ready' ? checkbox('backup', v('deploy_backup'), site.settings.backup_before_deploy) : ''),
        submitLabel: v('deploy_submit'),
        onSubmit: job((form) => ({ type: 'deploy', version: d.deploy,
          params: { backup: form.elements.backup ? form.elements.backup.checked : false, migrations: migrationsChoice(form) } })) });
      return;
    }
    if ('agentFile' in d) {
      const hs = (key, params) => t('sites.hosting.' + key, params);
      const file = await actionDialog({ title: hs('download_title'), message: hs('download_text'), password: true, submitLabel: hs('download'),
        onSubmit: async (form) => (await post(`sites/${site.id}/agent-file`, { password: form.elements.password.value })).data });
      if (file) {
        const link = document.createElement('a');
        link.href = URL.createObjectURL(new Blob([file.content], { type: 'application/octet-stream' }));
        link.download = file.filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 10000);
        toast(hs('downloaded', { name: file.filename }), 'success', 9000);
      }
      return;
    }
    if ('editEnv' in d) { await editEnv(site, app); return; }
    if (d.deleteUpload) {
      const done = await actionDialog({ title: v('delete_upload_title', { version: d.deleteUpload }), message: v('delete_upload_text'),
        password: true, danger: true, submitLabel: t('common.delete'),
        onSubmit: (form) => del(`sites/${site.id}/uploads/${encodeURIComponent(d.deleteUpload)}`, { password: form.elements.password.value }) });
      if (done) { toast(v('upload_deleted', { version: d.deleteUpload }), 'success'); await versionsTab(container, site, isCurrent); }
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
      target.disabled = true;
      const changes = await dbChanges(site, version, true);
      target.disabled = false;
      await actionDialog({ title: t('sites.adopt.confirm_title', { version }), message: t(site.deploy_mode === 'hosting' ? 'sites.adopt.text_hosting' : 'sites.adopt.text'),
        extra: changes + checkbox('backup', t('sites.adopt.backup'), true), password: true, submitLabel: t('sites.adopt.submit'),
        onSubmit: job((form) => ({ type: 'adopt', version, params: { backup: form.elements.backup.checked, migrations: migrationsChoice(form) } })) });
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
  const onSubmit = async (e) => {
    const form = e.target;
    if (form.matches('[data-upload-form]')) {
      e.preventDefault();
      await uploadVersion(site, form, app);
    } else if (form.matches('[data-artisan-form]')) {
      e.preventDefault();
      const a = (key, params) => t('sites.artisan.' + key, params);
      const command = form.elements.command.value.trim().replace(/^php\s+artisan\s+/, '');
      if (!command) { form.elements.command.focus(); return; }
      await actionDialog({ title: a('confirm_title'), message: a('confirm_text', { command: `<code>php artisan ${esc(command)}</code>` }), password: true,
        submitLabel: a('run'), onSubmit: job(() => ({ type: 'artisan', params: { command } })) });
    }
  };
  const onChange = (e) => {
    const input = e.target;
    if (input.name === 'file' && input.closest('[data-upload-form]') && input.files[0]) {
      input.form.elements.version.value = suggestVersion(input.files[0].name);
    }
  };
  main.addEventListener('click', onClick);
  main.addEventListener('submit', onSubmit);
  main.addEventListener('change', onChange);
  let waiting = null;
  if (site.deploy_mode === 'hosting' && site.state === 'unpaired') {       // the agent's "Connect" button finishes elsewhere
    waiting = setInterval(async () => {
      if (document.hidden || document.querySelector('.overlay')) return;
      try {
        const { data } = await get('sites/' + site.id);
        if (data.state !== 'unpaired' && isCurrent()) app.reloadPage();
      } catch (e) { /* try again */ }
    }, 5000);
  }
  const cleanup = () => {
    main.removeEventListener('click', onClick);
    main.removeEventListener('submit', onSubmit);
    main.removeEventListener('change', onChange);
    if (waiting) clearInterval(waiting);
    if (main.siteCleanup === cleanup) main.siteCleanup = null;
  };
  main.siteCleanup = cleanup;
  return cleanup;
}

// Site settings form, used by "Add site" and the Settings tab of a site. With a preset, only the basics need filling in;
// the folder layout, deployment steps, Docker Compose, health check and backups are folded away under "Advanced".
// Every site has its own read-only GitHub token: write-only here, kept by the runner, never shown again.
import { get, post } from '../../core/api.js';
import { t, tList } from '../../core/i18n.js';
import { esc, icon } from '../../core/ui.js';
import { checkbox } from './bits.js';

const f = (key, params) => t('sites.form.' + key, params);
const lines = (list) => (list || []).join('\n');

function field(name, label, value, { help = '', type = 'text', placeholder = '', wide = false, mono = false, attrs = '' } = {}) {
  return `<label class="field${wide ? ' field--full' : ''}"><span class="field__label">${label}</span>
    <input type="${type}" name="${name}" value="${esc(value ?? '')}" placeholder="${esc(placeholder)}" ${mono ? 'class="mono"' : ''} autocomplete="off" spellcheck="false" ${attrs}>
    ${help ? `<span class="field__help">${help}</span>` : ''}</label>`;
}

function select(name, label, value, options, help = '', attrs = '') {
  return `<label class="field" ${attrs}><span class="field__label">${label}</span>
    <select name="${name}">${options.map(([key, text]) => `<option value="${esc(key)}" ${key === value ? 'selected' : ''}>${esc(text)}</option>`).join('')}</select>
    ${help ? `<span class="field__help">${help}</span>` : ''}</label>`;
}

/** The server a new site is installed on (only when the dashboard controls more than one). */
function serverField(servers, current) {
  if (!servers || servers.length < 2) return '';
  return `<label class="field field--full"><span class="field__label">${f('server')}</span>
    <select name="server">${servers.map((x) => `<option value="${esc(x.id)}" ${x.id === current ? 'selected' : ''} ${x.status ? '' : 'disabled'}>${esc(x.local ? f('server_local') : x.name)}${x.status ? '' : ' · ' + f('server_offline')}</option>`).join('')}</select>
    <span class="field__help">${f('server_help')}</span></label>`;
}

function area(name, label, list, help) {
  return `<label class="field field--full"><span class="field__label">${label}</span>
    <textarea name="${name}" rows="3" class="mono" spellcheck="false">${esc(lines(list))}</textarea>
    ${help ? `<span class="field__help">${help}</span>` : ''}</label>`;
}

function tokenField(isNew, token) {
  const saved = !isNew && token && token.set;
  const status = isNew ? '' : saved ? f('github_token_saved', { hint: esc(token.hint) }) : f('github_token_none');
  return `<label class="field field--full"><span class="field__label">${f('github_token')}</span>
      <input type="password" name="github_token" class="mono" value="" placeholder="github_pat_…" autocomplete="new-password" spellcheck="false"
        data-1p-ignore data-lpignore="true">
      ${status ? `<span class="field__help"><b>${status}</b></span>` : ''}
      <span class="field__help">${f('github_token_help')}</span></label>
    ${saved ? `<div class="field field--full">${checkbox('remove_github_token', f('github_token_remove'), false)}</div>` : ''}
    <details class="tech-section field--full"><summary>${icon('info')}${f('github_token_how')}</summary>
      <ol class="bullet-list">${tList('sites.form.github_token_steps').map((x) => `<li>${esc(x)}</li>`).join('')}</ol></details>`;
}

export function siteFormHtml(site, presets, { isNew = false, token = null, servers = null, server = 'local' } = {}) {
  const s = site || {};
  return `
  <fieldset class="section">
    <legend>${icon('server')}${f('basics')}</legend>
    <div class="form-two">
      ${isNew ? serverField(servers, server) : ''}
      ${isNew ? `<label class="field field--full"><span class="field__label">${f('preset')}</span>
        <select name="preset">${presets.map((p) => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join('')}<option value="">${f('preset_custom')}</option></select>
        <span class="field__help">${f('preset_help')}</span></label>` : ''}
      ${field('domain', f('domain'), s.domain, { help: f('domain_help'), placeholder: 'example.com' })}
      ${field('name', f('name'), s.name, { placeholder: 'Example' })}
      ${isNew ? field('id', f('id'), s.id, { help: f('id_help'), placeholder: 'example', mono: true }) : ''}
      ${field('path', f('path'), s.path, { help: f('path_help'), placeholder: '/var/www/example.com', mono: true, wide: !isNew })}
      <label class="field field--full"><span class="field__label">${f('repo')}</span>
        <span class="field__combo"><input type="text" name="repo" class="mono" value="${esc(s.repo || '')}" placeholder="owner/repository" autocomplete="off" spellcheck="false">
        <button type="button" class="btn btn--outline btn--small" data-check-repo>${icon('github')}${f('check_repo')}</button></span>
        <span class="field__help">${f('repo_help')}</span><span class="field__help" data-repo-result></span></label>
      ${tokenField(isNew, token)}
      ${select('deploy_mode', f('deploy_mode'), s.deploy_mode || 'files', [['files', f('deploy_mode_files')], ['compose', f('deploy_mode_compose')]], f('deploy_mode_help'))}
      ${select('source', f('source'), s.source || 'asset', [['asset', f('source_asset')], ['source', f('source_source')]], f('source_help'))}
      <div class="field--full" data-show-source="asset">${field('asset_pattern', f('asset_pattern'), s.asset_pattern || '*.zip', { help: f('asset_pattern_help'), mono: true, wide: true })}</div>
    </div>
  </fieldset>
  <details class="tech-section" ${isNew ? '' : 'open'}>
    <summary>${icon('settings')}${f('advanced')}</summary>
    <fieldset class="section">
      <legend>${icon('folder')}${f('layout')}</legend>
      <div class="form-two">
        ${field('docroot_link', f('docroot_link'), s.docroot_link, { help: f('docroot_link_help'), mono: true })}
        ${field('docroot_target', f('docroot_target'), s.docroot_target, { help: f('docroot_target_help'), mono: true })}
        ${area('shared', f('shared'), s.shared, f('shared_help'))}
        ${field('web_user', f('web_user'), s.web_user, { mono: true })}
        ${field('keep_releases', f('keep_releases'), s.keep_releases, { type: 'number', attrs: 'min="2" max="20"' })}
      </div>
    </fieldset>
    <fieldset class="section">
      <legend>${icon('terminal')}${f('steps')}</legend>
      <div class="form-two">
        ${field('build', f('build'), s.build, { help: f('build_help'), mono: true, wide: true })}
        ${field('activate', f('activate'), s.activate, { help: f('activate_help'), mono: true, wide: true })}
        ${field('services', f('services'), (s.services || []).join(', '), { help: f('services_help'), mono: true })}
        <div class="field">${checkbox('reload_php_fpm', f('reload_php_fpm'), s.reload_php_fpm)}</div>
      </div>
    </fieldset>
    <fieldset class="section" data-show-mode="compose">
      <legend>${icon('layers')}${f('compose')}</legend>
      <div class="form-two">
        ${area('compose_files', f('compose_files'), s.compose_files, f('compose_files_help'))}
        ${field('compose_project', f('compose_project'), s.compose_project, { help: f('compose_project_help'), mono: true })}
        <div class="field">${checkbox('compose_build', f('compose_build'), s.compose_build !== false)}<span class="field__help">${f('compose_build_help')}</span></div>
      </div>
    </fieldset>
    <fieldset class="section">
      <legend>${icon('activity')}${f('health')}</legend>
      <div class="form-two">
        ${field('health_url', f('health_url'), s.health_url, { help: f('health_url_help'), mono: true, wide: true })}
        ${field('health_contains', f('health_contains'), s.health_contains, { help: f('health_contains_help'), mono: true, wide: true })}
      </div>
    </fieldset>
    <fieldset class="section">
      <legend>${icon('archive')}${f('backup')}</legend>
      <div class="form-two">
        <div class="field field--full">${checkbox('backup_before_deploy', f('backup_before_deploy'), s.backup_before_deploy)}</div>
        ${select('backup_db', f('backup_db'), s.backup_db || 'auto', [['auto', f('backup_db_auto')], ['mysql', 'MySQL / MariaDB'], ['postgres', 'PostgreSQL'], ['none', f('backup_db_none')]], f('backup_db_help'))}
        ${field('backup_db_service', f('backup_db_service'), s.backup_db_service, { help: f('backup_db_service_help'), mono: true, placeholder: 'db' })}
        ${field('backup_env_file', f('backup_env_file'), s.backup_env_file, { help: f('backup_env_file_help'), mono: true })}
        ${field('backup_keep', f('backup_keep'), s.backup_keep, { type: 'number', attrs: 'min="1" max="50"' })}
        ${area('backup_paths', f('backup_paths'), s.backup_paths, f('backup_paths_help'))}
        ${area('backup_exclude', f('backup_exclude'), s.backup_exclude, '')}
        ${area('backup_volumes', f('backup_volumes'), s.backup_volumes, f('backup_volumes_help'))}
      </div>
    </fieldset>
  </details>`;
}

const LIST_FIELDS = ['shared', 'backup_paths', 'backup_exclude', 'compose_files', 'backup_volumes'];
const TEXT_FIELDS = ['id', 'name', 'domain', 'path', 'repo', 'asset_pattern', 'docroot_link', 'docroot_target', 'web_user', 'build', 'activate',
  'health_url', 'health_contains', 'backup_env_file', 'deploy_mode', 'source', 'compose_project', 'backup_db', 'backup_db_service'];

export function readSiteForm(form) {
  const e = form.elements;
  const data = {};
  TEXT_FIELDS.forEach((name) => { if (e[name]) data[name] = e[name].value.trim(); });
  LIST_FIELDS.forEach((name) => { if (e[name]) data[name] = e[name].value.split('\n').map((v) => v.trim()).filter(Boolean); });
  data.services = e.services.value.split(',').map((v) => v.trim()).filter(Boolean);
  data.keep_releases = Number(e.keep_releases.value);
  data.backup_keep = Number(e.backup_keep.value);
  data.reload_php_fpm = e.reload_php_fpm.checked;
  data.backup_before_deploy = e.backup_before_deploy.checked;
  data.compose_build = e.compose_build.checked;
  if (e.preset && e.preset.value) data.preset = e.preset.value;
  if (e.server) data.server = e.server.value;
  const token = e.github_token.value.trim();
  if (token) data.github_token = token;
  else if (e.remove_github_token && e.remove_github_token.checked) data.remove_github_token = true;
  return data;
}

/** Show the settings that belong to the chosen deploy mode and release source. */
function syncSections(form) {
  const e = form.elements;
  form.querySelectorAll('[data-show-mode]').forEach((el) => { el.hidden = el.dataset.showMode !== e.deploy_mode.value; });
  form.querySelectorAll('[data-show-source]').forEach((el) => { el.hidden = el.dataset.showSource !== e.source.value; });
}

/**
 * Preset defaults, domain -> folder / name / id suggestions, the server choice (its presets and site folders), and
 * the "Check access" button. ``servers``: the list of GET /servers (new sites only).
 */
export function attachSiteForm(form, presets, { isNew = false, siteId = '', servers = null } = {}) {
  const e = form.elements;
  const roots = () => {
    const chosen = (servers || []).find((x) => x.id === (e.server ? e.server.value : 'local')) || (servers || [])[0];
    return (chosen && chosen.status && chosen.status.site_roots) || [];
  };
  const fill = (settings) => {
    Object.entries(settings).forEach(([name, value]) => {
      const input = e[name];
      if (!input || ['id', 'name', 'domain', 'path'].includes(name)) return;
      if (input.type === 'checkbox') input.checked = !!value;
      else if (LIST_FIELDS.includes(name)) input.value = lines(value);
      else if (name === 'services') input.value = (value || []).join(', ');
      else input.value = value ?? '';
    });
    syncSections(form);
  };
  e.deploy_mode.addEventListener('change', () => syncSections(form));
  e.source.addEventListener('change', () => syncSections(form));
  syncSections(form);
  let suggestPath = () => {};
  if (isNew) {
    const apply = () => {
      const preset = presets.find((p) => p.id === e.preset.value);
      if (preset) fill(preset.settings);
    };
    e.preset.addEventListener('change', apply);
    apply();
    if (e.server) {
      e.server.addEventListener('change', async () => {
        try {
          presets = (await get('presets?server=' + encodeURIComponent(e.server.value))).data;   // its web server user
          apply();
          suggestPath();
        } catch (error) { /* the select shows which servers answer */ }
      });
    }
    let touched = { name: false, id: false, path: false };
    ['name', 'id', 'path'].forEach((n) => e[n].addEventListener('input', () => { touched[n] = true; }));
    suggestPath = () => {
      const domain = e.domain.value.trim().toLowerCase();
      const root = (roots()[0] || '/www/wwwroot').replace(/\/+$/, '');
      if (!touched.path) e.path.value = domain ? root + '/' + domain : '';
    };
    e.domain.addEventListener('input', () => {
      const domain = e.domain.value.trim().toLowerCase();
      const first = domain.split('.')[0] || '';
      suggestPath();
      if (!touched.id) e.id.value = first.replace(/[^a-z0-9-]/g, '').slice(0, 40);
      if (!touched.name) e.name.value = first ? first.charAt(0).toUpperCase() + first.slice(1) : '';
    });
    const help = e.path.closest('.field').querySelector('.field__help');
    const showRoots = () => { if (help && roots().length) help.textContent = f('path_help_roots', { roots: roots().join(', ') }); };
    showRoots();
    if (e.server) e.server.addEventListener('change', showRoots);
  }
  const result = form.querySelector('[data-repo-result]');
  form.querySelector('[data-check-repo]').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    result.className = 'field__help';
    result.textContent = t('common.loading');
    try {
      const check = { repo: e.repo.value.trim(), asset_pattern: e.asset_pattern.value.trim(), source: e.source.value };
      if (e.github_token.value.trim()) check.github_token = e.github_token.value.trim();
      if (siteId) check.site = siteId;                 // editing: without a new token the saved one is tried
      else if (e.server) check.server = e.server.value;
      const { data } = await post('github/check', check);
      if (!data.ok) {
        result.className = 'field__help text-danger';
        result.textContent = f('repo_fail', { message: data.message });
      } else if (!data.with_asset) {
        result.className = 'field__help text-warning';
        result.textContent = data.releases ? f('repo_no_asset', { pattern: e.asset_pattern.value.trim() }) : f('repo_no_release');
      } else {
        result.className = 'field__help text-success';
        result.textContent = f('repo_ok', { n: data.releases, latest: data.latest });
      }
    } catch (error) {
      result.className = 'field__help text-danger';
      result.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}

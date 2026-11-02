// Overview: every registered site with its health, current and latest version, and one-click update (refreshes every 30 s).
import { get } from '../core/api.js';
import { t } from '../core/i18n.js';
import { esc, icon, timeAgo } from '../core/ui.js';
import { actionDialog, checkbox, healthBadge, jobStatusBadge, jobType, startJob, stateBadge, versionPill } from './shared/bits.js';

const REFRESH_SECONDS = 30;

function summary(sites) {
  const s = (key) => t('sites.overview.summary.' + key);
  const online = sites.filter((x) => x.health && x.health.ok).length;
  const updates = sites.filter((x) => x.update_available).length;
  const problems = sites.filter((x) => (x.health && !x.health.ok) || (x.last_job && x.last_job.status === 'failed') || x.state === 'missing').length;
  return `<section class="stat-grid">
    <div class="stat"><span>${icon('server')}${s('total')}</span><b>${sites.length}</b></div>
    <div class="stat stat--normal"><span>${icon('check-circle')}${s('online')}</span><b>${online}</b></div>
    <div class="stat"><span>${icon('upload-cloud')}${s('updates')}</span><b>${updates}</b></div>
    <div class="stat stat--problem"><span>${icon('alert')}${s('problems')}</span><b>${problems}</b></div></section>`;
}

function card(site) {
  const c = (key, params) => t('sites.card.' + key, params);
  const job = site.last_job;
  const services = Object.entries(site.services || {});
  const busy = !!site.active_job;
  let action = '';
  if (busy) {
    action = `<a class="btn btn--outline btn--small" href="#/jobs/${site.active_job.id}">${jobStatusBadge(site.active_job.status)} ${t('sites.overview.view_log')}</a>`;
  } else if (site.state === 'classic') {
    action = `<a class="btn btn--primary btn--small" href="#/sites/${site.id}/versions">${icon('package')}${t('sites.adopt.submit')}</a>`;
  } else if (site.update_available || (site.state === 'empty' && site.latest_version)) {
    action = `<button type="button" class="btn btn--primary btn--small" data-deploy="${esc(site.id)}" data-version="${esc(site.latest_version)}">${icon('upload-cloud')}${c('update_to', { version: site.latest_version })}</button>`;
  } else if (site.current_version) {
    action = `<span class="badge badge--normal">${icon('check')}${c('up_to_date')}</span>`;
  }
  return `<article class="site-card card">
    <header class="site-card__header">
      <div><h2><a href="#/sites/${esc(site.id)}">${esc(site.name)}</a></h2>
        ${site.domain ? `<a class="site-card__domain" href="https://${esc(site.domain)}" target="_blank" rel="noopener">${esc(site.domain)}${icon('external-link')}</a>` : ''}</div>
      ${healthBadge(site.health)}
    </header>
    <dl class="site-card__versions">
      <div><dt>${c('current')}</dt><dd>${versionPill(site.current_version, 'current')}</dd></div>
      <div><dt>${c('latest')}</dt><dd>${site.latest_error ? `<span class="muted" title="${esc(site.latest_error)}">${icon('alert')} GitHub</span>` : versionPill(site.latest_version, site.update_available ? 'new' : '')}</dd></div>
    </dl>
    <div class="site-card__meta">
      ${site.state !== 'ready' ? stateBadge(site.state) : ''}
      ${services.map(([name, state]) => `<span class="badge ${state === 'active' ? 'badge--normal' : state === 'unknown' ? 'badge--failed' : 'badge--danger'}">${esc(name)}: ${esc(state)}</span>`).join('')}
    </div>
    <p class="small muted site-card__job">${job ? c('last_job', { type: jobType(job.type), status: t('jobs.status.' + job.status).toLowerCase(), time: timeAgo(job.finished_at || job.created_at) }) : c('no_jobs')}</p>
    <footer class="site-card__actions">${action}<a class="btn btn--ghost btn--small" href="#/sites/${esc(site.id)}">${c('open')}${icon('arrow-right')}</a></footer>
  </article>`;
}

export async function render(main, _params, app, isCurrent) {
  main.innerHTML = '<div aria-busy="true"><div class="skeleton skeleton--title"></div><div class="stat-grid">' + '<div class="skeleton skeleton--stat"></div>'.repeat(4) + '</div><div class="skeleton skeleton--table"></div></div>';
  let sites = [];

  const draw = () => {
    const active = sites.find((s) => s.active_job);
    main.innerHTML = `
<header class="page-title">
  <div><h1>${t('sites.overview.title')}</h1><p class="muted">${t('sites.overview.lead', { n: sites.length })}
    <span class="live-note">${icon('refresh')}${t('sites.overview.auto_refresh')}</span></p></div>
  <div class="action-row"><a class="btn btn--primary" href="#/sites/new">${icon('plus')}${t('sites.overview.add')}</a></div>
</header>
${active ? `<div class="box box--blue">${icon('activity')}<div>${t('sites.overview.active_job', { id: active.active_job.id, type: jobType(active.active_job.type), site: active.name })}
  <a href="#/jobs/${active.active_job.id}">${t('sites.overview.view_log')}</a></div></div>` : ''}
${sites.length ? summary(sites) + `<section class="site-grid">${sites.map(card).join('')}</section>`
    : `<section class="empty-large">${icon('server', 'icon--large')}<h2>${t('sites.overview.empty_title')}</h2><p>${t('sites.overview.empty_text')}</p>
      <a class="btn btn--primary" href="#/sites/new">${icon('plus')}${t('sites.overview.add')}</a></section>`}`;
  };

  const load = async () => {
    const { data } = await get('sites');
    if (!isCurrent()) return;
    sites = data;
    draw();
  };
  await load();

  const onClick = async (e) => {
    const button = e.target.closest('[data-deploy]');
    if (!button) return;
    const site = sites.find((s) => s.id === button.dataset.deploy);
    const version = button.dataset.version;
    await actionDialog({
      title: t('sites.versions.deploy_title', { version }),
      message: t('sites.versions.deploy_text', { version }),
      extra: site.state === 'ready' ? checkbox('backup', t('sites.versions.deploy_backup'), true) : '',
      submitLabel: t('sites.versions.deploy_submit'),
      onSubmit: (form) => startJob(app, site.id, { type: 'deploy', version, params: { backup: form.elements.backup ? form.elements.backup.checked : false } }),
    });
  };
  main.addEventListener('click', onClick);
  const timer = setInterval(() => { if (!document.hidden && !document.querySelector('.overlay')) load().catch(() => {}); }, REFRESH_SECONDS * 1000);
  return () => { clearInterval(timer); main.removeEventListener('click', onClick); };
}

// Connect the agent file of a hosting site. The agent's page on the hosting ("Connect to the dashboard") opens
// #/sites/<site>/connect?agent=<address of the file>; the administrator confirms with the password, then the runner
// calls the agent with its one-time key and gives it its own secret. The newest version can be deployed right away.
import { get, post } from '../core/api.js';
import { t } from '../core/i18n.js';
import { attachPasswordToggles, clearErrors, esc, icon, showErrors, toast } from '../core/ui.js';
import { checkbox, passwordField } from './shared/bits.js';

export async function render(main, [siteId], app, isCurrent) {
  const c = (key, params) => t('sites.connect.' + key, params);
  const agent = new URLSearchParams(location.hash.split('?')[1] || '').get('agent') || '';
  const { data: site } = await get('sites/' + siteId);
  if (!isCurrent()) return null;
  const problem = site.deploy_mode !== 'hosting' ? c('not_hosting') : !agent ? c('no_agent') : '';
  const insecure = agent.startsWith('http://');
  main.innerHTML = `
<a class="back-link" href="#/sites/${esc(site.id)}">${icon('arrow-left')}${esc(site.name)}</a>
<header class="page-title"><div><h1>${c('title')}</h1><p class="muted">${c('lead')}</p></div></header>
<form class="card" novalidate>
  <dl class="figure-row">
    <div><dt>${c('site')}</dt><dd>${esc(site.name)} <small class="muted mono">${esc(site.id)}</small></dd></div>
    <div><dt>${c('agent')}</dt><dd class="mono small">${esc(agent || '–')}</dd></div>
  </dl>
  ${site.hosting && site.hosting.connected ? `<div class="box box--yellow">${icon('info')}<div>${c('already', { url: site.hosting.url || '' })}</div></div>` : ''}
  ${insecure && !problem ? `<div class="box box--yellow">${icon('alert')}<div>${c('insecure')}</div></div>` : ''}
  ${problem ? `<div class="box box--red">${icon('alert')}<div>${problem}</div></div>` : `
  ${site.latest_version ? `<div class="field">${checkbox('deploy_now', c('deploy_now', { version: esc(site.latest_version) }), true)}
    <span class="field__help">${c('deploy_now_help')}</span></div>` : ''}
  ${passwordField()}
  <div class="box box--red" data-form-error hidden></div>
  <div class="form-actions"><button type="submit" class="btn btn--primary">${icon('check')}${c('submit')}</button></div>`}
</form>`;
  const form = main.querySelector('form');
  if (problem) return null;
  attachPasswordToggles(form);
  form.elements.password.focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearErrors(form);
    const button = form.querySelector('[type=submit]');
    button.disabled = true;
    try {
      const password = form.elements.password.value;
      const { data: details } = await post(`sites/${site.id}/agent/connect`, { url: agent, password });
      const deployNow = form.elements.deploy_now && form.elements.deploy_now.checked;
      const version = details.latest_version;
      const envMissing = details.settings && details.settings.hosting_app === 'laravel' && details.env && !details.env.on_hosting;
      if (deployNow && version && details.state === 'empty' && !envMissing) {
        const { data: job } = await post(`sites/${site.id}/jobs`, { type: 'deploy', version, params: { migrations: 'auto' }, password });
        toast(c('deploying', { name: site.name, version }), 'success', 8000);
        app.go(`/sites/${site.id}/jobs/${job.id}`);
        return;
      }
      if (deployNow && version && details.state === 'classic') toast(c('needs_adopt', { name: site.name }), 'info', 10000);
      else if (deployNow && version && envMissing) toast(c('needs_env', { name: site.name }), 'info', 10000);
      else toast(c(details.hosting && details.hosting.mode === 'pull' ? 'done_pull' : 'done', { name: site.name }), 'success', 8000);
      app.go(`/sites/${site.id}`);
    } catch (error) {
      showErrors(form, error);
    } finally {
      button.disabled = false;
    }
  });
  return null;
}

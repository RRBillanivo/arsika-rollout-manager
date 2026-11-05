// Connect the agent file of a hosting site. The agent's page on the hosting ("Connect to the dashboard") opens
// #/sites/<site>/connect?agent=<address of the file>; the administrator confirms with the password, then the runner
// calls the agent with its one-time key and gives it its own secret.
import { get, post } from '../core/api.js';
import { t } from '../core/i18n.js';
import { attachPasswordToggles, clearErrors, esc, icon, showErrors, toast } from '../core/ui.js';
import { passwordField } from './shared/bits.js';

export async function render(main, [siteId], app, isCurrent) {
  const c = (key, params) => t('sites.connect.' + key, params);
  const agent = new URLSearchParams(location.hash.split('?')[1] || '').get('agent') || '';
  const { data: site } = await get('sites/' + siteId);
  if (!isCurrent()) return null;
  const problem = site.deploy_mode !== 'hosting' ? c('not_hosting') : !agent ? c('no_agent') : '';
  main.innerHTML = `
<a class="back-link" href="#/sites/${esc(site.id)}">${icon('arrow-left')}${esc(site.name)}</a>
<header class="page-title"><div><h1>${c('title')}</h1><p class="muted">${c('lead')}</p></div></header>
<form class="card" novalidate>
  <dl class="figure-row">
    <div><dt>${c('site')}</dt><dd>${esc(site.name)} <small class="muted mono">${esc(site.id)}</small></dd></div>
    <div><dt>${c('agent')}</dt><dd class="mono small">${esc(agent || '–')}</dd></div>
  </dl>
  ${site.hosting && site.hosting.connected ? `<div class="box box--yellow">${icon('info')}<div>${c('already', { url: site.hosting.url || '' })}</div></div>` : ''}
  ${problem ? `<div class="box box--red">${icon('alert')}<div>${problem}</div></div>` : `${passwordField()}
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
      await post(`sites/${site.id}/agent/connect`, { url: agent, password: form.elements.password.value });
      toast(c('done', { name: site.name }), 'success', 8000);
      app.go(`/sites/${site.id}`);
    } catch (error) {
      showErrors(form, error);
    } finally {
      button.disabled = false;
    }
  });
  return null;
}

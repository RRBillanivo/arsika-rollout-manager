// Add a site: choose the server (when the dashboard controls more than one) and a preset, fill in the domain and the
// GitHub repository; advanced settings come from the preset.
import { get, post } from '../core/api.js';
import { t } from '../core/i18n.js';
import { attachPasswordToggles, clearErrors, icon, showErrors, toast } from '../core/ui.js';
import { localizeFields, passwordField } from './shared/bits.js';
import { attachSiteForm, readSiteForm, siteFormHtml } from './shared/site-form.js';

export async function render(main, _params, app, isCurrent) {
  const servers = (await get('servers')).data;
  const first = servers.find((x) => x.status) || servers[0];
  const server = first ? first.id : 'local';
  const { data: presets } = await get('presets?server=' + encodeURIComponent(server));
  if (!isCurrent()) return null;
  main.innerHTML = `
<a class="back-link" href="#/">${icon('arrow-left')}${t('sites.detail.back')}</a>
<header class="page-title"><div><h1>${t('sites.form.new_title')}</h1><p class="muted">${t('sites.form.new_lead')}</p></div></header>
<form class="card site-form" novalidate>
  ${siteFormHtml({}, presets, { isNew: true, servers, server })}
  <div class="form-two form-confirm">${passwordField()}</div>
  <div class="box box--red" data-form-error hidden></div>
  <div class="form-actions"><button type="submit" class="btn btn--primary">${icon('check')}${t('sites.form.create')}</button></div>
</form>`;
  const form = main.querySelector('form');
  attachSiteForm(form, presets, { isNew: true, servers });
  attachPasswordToggles(form.querySelector('.form-confirm'));
  form.elements.domain.focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearErrors(form);
    const button = form.querySelector('[type=submit]');
    button.disabled = true;
    try {
      const { data } = await post('sites', { ...readSiteForm(form), server: form.elements.server ? form.elements.server.value : server,
        password: form.elements.password.value });
      toast(t('sites.form.created', { name: data.name }), 'success');
      app.go(`/sites/${data.id}`);
    } catch (error) {
      const details = form.querySelector('details');
      if (error.fields && Object.keys(error.fields).some((name) => details.querySelector(`[name="${name}"]`))) details.open = true;
      const shown = localizeFields(error);
      if (shown.fields && shown.fields.id) {              // the id field is named site_id in the form
        shown.fields.site_id = shown.fields.id;
        delete shown.fields.id;
      }
      showErrors(form, shown);
    } finally {
      button.disabled = false;
    }
  });
  return null;
}

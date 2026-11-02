// Add a site: choose a preset, fill in the domain and the GitHub repository; advanced settings come from the preset.
import { get, post } from '../core/api.js';
import { t } from '../core/i18n.js';
import { attachPasswordToggles, clearErrors, icon, showErrors, toast } from '../core/ui.js';
import { localizeFields, passwordField } from './shared/bits.js';
import { attachSiteForm, readSiteForm, siteFormHtml } from './shared/site-form.js';

export async function render(main, _params, app, isCurrent) {
  const { data: presets } = await get('presets');
  if (!isCurrent()) return null;
  main.innerHTML = `
<a class="back-link" href="#/">${icon('arrow-left')}${t('sites.detail.back')}</a>
<header class="page-title"><div><h1>${t('sites.form.new_title')}</h1><p class="muted">${t('sites.form.new_lead')}</p></div></header>
<form class="card site-form" novalidate>
  ${siteFormHtml({}, presets, { isNew: true })}
  <div class="form-two form-confirm">${passwordField()}</div>
  <div class="box box--red" data-form-error hidden></div>
  <div class="form-actions"><button type="submit" class="btn btn--primary">${icon('check')}${t('sites.form.create')}</button></div>
</form>`;
  const form = main.querySelector('form');
  attachSiteForm(form, presets, { isNew: true });
  attachPasswordToggles(form.querySelector('.form-confirm'));
  form.elements.domain.focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearErrors(form);
    const button = form.querySelector('[type=submit]');
    button.disabled = true;
    try {
      const { data } = await post('sites', { ...readSiteForm(form), password: form.elements.password.value });
      toast(t('sites.form.created', { name: data.name }), 'success');
      app.go(`/sites/${data.id}`);
    } catch (error) {
      const details = form.querySelector('details');
      if (error.fields && Object.keys(error.fields).some((name) => details.querySelector(`[name="${name}"]`))) details.open = true;
      showErrors(form, localizeFields(error));
    } finally {
      button.disabled = false;
    }
  });
  return null;
}

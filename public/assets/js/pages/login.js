// Sign-in of the server administrator (username + password).
import { post } from '../core/api.js';
import { t } from '../core/i18n.js';
import { attachPasswordToggles, clearErrors, icon, showErrors, toast } from '../core/ui.js';

export async function render(main, _params, app) {
  main.innerHTML = `<section class="auth">
    <aside class="auth__panel auth__panel--simple" aria-hidden="true"><div class="auth__panel-content">
      <img src="assets/img/logo.svg" alt="" width="52" height="52">
      <h2>${t('auth.panel_title')}</h2><p>${t('auth.panel_text')}</p></div></aside>
    <form class="auth__form card" novalidate>
      <h1>${t('auth.title')}</h1>
      <p class="muted">${t('auth.lead')}</p>
      <label class="field"><span class="field__label">${t('auth.username')}</span>
        <input type="text" name="username" autocomplete="username" autocapitalize="off" spellcheck="false" value="admin" required></label>
      <label class="field"><span class="field__label">${t('auth.password')}</span>
        <input type="password" name="password" autocomplete="current-password" required></label>
      <div class="box box--red" data-form-error hidden></div>
      <button class="btn btn--primary btn--block btn--large" type="submit">${icon('log-in')}${t('auth.submit')}</button>
      <p class="small muted">${t('auth.forgot')}</p>
    </form></section>`;
  const form = main.querySelector('form');
  attachPasswordToggles(form);
  form.elements.password.focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearErrors(form);
    const button = form.querySelector('[type=submit]');
    button.disabled = true;
    try {
      const { data } = await post('auth/login', { username: form.elements.username.value.trim(), password: form.elements.password.value });
      toast(t('auth.welcome', { name: data.user.name }), 'success');
      await app.afterLogin(data);
    } catch (error) {
      showErrors(form, error);
      form.elements.password.select();
    } finally {
      button.disabled = false;
    }
  });
  return null;
}

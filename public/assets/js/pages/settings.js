// Settings: runner state, administrator password, versions. GitHub tokens belong to the sites (site form).
import { get, put } from '../core/api.js';
import { t } from '../core/i18n.js';
import { attachPasswordToggles, clearErrors, esc, formatBytes, icon, passwordStrength, showErrors, toast } from '../core/ui.js';

function uptime(seconds) {
  const hours = Math.floor(seconds / 3600), minutes = Math.floor((seconds % 3600) / 60);
  return hours >= 24 ? t('settings.uptime.days', { d: Math.floor(hours / 24), h: hours % 24 })
    : hours ? t('settings.uptime.hours', { h: hours, m: minutes }) : t('settings.uptime.minutes', { m: minutes });
}

export async function render(main, _params, app, isCurrent) {
  const s = (key, params) => t('settings.' + key, params);
  main.innerHTML = '<div aria-busy="true"><div class="skeleton skeleton--title"></div><div class="skeleton skeleton--table"></div></div>';
  let data = null, runnerError = null;
  try {
    data = (await get('settings')).data;
  } catch (error) {
    runnerError = error;
  }
  if (!isCurrent()) return null;
  const runner = data && data.runner;
  const min = (app.info && app.info.password_min_length) || 12;

  main.innerHTML = `
<header class="page-title"><div><h1>${s('title')}</h1></div></header>
<div class="grid-2 grid-2--top">
  <div class="stack">
    <section class="card">
      <h2 class="card__title">${icon('server')}${s('runner.title')}</h2>
      ${runner ? `<div class="box box--green">${icon('check-circle')}<div>${s('runner.ok')}</div></div>
        <dl class="figure-row figure-row--3">
          <div><dt>${s('runner.version')}</dt><dd>${esc(runner.version)}</dd></div>
          <div><dt>${s('runner.python')}</dt><dd>${esc(runner.python)}</dd></div>
          <div><dt>${s('runner.uptime')}</dt><dd>${uptime(runner.uptime_seconds)}</dd></div>
          ${runner.disk ? `<div><dt>${s('runner.disk')}</dt><dd>${formatBytes(runner.disk.free)} / ${formatBytes(runner.disk.total)}</dd></div>` : ''}
        </dl>
        <p class="small muted">${s('runner.roots')}: <span class="mono">${(runner.site_roots || []).map(esc).join(', ')}</span></p>`
      : `<div class="box box--red">${icon('alert')}<div><b>${s('runner.down')}</b> ${esc(runnerError ? runnerError.message : '')}</div></div>`}
      ${runner ? `<p class="small muted">${s('runner.tokens', { n: runner.github_tokens || 0, sites: runner.sites || 0 })}</p>` : ''}
    </section>
  </div>
  <div class="stack">
    <form class="card" data-password-form novalidate>
      <h2 class="card__title">${icon('lock')}${s('account.title')}</h2>
      <label class="field"><span class="field__label">${s('account.current')}</span><input type="password" name="current_password" autocomplete="current-password"></label>
      <label class="field"><span class="field__label">${s('account.new')}</span><input type="password" name="password" autocomplete="new-password">
        <div class="password-meter" data-meter><i></i></div><span class="field__help">${s('account.rules', { min })}</span></label>
      <label class="field"><span class="field__label">${s('account.confirm')}</span><input type="password" name="password_confirmation" autocomplete="new-password"></label>
      <div class="box box--red" data-form-error hidden></div>
      <div class="form-actions"><button type="submit" class="btn btn--outline">${icon('lock')}${s('account.save')}</button></div>
    </form>
    <section class="card">
      <h2 class="card__title">${icon('info')}${s('about.title')}</h2>
      <dl class="figure-row">
        <div><dt>${s('about.dashboard')}</dt><dd>${esc(data ? data.dashboard_version : (app.info.app || {}).version)}</dd></div>
        ${data ? `<div><dt>${s('about.php')}</dt><dd>${esc(data.php)}</dd></div>` : ''}
      </dl>
    </section>
  </div>
</div>`;

  const passwordForm = main.querySelector('[data-password-form]');
  attachPasswordToggles(passwordForm);
  const meter = passwordForm.querySelector('[data-meter]');
  passwordForm.elements.password.addEventListener('input', (e) => { meter.dataset.score = e.target.value ? String(passwordStrength(e.target.value).score) : ''; });
  passwordForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearErrors(passwordForm);
    const f = passwordForm.elements;
    try {
      const { data: result } = await put('account/password', { current_password: f.current_password.value, password: f.password.value,
        password_confirmation: f.password_confirmation.value });
      toast(result.message, 'success', 6000);
      passwordForm.reset();
      meter.dataset.score = '';
    } catch (error) {
      showErrors(passwordForm, error);
    }
  });
  return null;
}

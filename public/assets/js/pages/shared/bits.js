// Small building blocks shared by the pages: status badges, password-confirmed dialogs and job start.
import { post } from '../../core/api.js';
import { lang, t } from '../../core/i18n.js';
import { esc, formDialog, icon, toast } from '../../core/ui.js';

const JOB_STATUS = { queued: 'badge--yellow', running: 'badge--admin', succeeded: 'badge--normal', failed: 'badge--danger' };
const STATE = { ready: 'badge--normal', classic: 'badge--yellow', empty: 'badge--failed', missing: 'badge--danger' };

export const jobType = (type) => t('jobs.types.' + type);

export function jobStatusBadge(status) {
  const spin = status === 'running' ? '<span class="spinner spinner--tiny"></span>' : '';
  return `<span class="badge ${JOB_STATUS[status] || 'badge--failed'}">${spin}${t('jobs.status.' + status)}</span>`;
}

export function stateBadge(state) {
  return `<span class="badge ${STATE[state] || 'badge--failed'}">${t('sites.state.' + state)}</span>`;
}

/** Online / problem / not checked, with the response time. */
export function healthBadge(health) {
  if (!health) return `<span class="presence presence--offline"><i></i>${t('sites.health.unknown')}</span>`;
  if (health.ok) return `<span class="presence presence--online"><i></i>${t('sites.health.online')} <small>${t('sites.health.ms', { ms: health.ms })}</small></span>`;
  return `<span class="presence presence--down" title="${esc(health.error || '')}"><i></i>${t('sites.health.down')}</span>`;
}

export const versionPill = (version, kind = '') => (version
  ? `<span class="version-pill ${kind ? 'version-pill--' + kind : ''}">${icon('git-commit')}${esc(version)}</span>` : '<span class="muted">–</span>');

/** Password field for dialogs that need confirmation. */
export function passwordField() {
  return `<label class="field"><span class="field__label">${t('common.confirm_password')}</span>
    <input type="password" name="password" autocomplete="current-password" required>
    <span class="field__help">${t('common.confirm_password_help')}</span></label>`;
}

/**
 * Dialog with a message, optional extra fields and (when ``password``) the password confirmation.
 * onSubmit(form) does the request; resolves with its result, or null when cancelled.
 */
export function actionDialog({ title, message = '', extra = '', password = false, submitLabel, danger = false, onSubmit }) {
  return formDialog({
    title, submitLabel, danger,
    body: `${message ? `<p>${message}</p>` : ''}${extra}${password ? passwordField() : ''}`,
    onSubmit,
  });
}

/** Queue a job on the runner of the site's server and open its live log. */
export async function startJob(app, siteId, payload) {
  const { data } = await post(`sites/${siteId}/jobs`, payload);
  toast(t('sites.versions.job_started', { id: data.id }), 'success');
  app.go(`/sites/${siteId}/jobs/${data.id}`);
  return data;
}

/** Runner validation messages are English; in Indonesian the dashboard shows its own text for known fields. */
export function localizeFields(error) {
  if (!error || !error.fields || lang() !== 'id') return error;
  const fields = {};
  Object.entries(error.fields).forEach(([name, message]) => {
    const local = t('sites.field_errors.' + name);
    fields[name] = local.startsWith('sites.') ? message : local;
  });
  error.fields = fields;
  return error;
}

export function checkbox(name, label, checked) {
  return `<label class="checkbox"><input type="checkbox" name="${name}" ${checked ? 'checked' : ''}><span>${label}</span></label>`;
}

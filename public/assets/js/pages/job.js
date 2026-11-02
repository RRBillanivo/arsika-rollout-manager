// Live job log: the runner's log is read in pieces (from the last byte offset) every second until the job ends.
// Job numbers count per server: #/sites/<site>/jobs/<n> (#/jobs/<n> = a job of this server, links of version 1.1).
import { get } from '../core/api.js';
import { t } from '../core/i18n.js';
import { duration, esc, formatDateTime, icon, toast } from '../core/ui.js';
import { jobStatusBadge, jobType } from './shared/bits.js';

const POLL_MS = 1000;
const LINE_CLASSES = { '==': 'log__step', '!!': 'log__error', '--': 'log__info', '  ': 'log__output' };

/** "HH:MM:SS == text" lines -> coloured HTML. */
function formatLog(text) {
  return text.split('\n').filter((line) => line !== '').map((line) => {
    const time = line.slice(0, 8), mark = line.slice(9, 11), rest = line.slice(12);
    return `<span class="log__line ${LINE_CLASSES[mark] || ''}"><span class="log__time">${esc(time)}</span>${esc(rest)}</span>`;
  }).join('');
}

export async function render(main, params, app, isCurrent) {
  const p = (key, params) => t('jobs.page.' + key, params);
  const [siteId, jobId] = params.length > 1 ? params : ['', params[0]];
  const path = siteId ? `sites/${siteId}/jobs/${jobId}` : `jobs/${jobId}`;
  let offset = 0;
  let text = '';
  let follow = true;
  let timer = null;
  let job = (await get(`${path}?offset=0`)).data;
  if (!isCurrent()) return null;

  main.innerHTML = `
<a class="back-link" href="#/sites/${esc(job.site_id)}/history">${icon('arrow-left')}${p('back', { site: job.site_id })}</a>
<header class="page-title"><div><h1>${p('title', { id: job.id })} · ${jobType(job.type)}${job.version ? ' ' + esc(job.version) : ''}</h1>
  <p class="muted" data-summary></p></div>
  <div class="action-row"><label class="checkbox"><input type="checkbox" data-follow checked><span>${p('follow')}</span></label>
    <button type="button" class="btn btn--outline btn--small" data-copy>${icon('file')}${p('copy')}</button></div></header>
<div data-result></div>
<section class="card log-card"><div class="log" data-log tabindex="0" aria-live="polite"></div></section>`;
  const logBox = main.querySelector('[data-log]');
  const summary = main.querySelector('[data-summary]');
  const result = main.querySelector('[data-result]');

  const drawSummary = () => {
    const change = job.from_version && job.to_version && job.from_version !== job.to_version ? `${job.from_version} → ${job.to_version}` : (job.to_version || job.version || '');
    summary.innerHTML = `${jobStatusBadge(job.status)} · ${p('site')}: <a href="#/sites/${esc(job.site_id)}">${esc(job.site_id)}</a>
      ${change ? ` · ${p('change')}: <b class="mono">${esc(change)}</b>` : ''} · ${p('actor')}: ${esc(job.actor || '–')}
      · ${p('started')}: ${job.started_at ? formatDateTime(job.started_at, true) : '–'} · ${p('duration')}: ${job.started_at ? duration(job.started_at, job.finished_at) : '–'}`;
    if (job.status === 'succeeded' || job.status === 'failed') {
      const ok = job.status === 'succeeded';
      result.innerHTML = `<div class="box box--${ok ? 'green' : 'red'}">${icon(ok ? 'check-circle' : 'x-circle')}<div>
        ${p(ok ? 'done_ok' : 'done_failed', { message: job.message || '' })}</div></div>`;
    }
  };
  const append = (chunk) => {
    if (!chunk) return;
    text += chunk;
    logBox.insertAdjacentHTML('beforeend', formatLog(chunk));
    if (follow) logBox.scrollTop = logBox.scrollHeight;
  };
  const apply = (data) => {
    job = data;
    append(data.log.text);
    offset = data.log.offset;
    if (!text) logBox.innerHTML = `<span class="log__line log__info">${p('log_empty')}</span>`;
    else if (logBox.firstChild && logBox.firstChild.textContent === p('log_empty')) logBox.firstChild.remove();
    drawSummary();
  };
  apply(job);

  const poll = async () => {
    if (!isCurrent()) return;
    try {
      const { data } = await get(`${path}?offset=${offset}`);
      if (!isCurrent()) return;
      const wasRunning = job.status === 'queued' || job.status === 'running';
      apply(data);
      if (data.status === 'queued' || data.status === 'running' || data.log.offset < data.log.size) {
        timer = setTimeout(poll, POLL_MS);
      } else if (wasRunning) {
        toast(p(data.status === 'succeeded' ? 'done_ok' : 'done_failed', { message: data.message || '' }), data.status === 'succeeded' ? 'success' : 'error', 8000);
        if (app.loadInfo) app.loadInfo().catch(() => {});
      }
    } catch (error) {
      timer = setTimeout(poll, POLL_MS * 3);
    }
  };
  if (job.status === 'queued' || job.status === 'running' || job.log.offset < job.log.size) timer = setTimeout(poll, POLL_MS);

  main.querySelector('[data-follow]').addEventListener('change', (e) => { follow = e.target.checked; if (follow) logBox.scrollTop = logBox.scrollHeight; });
  main.querySelector('[data-copy]').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(text); toast(p('copied'), 'success'); } catch (e) { toast(e.message, 'error'); }
  });
  return () => clearTimeout(timer);
}

// Servers: this server and the other servers the dashboard controls, with the state of each runner. Another server
// runs only the runner (deploy/install.sh --agent); it is added with its address, secret and certificate key pin,
// which "connection-info" prints on that server. The connection is tested before anything is saved.
import { del, get, post, put } from '../core/api.js';
import { t, tList } from '../core/i18n.js';
import { attachPasswordToggles, esc, formatBytes, formDialog, icon, showErrors, clearErrors, toast } from '../core/ui.js';
import { passwordField } from './shared/bits.js';

const s = (key, params) => t('servers.' + key, params);

function facts(status) {
  const rows = [[s('facts.version'), status.version], [s('facts.python'), status.python],
    [s('facts.platform'), status.platform === 'aapanel' ? 'aaPanel' : 'Linux'],
    [s('facts.docker'), status.docker_compose ? 'Docker Compose ' + status.docker_compose : s('facts.no_docker')],
    [s('facts.sites'), String(status.sites)],
    [s('facts.disk'), status.disk ? `${formatBytes(status.disk.free)} / ${formatBytes(status.disk.total)}` : '–']];
  return `<dl class="figure-row figure-row--3">${rows.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value || '–')}</dd></div>`).join('')}</dl>
    <p class="small muted">${s('facts.roots')}: <span class="mono">${(status.site_roots || []).map(esc).join(', ')}</span>
      · ${s('facts.host')}: <span class="mono">${esc(status.hostname || '')}</span>
      ${status.tls ? ' · HTTPS' : ''}${(status.allow_from || []).length ? ` · ${s('facts.allow', { list: status.allow_from.join(', ') })}` : ''}</p>`;
}

function card(server) {
  const title = server.local ? s('local') : server.name;
  return `<article class="card server-card">
    <header class="card__header"><div><h2 class="card__title">${icon('server')}${esc(title)}</h2>
      <p class="small muted"><span class="mono">${esc(server.url)}</span>${server.tls_pin ? ' · ' + s('pinned') : ''}</p></div>
      ${server.local ? `<span class="badge badge--failed">${s('local_badge')}</span>` : `<div class="action-row">
        <button type="button" class="btn btn--outline btn--small" data-edit="${esc(server.id)}">${icon('edit')}${s('edit')}</button>
        <button type="button" class="icon-btn icon-btn--danger" data-remove="${esc(server.id)}" title="${s('remove')}" aria-label="${s('remove')}">${icon('trash')}</button></div>`}
    </header>
    ${server.status ? `<div class="box box--green">${icon('check-circle')}<div>${s('connected')}</div></div>${facts(server.status)}`
    : `<div class="box box--red">${icon('alert')}<div><b>${s('not_connected')}</b> ${esc(server.error || '')}</div></div>`}
  </article>`;
}

function serverForm(server) {
  const editing = !!server;
  return `
    <label class="field"><span class="field__label">${s('form.name')}</span>
      <input type="text" name="name" value="${esc(server ? server.name : '')}" placeholder="VM2" autocomplete="off"></label>
    <label class="field"><span class="field__label">${s('form.url')}</span>
      <input type="text" name="url" class="mono" value="${esc(server ? server.url : '')}" placeholder="https://192.168.1.20:8790" autocomplete="off" spellcheck="false">
      <span class="field__help">${s('form.url_help')}</span></label>
    <label class="field"><span class="field__label">${s('form.secret')}</span>
      <input type="password" name="secret" class="mono" value="" autocomplete="new-password" spellcheck="false" data-1p-ignore data-lpignore="true"
        placeholder="${editing ? esc(s('form.secret_keep')) : ''}">
      <span class="field__help">${s('form.secret_help')}</span></label>
    <label class="field"><span class="field__label">${s('form.pin')}</span>
      <input type="text" name="tls_pin" class="mono" value="${esc(server ? server.tls_pin : '')}" placeholder="sha256//…" autocomplete="off" spellcheck="false">
      <span class="field__help">${s('form.pin_help')}</span></label>
    <div class="field"><button type="button" class="btn btn--outline btn--small" data-test>${icon('zap')}${s('form.test')}</button>
      <span class="field__help" data-test-result></span></div>
    ${passwordField()}`;
}

function readForm(form) {
  const e = form.elements;
  return { name: e.name.value.trim(), url: e.url.value.trim(), secret: e.secret.value.trim(), tls_pin: e.tls_pin.value.trim(),
    password: e.password.value };
}

/** Add or edit a server; "Test connection" tries the values without saving them. */
function openForm(server, onSaved) {
  const dialog = formDialog({
    title: server ? s('form.edit_title', { name: server.name }) : s('form.add_title'),
    body: serverForm(server),
    submitLabel: server ? s('form.save') : s('form.add'),
    wide: true,
    onSubmit: async (form) => {
      const data = readForm(form);
      const { data: saved } = server ? await put('servers/' + server.id, data) : await post('servers', data);
      toast(server ? s('saved', { name: saved.name }) : s('added', { name: saved.name, n: saved.site_count || 0 }), 'success');
      return saved;
    },
  });
  const form = document.querySelector('.overlay form');
  attachPasswordToggles(form);
  const result = form.querySelector('[data-test-result]');
  form.querySelector('[data-test]').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    clearErrors(form);
    button.disabled = true;
    result.className = 'field__help';
    result.textContent = t('common.loading');
    try {
      const { password, ...values } = readForm(form);
      const { data } = await post('servers/test', server ? { ...values, server: server.id } : values);
      result.className = 'field__help text-success';
      result.textContent = s('form.test_ok', { version: data.status.version, host: data.status.hostname, n: data.status.sites });
    } catch (error) {
      result.className = 'field__help text-danger';
      result.textContent = '';
      showErrors(form, error);
    } finally {
      button.disabled = false;
    }
  });
  return dialog.then((saved) => { if (saved) onSaved(); });
}

export async function render(main, _params, app, isCurrent) {
  main.innerHTML = '<div aria-busy="true"><div class="skeleton skeleton--title"></div><div class="skeleton skeleton--table"></div></div>';
  let servers = (await get('servers')).data;
  if (!isCurrent()) return null;

  const draw = () => {
    main.innerHTML = `
<header class="page-title">
  <div><h1>${s('title')}</h1><p class="muted">${s('lead')}</p></div>
  <div class="action-row"><button type="button" class="btn btn--primary" data-add>${icon('plus')}${s('add')}</button></div>
</header>
<section class="stack">${servers.map(card).join('')}</section>
<details class="tech-section card">
  <summary>${icon('book')}${s('howto.title')}</summary>
  <ol class="bullet-list">${tList('servers.howto.steps').map((x) => `<li>${x}</li>`).join('')}</ol>
  <p class="small muted">${s('howto.note')}</p>
</details>`;
  };
  draw();
  const reload = async () => {
    servers = (await get('servers')).data;
    if (isCurrent()) draw();
  };

  const onClick = async (e) => {
    const button = e.target.closest('button');
    if (!button) return;
    if ('add' in button.dataset) await openForm(null, reload);
    if (button.dataset.edit) await openForm(servers.find((x) => x.id === button.dataset.edit), reload);
    if (button.dataset.remove) {
      const server = servers.find((x) => x.id === button.dataset.remove);
      const done = await formDialog({
        title: s('remove_title', { name: server.name }),
        body: `<p>${s('remove_text')}</p>${passwordField()}`,
        submitLabel: s('remove'),
        danger: true,
        onSubmit: (form) => del('servers/' + server.id, { password: form.elements.password.value }),
      });
      if (done) {
        toast(s('removed', { name: server.name }), 'success');
        await reload();
      }
    }
  };
  main.addEventListener('click', onClick);
  return () => main.removeEventListener('click', onClick);
}

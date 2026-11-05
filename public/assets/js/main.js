// Single-page app shell: hash routes (guests see only the sign-in page), menu, account menu, theme and language toggles,
// runner status banner and the bootstrap data from GET /api/v1/app.
import { get, onSessionExpired, post, setCsrfToken } from './core/api.js';
import { LANGUAGES, lang, setLang, t } from './core/i18n.js';
import { Router } from './core/router.js';
import { setupThemeToggle } from './core/theme.js';
import { avatar, esc, icon, toast } from './core/ui.js';
import * as loginPage from './pages/login.js';
import * as overviewPage from './pages/overview.js';
import * as siteNewPage from './pages/site-new.js';
import * as sitePage from './pages/site.js';
import * as jobPage from './pages/job.js';
import * as settingsPage from './pages/settings.js';
import * as activityPage from './pages/activity.js';
import * as serversPage from './pages/servers.js';
import * as connectPage from './pages/connect.js';

// access: guest (signed out only) or auth; title = key in common.titles; menu = key in common.menu
const router = new Router([
  { pattern: /^\/login$/, page: loginPage, access: 'guest', menu: '', title: 'login' },
  { pattern: /^\/$/, page: overviewPage, access: 'auth', menu: 'overview', title: 'overview' },
  { pattern: /^\/sites\/new$/, page: siteNewPage, access: 'auth', menu: 'overview', title: 'site_new' },
  { pattern: /^\/sites\/([a-z0-9][a-z0-9-]{0,39})\/connect(?:\?.*)?$/, page: connectPage, access: 'auth', menu: 'overview', title: 'connect' },
  { pattern: /^\/sites\/([a-z0-9][a-z0-9-]{0,39})(?:\/(versions|history|backups|settings))?$/, page: sitePage, access: 'auth', menu: 'overview', title: 'site' },
  { pattern: /^\/(sites)\/([a-z0-9][a-z0-9-]{0,39})\/jobs\/(\d+)$/, page: jobPage, access: 'auth', menu: 'overview', title: 'job' },
  { pattern: /^\/(servers)\/(\d+)\/jobs\/(\d+)$/, page: jobPage, access: 'auth', menu: 'servers', title: 'job' },
  { pattern: /^\/jobs\/(\d+)$/, page: jobPage, access: 'auth', menu: 'overview', title: 'job' },
  { pattern: /^\/servers$/, page: serversPage, access: 'auth', menu: 'servers', title: 'servers' },
  { pattern: /^\/settings$/, page: settingsPage, access: 'auth', menu: 'settings', title: 'settings' },
  { pattern: /^\/activity$/, page: activityPage, access: 'auth', menu: 'activity', title: 'activity' },
]);

const MENU = [['/', 'overview', 'grid'], ['/servers', 'servers', 'server'], ['/activity', 'activity', 'file'], ['/settings', 'settings', 'settings']];

/** Shared state and navigation helpers handed to every page. */
const app = {
  info: null,
  get user() { return (app.info && app.info.user) || null; },
  role() { return app.user ? 'auth' : 'guest'; },
  homePath() { return app.user ? '/' : '/login'; },
  async loadInfo() {
    const { data } = await get('app');
    setCsrfToken(data.csrf_token);
    app.info = data;
    renderHeader();
    renderRunnerBanner();
    return data;
  },
  setUser(user) {
    if (!app.info) app.info = {};
    app.info.user = user;
    renderHeader();
  },
  go(path) { router.go(path); },
  replace(path) { router.replace(path); },
  async afterLogin(data) {
    if (data.csrf_token) setCsrfToken(data.csrf_token);
    let target = '';
    try { target = sessionStorage.getItem('rollout-redirect') || ''; sessionStorage.removeItem('rollout-redirect'); } catch (e) { /* private mode */ }
    await app.loadInfo();
    const match = target ? router.match(target) : null;
    app.go(match && match.route.access === 'auth' ? target : '/');
  },
  async logout() {
    try { await post('auth/logout'); } catch (e) { /* sign out in the interface anyway */ }
    app.setUser(null);
    app.info.runner = null;
    renderRunnerBanner();
    toast(t('common.toast.logged_out'), 'success');
    app.go('/login');
  },
};

let cleanup = null;
let renderCount = 0;

function errorBox(error) {
  return `<section class="empty-large">${icon('alert', 'icon--large')}<h1>${t('common.error.title')}</h1>
    <p>${esc(error && error.message ? error.message : String(error))}</p>
    ${error && error.details ? `<details class="tech-details"><summary>${t('common.error.details')}</summary><pre>${esc(error.details)}</pre></details>` : ''}
    <a class="btn btn--primary" href="#${app.homePath()}">${icon('arrow-left')} ${t('common.error.back')}</a></section>`;
}

function rememberRedirect(path) {
  try { sessionStorage.setItem('rollout-redirect', path); } catch (e) { /* private mode */ }
}

async function render() {
  const main = document.getElementById('content');
  if (location.hash && !location.hash.startsWith('#/') && renderCount > 0) return;   // in-page anchor
  const path = router.currentPath();
  const match = router.match(path);
  const route = match && match.route;
  if (route && route.access !== app.role()) {
    if (route.access === 'guest') { app.replace('/'); return; }
    rememberRedirect(path);
    app.replace('/login');
    return;
  }
  closeAccountMenu();
  if (typeof cleanup === 'function') { try { cleanup(); } catch (e) { /* ignore */ } }
  cleanup = null;
  const current = ++renderCount;
  markActiveMenu(route);
  main.className = 'content container';
  if (!route) {
    document.title = `${t('common.titles.not_found')} · ${t('common.app_name')}`;
    main.innerHTML = errorBox({ message: t('common.error.not_found') });
    return;
  }
  document.title = `${t('common.titles.' + route.title)} · ${t('common.app_name')}`;
  void main.offsetWidth;                 // restart the enter animation
  main.classList.add('enter');
  window.scrollTo({ top: 0 });
  try {
    const result = await route.page.render(main, match.params, app, () => current === renderCount);
    if (current === renderCount) cleanup = typeof result === 'function' ? result : null;
  } catch (error) {
    if (current !== renderCount) return;
    if (error && error.status === 401 && error.code === 'unauthenticated') return;   // already sent to the sign-in page
    main.innerHTML = errorBox(error);
  }
}

// ---------- header ----------
function markActiveMenu(route) {
  document.querySelectorAll('[data-menu]').forEach((link) => {
    const active = !!route && link.dataset.menu === route.menu;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
}

function renderHeader() {
  const user = app.user;
  const menu = document.getElementById('menu');
  menu.setAttribute('aria-label', t('common.menu.main'));
  menu.innerHTML = user ? MENU.map(([href, key, name]) => `<a href="#${href}" data-menu="${key}">${icon(name)}<span>${t('common.menu.' + key)}</span></a>`).join('') : '';
  const account = document.getElementById('account');
  if (!user) {
    account.className = 'account account--guest';
    account.innerHTML = '';
  } else {
    account.className = 'account';
    account.innerHTML = `<button type="button" class="account__button" aria-haspopup="menu" aria-expanded="false" aria-label="${t('common.account.menu', { name: user.name })}">
        ${avatar(user, 34)}<span class="account__name">${esc(user.username)}</span>${icon('chevron-down')}</button>
      <div class="account__menu" role="menu" hidden>
        <div class="account__header">${avatar(user, 46)}<div><b>${esc(user.name)}</b><small>${esc(user.username)}</small></div></div>
        <a role="menuitem" href="#/settings">${icon('settings')}${t('common.account.settings')}</a>
        <button type="button" role="menuitem" class="account__logout" data-logout>${icon('log-out')}${t('common.account.logout')}</button>
      </div>`;
  }
  const match = router.match(router.currentPath());
  markActiveMenu(match && match.route);
  renderStaticTexts();
}

function renderRunnerBanner() {
  const banner = document.getElementById('banner');
  const runner = app.info && app.info.runner;          // the runner of this server (a dashboard may only control others)
  if (!app.user || !runner || runner.ok) { if (banner.dataset.kind === 'runner') banner.hidden = true; return; }
  banner.dataset.kind = 'runner';
  banner.hidden = false;
  banner.innerHTML = `${icon('alert')}<div>${t('common.runner_down', { message: runner.message || '' })}</div>
    <button type="button" class="btn btn--small btn--outline">${icon('refresh')} ${t('common.banner.retry')}</button>`;
  banner.querySelector('button').addEventListener('click', async () => {
    try { await app.loadInfo(); render(); } catch (e) { toast(e.message, 'error'); }
  });
}

function closeAccountMenu() {
  const menu = document.querySelector('.account__menu');
  const button = document.querySelector('.account__button');
  if (menu) menu.hidden = true;
  if (button) button.setAttribute('aria-expanded', 'false');
}

function setupAccountMenu() {
  document.getElementById('account').addEventListener('click', (e) => {
    const button = e.target.closest('.account__button');
    if (button) {
      const menu = document.querySelector('.account__menu');
      menu.hidden = !menu.hidden;
      button.setAttribute('aria-expanded', String(!menu.hidden));
      if (!menu.hidden) menu.querySelector('[role=menuitem]').focus();
      return;
    }
    if (e.target.closest('[data-logout]')) { closeAccountMenu(); app.logout(); return; }
    if (e.target.closest('a[role=menuitem]')) closeAccountMenu();
  });
  document.addEventListener('click', (e) => { if (!e.target.closest('#account')) closeAccountMenu(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAccountMenu(); });
}

// ---------- language & theme ----------
let redrawTheme = () => {};

function renderStaticTexts() {
  document.querySelector('.skip-link').textContent = t('common.skip');
  document.querySelector('.logo').setAttribute('aria-label', t('common.home_label'));
  const version = app.info && app.info.app ? 'v' + app.info.app.version : '';
  document.getElementById('footer-text').textContent = t('common.footer', { version, year: new Date().getFullYear() }).replace('  ', ' ');
  const button = document.getElementById('language-toggle');
  button.innerHTML = `${icon('globe')}<span>${LANGUAGES[lang()].short}</span>`;
  button.setAttribute('aria-label', t('common.language.switch'));
  button.title = t('common.language.switch');
  redrawTheme();
}

function setupLanguageToggle() {
  document.getElementById('language-toggle').addEventListener('click', () => {
    setLang(lang() === 'id' ? 'en' : 'id');
    renderHeader();
    renderRunnerBanner();
    render();
  });
}

function serverBanner(error) {
  const banner = document.getElementById('banner');
  banner.dataset.kind = 'server';
  banner.hidden = false;
  banner.innerHTML = `${icon('alert')}<div><b>${t('common.banner.not_ready')}</b> ${esc(error.message || '')}</div>
    <button type="button" class="btn btn--small btn--outline">${icon('refresh')} ${t('common.banner.retry')}</button>`;
  banner.querySelector('button').addEventListener('click', async () => {
    try { await app.loadInfo(); banner.hidden = true; render(); } catch (e) { toast(e.message, 'error'); }
  });
}

/** One-time notice after the administrator setup page redirected here (?installed=1). */
function installNotice() {
  const params = new URLSearchParams(location.search);
  if (!params.has('installed')) return;
  history.replaceState(null, '', location.pathname + location.hash);
  toast(t('common.toast.installed'), 'success', 9000);
}

/**
 * The agent page of a hosting links to ?connect=<site>&agent=<address> (a query survives a sign-in in front of the
 * dashboard, e.g. Cloudflare Access, where a #fragment would be lost): open its confirmation page.
 */
function connectLink() {
  const params = new URLSearchParams(location.search);
  const site = params.get('connect') || '';
  if (!/^[a-z0-9][a-z0-9-]{0,39}$/.test(site) || !params.get('agent')) return;
  history.replaceState(null, '', location.pathname + `#/sites/${site}/connect?agent=${encodeURIComponent(params.get('agent'))}`);
}

async function start() {
  connectLink();
  redrawTheme = setupThemeToggle(document.getElementById('theme-toggle'));
  setupLanguageToggle();
  renderStaticTexts();
  setupAccountMenu();
  document.querySelector('.skip-link').addEventListener('click', (e) => {
    e.preventDefault();
    document.getElementById('content').focus();
  });
  onSessionExpired(() => {
    if (!app.user) return;
    app.setUser(null);
    toast(t('common.toast.session_expired'), 'warning', 6000);
    rememberRedirect(router.currentPath());
    app.replace('/login');
  });
  try {
    await app.loadInfo();
  } catch (error) {
    app.info = null;
    renderHeader();
    serverBanner(error);
  }
  installNotice();
  window.addEventListener('hashchange', render);
  render();
}

start();

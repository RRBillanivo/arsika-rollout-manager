// Light/dark theme toggle (the saved theme is applied early by theme-init.js).
import { t } from './i18n.js';
import { icon } from './ui.js';

export function currentTheme() {
  const theme = document.documentElement.getAttribute('data-theme');
  if (theme === 'light' || theme === 'dark') return theme;
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

/** Returns the redraw function (labels follow the interface language). */
export function setupThemeToggle(button) {
  const draw = () => {
    const dark = currentTheme() === 'dark';
    button.innerHTML = icon(dark ? 'sun' : 'moon');
    button.setAttribute('aria-label', t(dark ? 'common.theme.to_light' : 'common.theme.to_dark'));
    button.title = button.getAttribute('aria-label');
  };
  button.addEventListener('click', () => {
    const next = currentTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('rollout-theme', next); } catch (e) { /* private mode */ }
    draw();
  });
  draw();
  return draw;
}

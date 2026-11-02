// Applies the saved light/dark theme before the first paint (no flash). The choice is stored in this browser only.
(function () {
  try {
    var theme = localStorage.getItem('rollout-theme');
    if (theme === 'light' || theme === 'dark') document.documentElement.setAttribute('data-theme', theme);
  } catch (e) { /* private mode: follow the system theme */ }
})();

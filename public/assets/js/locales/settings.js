// Settings page: runner, account password, versions. GitHub tokens are set per site (sites.form.github_token).
export default {
  id: {
    title: 'Pengaturan',
    runner: {
      title: 'Runner', version: 'Versi', python: 'Python', uptime: 'Hidup', disk: 'Disk backup', roots: 'Folder situs yang diizinkan',
      ok: 'Runner berjalan di server.', down: 'Runner tidak menjawab.',
      tokens: 'Token GitHub: {n} dari {sites} situs punya token sendiri (diatur di pengaturan tiap situs).',
    },
    uptime: { days: '{d} hari {h} jam', hours: '{h} jam {m} menit', minutes: '{m} menit' },
    account: {
      title: 'Kata sandi admin', current: 'Kata sandi sekarang', new: 'Kata sandi baru', confirm: 'Ulangi kata sandi baru',
      rules: 'Minimal {min} karakter, berisi huruf dan angka.', save: 'Ganti kata sandi',
    },
    about: { title: 'Tentang', dashboard: 'Dasbor', php: 'PHP' },
  },
  en: {
    title: 'Settings',
    runner: {
      title: 'Runner', version: 'Version', python: 'Python', uptime: 'Uptime', disk: 'Backup disk', roots: 'Allowed site folders',
      ok: 'The runner is running on the server.', down: 'The runner does not answer.',
      tokens: 'GitHub tokens: {n} of {sites} sites have their own token (set in the settings of each site).',
    },
    uptime: { days: '{d} days {h} h', hours: '{h} h {m} min', minutes: '{m} min' },
    account: {
      title: 'Administrator password', current: 'Current password', new: 'New password', confirm: 'Repeat the new password',
      rules: 'At least {min} characters with letters and numbers.', save: 'Change password',
    },
    about: { title: 'About', dashboard: 'Dashboard', php: 'PHP' },
  },
};

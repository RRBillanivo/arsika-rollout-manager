// Settings page: runner, GitHub token, account password.
export default {
  id: {
    title: 'Pengaturan',
    runner: {
      title: 'Runner', version: 'Versi', python: 'Python', uptime: 'Hidup', disk: 'Disk backup', roots: 'Folder situs yang diizinkan',
      ok: 'Runner berjalan di server.', down: 'Runner tidak menjawab.',
    },
    uptime: { days: '{d} hari {h} jam', hours: '{h} jam {m} menit', minutes: '{m} menit' },
    github: {
      title: 'Token GitHub',
      lead: 'Dibutuhkan untuk repositori private. Buat "fine-grained token" di GitHub dengan akses hanya-baca ke Contents untuk repositori Anda.',
      set: 'Token tersimpan ({hint}).', not_set: 'Belum ada token: hanya repositori publik yang bisa dibaca.',
      token: 'Token baru', token_help: 'Disimpan di server (root saja) dan tidak pernah ditampilkan lagi.',
      save: 'Simpan token', remove: 'Hapus token', saved: 'Token disimpan.', removed: 'Token dihapus.',
      how: 'Cara membuat token',
      steps: [
        'GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.',
        'Repository access: Only select repositories → pilih repositori web Anda (mis. Anemia_Web).',
        'Permissions → Repository permissions → Contents: Read-only. Lainnya biarkan No access.',
        'Generate token, salin, lalu tempel di sini.',
      ],
    },
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
    },
    uptime: { days: '{d} days {h} h', hours: '{h} h {m} min', minutes: '{m} min' },
    github: {
      title: 'GitHub token',
      lead: 'Needed for private repositories. Create a "fine-grained token" on GitHub with read-only access to Contents of your repositories.',
      set: 'Token saved ({hint}).', not_set: 'No token: only public repositories can be read.',
      token: 'New token', token_help: 'Stored on the server (root only) and never shown again.',
      save: 'Save token', remove: 'Remove token', saved: 'Token saved.', removed: 'Token removed.',
      how: 'How to create a token',
      steps: [
        'GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.',
        'Repository access: Only select repositories → choose your web repositories (e.g. Anemia_Web).',
        'Permissions → Repository permissions → Contents: Read-only. Leave the rest at No access.',
        'Generate the token, copy it and paste it here.',
      ],
    },
    account: {
      title: 'Administrator password', current: 'Current password', new: 'New password', confirm: 'Repeat the new password',
      rules: 'At least {min} characters with letters and numbers.', save: 'Change password',
    },
    about: { title: 'About', dashboard: 'Dashboard', php: 'PHP' },
  },
};

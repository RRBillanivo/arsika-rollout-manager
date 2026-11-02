// Activity log: sign-ins and administrator actions.
export default {
  id: {
    title: 'Log aktivitas',
    summary: '24 jam terakhir: <b>{ok}</b> login berhasil, <b>{failed}</b> login gagal.',
    tab_actions: 'Tindakan', tab_logins: 'Login',
    columns: { time: 'Waktu', account: 'Akun', result: 'Hasil', device: 'Perangkat', ip: 'IP', admin: 'Admin', action: 'Tindakan', details: 'Rincian' },
    unknown: 'akun tidak dikenal', system: 'sistem', success: 'Berhasil', failed: 'Gagal',
    no_logins: 'Belum ada login.', no_actions: 'Belum ada tindakan.',
    actions: {
      admin_created: 'Akun admin dibuat (halaman pemasangan)', admin_reset: 'Sandi admin diatur ulang (halaman pemasangan)',
      password_changed: 'Ganti kata sandi', site_created: 'Tambah situs', site_updated: 'Ubah pengaturan situs', site_deleted: 'Hapus situs dari dasbor',
      job_deploy: 'Deploy', job_rollback: 'Rollback', job_adopt: 'Adopsi instalasi lama', job_backup: 'Backup', job_restore: 'Pulihkan backup',
      job_delete_release: 'Hapus versi dari server', job_cleanup_adopt: 'Hapus _pre_rollout', backup_deleted: 'Hapus backup',
      server_added: 'Tambah server', server_updated: 'Ubah server', server_removed: 'Hapus server dari dasbor',
      github_token_changed: 'Ubah token GitHub',
    },
  },
  en: {
    title: 'Activity log',
    summary: 'Last 24 hours: <b>{ok}</b> successful and <b>{failed}</b> failed sign-ins.',
    tab_actions: 'Actions', tab_logins: 'Sign-ins',
    columns: { time: 'Time', account: 'Account', result: 'Result', device: 'Device', ip: 'IP', admin: 'Admin', action: 'Action', details: 'Details' },
    unknown: 'unknown account', system: 'system', success: 'Successful', failed: 'Failed',
    no_logins: 'No sign-ins yet.', no_actions: 'No actions yet.',
    actions: {
      admin_created: 'Administrator account created (setup page)', admin_reset: 'Administrator password reset (setup page)',
      password_changed: 'Password changed', site_created: 'Site added', site_updated: 'Site settings changed', site_deleted: 'Site removed from the dashboard',
      job_deploy: 'Deploy', job_rollback: 'Rollback', job_adopt: 'Adoption of the existing installation', job_backup: 'Backup', job_restore: 'Backup restored',
      job_delete_release: 'Version deleted from the server', job_cleanup_adopt: '_pre_rollout deleted', backup_deleted: 'Backup deleted',
      server_added: 'Server added', server_updated: 'Server changed', server_removed: 'Server removed from the dashboard',
      github_token_changed: 'GitHub token changed',
    },
  },
};

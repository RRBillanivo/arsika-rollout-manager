// Jobs: types, statuses and the live log page.
export default {
  id: {
    types: {
      deploy: 'Deploy', rollback: 'Rollback', adopt: 'Adopsi', backup: 'Backup', restore: 'Pulihkan backup',
      'delete-release': 'Hapus versi', 'cleanup-adopt': 'Hapus _pre_rollout', 'self-update': 'Update runner',
      upload: 'Unggah versi', artisan: 'Artisan',
    },
    status: { queued: 'Menunggu', running: 'Berjalan', succeeded: 'Berhasil', failed: 'Gagal' },
    page: {
      title: 'Job #{id}', back: 'Kembali ke {site}', back_servers: 'Kembali ke Server', server: 'Server', runner: 'runner', site: 'Situs', version: 'Versi', change: 'Perubahan', actor: 'Oleh',
      started: 'Mulai', finished: 'Selesai', duration: 'Durasi', live: 'Langsung', log: 'Log', log_empty: 'Menunggu giliran…',
      follow: 'Ikuti log', copy: 'Salin log', copied: 'Log disalin.',
      done_ok: 'Job selesai: {message}', done_failed: 'Job gagal: {message}',
    },
    in_progress: 'Ada job yang sedang berjalan untuk situs ini.',
  },
  en: {
    types: {
      deploy: 'Deploy', rollback: 'Rollback', adopt: 'Adoption', backup: 'Backup', restore: 'Restore backup',
      'delete-release': 'Delete version', 'cleanup-adopt': 'Delete _pre_rollout', 'self-update': 'Update runner',
      upload: 'Upload version', artisan: 'Artisan',
    },
    status: { queued: 'Queued', running: 'Running', succeeded: 'Succeeded', failed: 'Failed' },
    page: {
      title: 'Job #{id}', back: 'Back to {site}', back_servers: 'Back to Servers', server: 'Server', runner: 'runner', site: 'Site', version: 'Version', change: 'Change', actor: 'By',
      started: 'Started', finished: 'Finished', duration: 'Duration', live: 'Live', log: 'Log', log_empty: 'Waiting for its turn…',
      follow: 'Follow the log', copy: 'Copy log', copied: 'Log copied.',
      done_ok: 'Job finished: {message}', done_failed: 'Job failed: {message}',
    },
    in_progress: 'A job is running for this site.',
  },
};

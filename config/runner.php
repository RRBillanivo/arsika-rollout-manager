<?php

return [
    // The runner service (runner/arsika_rollout_manager, systemd unit arsika-rollout-manager-runner) on this server.
    // It does the actual work as root: downloading releases, switching versions, restarting services, backups.
    'url' => rtrim((string)env('RUNNER_URL', 'http://127.0.0.1:8790'), '/'),

    // Shared HMAC key; must equal "secret" in /etc/arsika-rollout-manager/runner.json (written by deploy/install.sh).
    'secret' => (string)App\Core\Env::raw('RUNNER_SECRET', ''),

    // Seconds to wait for an answer (the overview checks every site, which can take a few seconds).
    'timeout' => 30,
];

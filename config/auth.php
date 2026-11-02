<?php

return [
    'session_cookie' => 'rollout_session',

    // A sign-in ends after this many hours of inactivity ("remember me" is deliberately not offered: this dashboard
    // can deploy code as root on the server).
    'session_lifetime_hours' => 8,
    'remember_days' => 1,

    // Sign-in lockout: per account and per IP address.
    'lockout' => [
        'max_attempts' => 5,
        'max_attempts_per_ip' => 20,
        'decay_minutes' => 15,
    ],

    // Minimum password length (longer than usual for the same reason).
    'password_min_length' => 12,

    // Validity of the one-time administrator setup link (php bin/console install:token).
    'install_token_hours' => 24,
];

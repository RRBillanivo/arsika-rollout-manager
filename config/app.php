<?php

return [
    // Name shown in the browser title and on the setup page.
    'name' => 'Arsika Rollout Manager',

    'version' => App\Core\Application::VERSION,

    // production | local
    'env' => env('APP_ENV', 'production'),

    // true shows technical error details to every visitor; keep false in production.
    'debug' => (bool)env('APP_DEBUG', false),

    // Public address without a trailing slash, e.g. https://rollout.example.com (used for the setup link).
    'url' => rtrim((string)env('APP_URL', ''), '/'),

    // 32-byte master key ("base64:..."), created by `php bin/console key:generate`. Never change or lose it:
    // every password depends on it.
    'key' => (string)env('APP_KEY', ''),

    'timezone' => (string)env('APP_TIMEZONE', 'Asia/Jakarta'),
];

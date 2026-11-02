<?php

return [
    // mysql (production: MySQL / MariaDB) or sqlite (local development and tests).
    'default' => (string)env('DB_CONNECTION', 'sqlite'),

    'connections' => [
        'mysql' => [
            'host' => (string)env('DB_HOST', '127.0.0.1'),
            'port' => (int)env('DB_PORT', 3306),
            'database' => (string)App\Core\Env::raw('DB_DATABASE', ''),
            'username' => (string)App\Core\Env::raw('DB_USERNAME', ''),
            'password' => (string)App\Core\Env::raw('DB_PASSWORD', ''),
            'charset' => 'utf8mb4',
        ],
        'sqlite' => [
            'database' => (string)env('DB_SQLITE_PATH', storage_path('database/rollout.sqlite')),
        ],
    ],
];

-- Schema for MySQL 5.7+ / MariaDB 10.3+ (created automatically, see App\Core\Database).
-- The dashboard stores its administrators, audit data and the other servers it controls; sites, releases, jobs and
-- backups live in the runner of each server.

CREATE TABLE IF NOT EXISTS app_meta (
    meta_key VARCHAR(64) NOT NULL PRIMARY KEY,
    meta_value TEXT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS users (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(64) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'active',
    name VARCHAR(100) NOT NULL,
    session_version INT UNSIGNED NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    last_login_at DATETIME NULL,
    last_seen_at DATETIME NULL,
    login_count INT UNSIGNED NOT NULL DEFAULT 0,
    UNIQUE KEY users_username_unique (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS login_attempts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    attempted_at DATETIME NOT NULL,
    user_id INT UNSIGNED NULL,
    identity VARCHAR(191) NOT NULL,
    ip VARCHAR(45) NOT NULL,
    device VARCHAR(80) NULL,
    success TINYINT NOT NULL DEFAULT 0,
    KEY login_attempts_identity_index (identity, attempted_at),
    KEY login_attempts_ip_index (ip, attempted_at),
    KEY login_attempts_time_index (attempted_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rate_limits (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    rate_key VARCHAR(191) NOT NULL,
    hit_at DATETIME NOT NULL,
    KEY rate_limits_key_index (rate_key, hit_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    actor_id INT UNSIGNED NULL,
    action VARCHAR(40) NOT NULL,
    target VARCHAR(80) NULL,
    details VARCHAR(255) NULL,
    ip VARCHAR(45) NULL,
    KEY audit_logs_time_index (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Runners on other servers (schema version 2). The secret is encrypted with APP_KEY; tls_pin is the public key pin of
-- the runner's certificate (empty: the certificate is checked against the system's certificate authorities).
CREATE TABLE IF NOT EXISTS servers (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(80) NOT NULL,
    url VARCHAR(255) NOT NULL,
    secret TEXT NOT NULL,
    tls_pin VARCHAR(255) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY servers_url_unique (url)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- The server of each site that is not on this server (a site without a row belongs to the runner of this server).
CREATE TABLE IF NOT EXISTS site_servers (
    site_id VARCHAR(40) NOT NULL PRIMARY KEY,
    server_id INT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL,
    KEY site_servers_server_index (server_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

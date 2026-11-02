-- Schema for SQLite 3 (local development and tests; created automatically, see App\Core\Database).

CREATE TABLE IF NOT EXISTS app_meta (
    meta_key TEXT NOT NULL PRIMARY KEY,
    meta_value TEXT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    name TEXT NOT NULL,
    session_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT NULL,
    last_seen_at TEXT NULL,
    login_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempted_at TEXT NOT NULL,
    user_id INTEGER NULL,
    identity TEXT NOT NULL,
    ip TEXT NOT NULL,
    device TEXT NULL,
    success INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS login_attempts_identity_index ON login_attempts (identity, attempted_at);
CREATE INDEX IF NOT EXISTS login_attempts_ip_index ON login_attempts (ip, attempted_at);

CREATE TABLE IF NOT EXISTS rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rate_key TEXT NOT NULL,
    hit_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_limits_key_index ON rate_limits (rate_key, hit_at);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor_id INTEGER NULL,
    action TEXT NOT NULL,
    target TEXT NULL,
    details TEXT NULL,
    ip TEXT NULL
);
CREATE INDEX IF NOT EXISTS audit_logs_time_index ON audit_logs (created_at);

CREATE TABLE IF NOT EXISTS servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    secret TEXT NOT NULL,
    tls_pin TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_servers (
    site_id TEXT NOT NULL PRIMARY KEY,
    server_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS site_servers_server_index ON site_servers (server_id);

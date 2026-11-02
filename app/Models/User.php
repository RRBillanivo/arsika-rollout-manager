<?php

namespace App\Models;

use App\Core\Database;
use App\Services\Crypto;

/**
 * Administrator accounts of the dashboard (users table). Every account is an administrator; the built-in one is
 * "admin", created on the one-time setup page. Status: active, blocked. Passwords are Argon2id hashes.
 */
final class User
{
    const STATUS_ACTIVE = 'active';
    const STATUS_BLOCKED = 'blocked';
    const TOUCH_SECONDS = 30;
    const COLUMNS = 'id, username, password_hash, status, name, session_version, created_at, updated_at, last_login_at, last_seen_at, login_count';

    /** @var Database */
    private $db;
    /** @var Crypto */
    private $crypto;

    public function __construct(Database $db, Crypto $crypto)
    {
        $this->db = $db;
        $this->crypto = $crypto;
    }

    public function find(int $id): ?array
    {
        return $this->db->first('SELECT ' . self::COLUMNS . ' FROM users WHERE id = ?', [$id]);
    }

    public function findByIdentity(string $identity): ?array
    {
        $identity = strtolower(trim($identity));
        return $identity === '' ? null : $this->db->first('SELECT ' . self::COLUMNS . ' FROM users WHERE username = ?', [$identity]);
    }

    /** $data: username, password (plain text), name. -> new id */
    public function create(array $data): int
    {
        $now = $this->db->now();
        return $this->db->insert('users', [
            'username' => strtolower($data['username']),
            'password_hash' => $this->crypto->hashPassword($data['password']),
            'status' => self::STATUS_ACTIVE,
            'name' => $data['name'] ?? $data['username'],
            'session_version' => 1,
            'created_at' => $now,
            'updated_at' => $now,
        ]);
    }

    public function update(int $id, array $data): void
    {
        $set = array_intersect_key($data, array_flip(['name', 'status']));
        if (isset($set['status'])) {
            $this->db->statement('UPDATE users SET session_version = session_version + 1 WHERE id = ?', [$id]);
        }
        if ($set) {
            $set['updated_at'] = $this->db->now();
            $this->db->update('users', $id, $set);
        }
    }

    /** New password; sessions on other devices end (session_version is incremented). */
    public function changePassword(int $id, string $password): void
    {
        $this->db->statement('UPDATE users SET password_hash = ?, session_version = session_version + 1, updated_at = ? WHERE id = ?',
            [$this->crypto->hashPassword($password), $this->db->now(), $id]);
    }

    public function recordLogin(array $user, string $password): void
    {
        $set = ['last_login_at' => $this->db->now(), 'last_seen_at' => $this->db->now()];
        if ($this->crypto->needsRehash($user['password_hash'])) {
            $set['password_hash'] = $this->crypto->hashPassword($password);
        }
        $this->db->update('users', (int)$user['id'], $set);
        $this->db->statement('UPDATE users SET login_count = login_count + 1 WHERE id = ?', [(int)$user['id']]);
    }

    /** last_seen_at at most every TOUCH_SECONDS. */
    public function touch(array $user): void
    {
        if ($user['last_seen_at'] === null || strtotime((string)$user['last_seen_at']) < time() - self::TOUCH_SECONDS) {
            $this->db->statement('UPDATE users SET last_seen_at = ? WHERE id = ?', [$this->db->now(), (int)$user['id']]);
        }
    }

    public function verifyPassword(array $user, string $password): bool
    {
        return $this->crypto->verifyPassword($password, (string)$user['password_hash']);
    }

    public function count(): int
    {
        return (int)$this->db->value('SELECT COUNT(*) FROM users');
    }

    /** Account data for the client. The password hash is never included. */
    public function toArray(array $user): array
    {
        return [
            'id' => (int)$user['id'], 'username' => $user['username'], 'name' => $user['name'], 'status' => $user['status'],
            'created_at' => $user['created_at'], 'last_login_at' => $user['last_login_at'], 'login_count' => (int)$user['login_count'],
        ];
    }
}

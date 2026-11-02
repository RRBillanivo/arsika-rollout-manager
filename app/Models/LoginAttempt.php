<?php

namespace App\Models;

use App\Core\Database;
use App\Core\Request;
use App\Support\Str;

/** Sign-in attempts (login_attempts table): audit trail and brute-force lockout. */
final class LoginAttempt
{
    /** @var Database */
    private $db;

    public function __construct(Database $db)
    {
        $this->db = $db;
    }

    public function record(?int $userId, string $identity, bool $success, Request $request): void
    {
        $this->db->insert('login_attempts', [
            'attempted_at' => $this->db->now(), 'user_id' => $userId, 'identity' => substr(strtolower($identity), 0, 191),
            'ip' => $request->ip(), 'device' => Str::limit($request->device(), 80), 'success' => $success ? 1 : 0,
        ]);
    }

    /** Failed attempts within the last $seconds: [for this identity, for this IP]. */
    public function recentFailures(string $identity, string $ip, int $seconds): array
    {
        $since = $this->db->now(time() - $seconds);
        return [
            (int)$this->db->value('SELECT COUNT(*) FROM login_attempts WHERE identity = ? AND success = 0 AND attempted_at >= ?',
                [substr(strtolower($identity), 0, 191), $since]),
            (int)$this->db->value('SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND success = 0 AND attempted_at >= ?', [$ip, $since]),
        ];
    }

    public function ofUser(int $userId, int $limit): array
    {
        return array_map(static function ($row) {
            return ['attempted_at' => $row['attempted_at'], 'ip' => $row['ip'], 'device' => $row['device'], 'success' => (bool)$row['success']];
        }, $this->db->select('SELECT attempted_at, ip, device, success FROM login_attempts WHERE user_id = ? ORDER BY id DESC LIMIT '
            . $limit, [$userId]));
    }

    public function latest(int $limit): array
    {
        return array_map(static function ($row) {
            return ['attempted_at' => $row['attempted_at'], 'identity' => $row['identity'], 'ip' => $row['ip'], 'device' => $row['device'],
                    'success' => (bool)$row['success'], 'user_name' => $row['name']];
        }, $this->db->select('SELECT l.attempted_at, l.identity, l.ip, l.device, l.success, u.name FROM login_attempts l
            LEFT JOIN users u ON u.id = l.user_id ORDER BY l.id DESC LIMIT ' . $limit));
    }

    /** Successful and failed sign-ins in the last 24 hours. */
    public function lastDaySummary(): array
    {
        $since = $this->db->now(time() - 86400);
        return [
            'failed_24h' => (int)$this->db->value('SELECT COUNT(*) FROM login_attempts WHERE success = 0 AND attempted_at >= ?', [$since]),
            'succeeded_24h' => (int)$this->db->value('SELECT COUNT(*) FROM login_attempts WHERE success = 1 AND attempted_at >= ?', [$since]),
        ];
    }
}

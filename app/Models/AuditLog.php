<?php

namespace App\Models;

use App\Core\Database;
use App\Support\Str;
use Throwable;

/** Administrative actions (audit_logs table). Writing the log never makes the action itself fail. */
final class AuditLog
{
    /** @var Database */
    private $db;

    public function __construct(Database $db)
    {
        $this->db = $db;
    }

    public function record(?int $actorId, string $action, string $target = '', string $details = '', string $ip = ''): void
    {
        try {
            $this->db->insert('audit_logs', [
                'created_at' => $this->db->now(), 'actor_id' => $actorId, 'action' => substr($action, 0, 40),
                'target' => $target === '' ? null : substr($target, 0, 80), 'details' => $details === '' ? null : Str::limit($details, 255),
                'ip' => $ip === '' ? null : $ip,
            ]);
        } catch (Throwable $e) {
            // the audit trail must not break the action
        }
    }

    public function latest(int $limit): array
    {
        return array_map(static function ($row) {
            return ['created_at' => $row['created_at'], 'action' => $row['action'], 'target' => $row['target'],
                    'details' => $row['details'], 'ip' => $row['ip'], 'actor_name' => $row['name']];
        }, $this->db->select('SELECT l.created_at, l.action, l.target, l.details, l.ip, u.name FROM audit_logs l
            LEFT JOIN users u ON u.id = l.actor_id ORDER BY l.id DESC LIMIT ' . $limit));
    }
}

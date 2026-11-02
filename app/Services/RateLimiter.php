<?php

namespace App\Services;

use App\Core\Database;
use App\Core\HttpException;

/** Sliding-window rate limits stored in the rate_limits table. */
final class RateLimiter
{
    /** @var Database */
    private $db;

    public function __construct(Database $db)
    {
        $this->db = $db;
    }

    /**
     * Count one hit for $key; throws 429 with $message when $key already had $max hits in the last $seconds.
     * @throws HttpException
     */
    public function hit(string $key, int $max, int $seconds, string $message): void
    {
        if ($this->attempts($key, $seconds) >= $max) {
            throw HttpException::tooManyRequests($message);
        }
        $this->db->statement('INSERT INTO rate_limits (rate_key, hit_at) VALUES (?, ?)', [substr($key, 0, 191), $this->db->now()]);
    }

    public function attempts(string $key, int $seconds): int
    {
        return (int)$this->db->value('SELECT COUNT(*) FROM rate_limits WHERE rate_key = ? AND hit_at >= ?',
            [substr($key, 0, 191), $this->db->now(time() - $seconds)]);
    }
}

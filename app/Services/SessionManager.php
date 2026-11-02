<?php

namespace App\Services;

use App\Core\Application;
use App\Core\Request;
use App\Models\User;

/**
 * PHP session: CSRF token and the signed-in account.
 *   - Session files live in storage/framework/sessions (isolated from other sites, own lifetime).
 *   - A normal login expires after auth.session_lifetime_hours of inactivity; "remember me" lasts auth.remember_days.
 *   - The account is re-read on every request: blocking, deleting, a role change or a password change (session_version
 *     is incremented) immediately invalidates existing sessions.
 */
final class SessionManager
{
    /** @var Application */
    private $app;
    /** @var User */
    private $users;
    /** @var array|null|false false = not loaded yet, null = guest */
    private $user = false;

    public function __construct(Application $app, User $users)
    {
        $this->app = $app;
        $this->users = $users;
    }

    private function secure(): bool
    {
        return $this->app->make(Request::class)->isSecure();
    }

    private function cookieOptions(int $expires): array
    {
        return ['expires' => $expires, 'path' => '/', 'secure' => $this->secure(), 'httponly' => true, 'samesite' => 'Strict'];
    }

    public function start(): void
    {
        if (session_status() === PHP_SESSION_ACTIVE) {
            return;
        }
        $path = storage_path('framework/sessions');
        if ((is_dir($path) || @mkdir($path, 0770, true)) && is_writable($path)) {
            session_save_path($path);
        }
        @ini_set('session.gc_maxlifetime', (string)(max(1, (int)config('auth.remember_days', 30)) * 86400));
        @ini_set('session.gc_probability', '1');
        @ini_set('session.gc_divisor', '200');
        @ini_set('session.use_strict_mode', '1');
        @ini_set('session.use_only_cookies', '1');
        session_name((string)config('auth.session_cookie', 'rollout_session'));
        $options = $this->cookieOptions(0);
        unset($options['expires']);
        session_set_cookie_params(['lifetime' => 0] + $options);
        session_start();
        if (empty($_SESSION['csrf_token'])) {
            $_SESSION['csrf_token'] = bin2hex(random_bytes(16));
        }
    }

    /** Write the session and release its lock, so long requests do not block the user's other requests. */
    public function release(): void
    {
        if (session_status() === PHP_SESSION_ACTIVE) {
            session_write_close();
        }
    }

    public function token(): string
    {
        $this->start();
        return (string)$_SESSION['csrf_token'];
    }

    public function verifyCsrf(string $token): bool
    {
        $this->start();
        return $token !== '' && hash_equals((string)$_SESSION['csrf_token'], $token);
    }

    /** The signed-in account (users row), or null for guests. */
    public function user(): ?array
    {
        if ($this->user !== false) {
            return $this->user;
        }
        $this->start();
        $this->user = null;
        $id = (int)($_SESSION['user_id'] ?? 0);
        if ($id <= 0) {
            return null;
        }
        $limit = !empty($_SESSION['remember'])
            ? (int)config('auth.remember_days', 30) * 86400
            : (int)config('auth.session_lifetime_hours', 12) * 3600;
        if (time() - (int)($_SESSION['last_activity'] ?? 0) > $limit) {
            $this->forget();
            return null;
        }
        $user = $this->users->find($id);
        if ($user === null || $user['status'] !== User::STATUS_ACTIVE
            || (int)$user['session_version'] !== (int)($_SESSION['session_version'] ?? -1)) {
            $this->forget();
            return null;
        }
        $_SESSION['last_activity'] = time();
        $this->users->touch($user);
        return $this->user = $user;
    }

    /** Bind the session to $user after a successful login; the session id and CSRF token are rotated (no fixation). */
    public function login(array $user, bool $remember): void
    {
        $this->start();
        session_regenerate_id(true);
        $_SESSION['user_id'] = (int)$user['id'];
        $_SESSION['session_version'] = (int)$user['session_version'];
        $_SESSION['remember'] = $remember;
        $_SESSION['last_activity'] = time();
        $_SESSION['csrf_token'] = bin2hex(random_bytes(16));
        if ($remember) {
            setcookie(session_name(), session_id(), $this->cookieOptions(time() + (int)config('auth.remember_days', 30) * 86400));
        }
        $this->user = $user;
    }

    /** Keep this device signed in after the account's own session_version changed (e.g. password change). */
    public function refresh(array $user): void
    {
        $this->start();
        $_SESSION['session_version'] = (int)$user['session_version'];
        $this->user = $user;
    }

    public function logout(): void
    {
        $this->start();
        $this->forget();
        $_SESSION['csrf_token'] = bin2hex(random_bytes(16));
        setcookie(session_name(), session_id(), $this->cookieOptions(0));   // replaces a "remember me" cookie
    }

    /** Session value for small per-visitor flags (e.g. the daily visitor counter). */
    public function get(string $key)
    {
        $this->start();
        return $_SESSION[$key] ?? null;
    }

    public function put(string $key, $value): void
    {
        $this->start();
        $_SESSION[$key] = $value;
    }

    private function forget(): void
    {
        unset($_SESSION['user_id'], $_SESSION['session_version'], $_SESSION['remember'], $_SESSION['last_activity']);
        session_regenerate_id(true);
        $this->user = null;
    }
}

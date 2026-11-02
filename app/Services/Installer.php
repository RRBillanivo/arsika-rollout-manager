<?php

namespace App\Services;

use App\Core\Application;
use App\Core\Database;
use App\Models\AuditLog;
use App\Models\User;
use RuntimeException;

/**
 * One-time administrator setup.
 *
 * `php bin/console install:token` stores the SHA-256 of a random token in app_meta (valid for auth.install_token_hours)
 * and creates public/install/index.php. The administrator opens <APP_URL>/install/?token=..., chooses the password,
 * and the page then revokes the token and deletes itself. The same command resets a forgotten admin password.
 */
final class Installer
{
    const META_KEY = 'install_token';
    const ADMIN_USERNAME = 'admin';
    const STUB = <<<'PHP'
<?php

/*
 * One-time administrator setup page, created by `php bin/console install:token`.
 * It deletes itself (and this folder) as soon as the administrator account is set up.
 */

$app = require dirname(__DIR__, 2) . '/bootstrap/app.php';
$app->make(App\Install\InstallPage::class)->handle(App\Core\Request::capture())->send();

PHP;

    /** @var Application */
    private $app;
    /** @var Database */
    private $db;
    /** @var User */
    private $users;
    /** @var AuditLog */
    private $audit;

    public function __construct(Application $app, Database $db, User $users, AuditLog $audit)
    {
        $this->app = $app;
        $this->db = $db;
        $this->users = $users;
        $this->audit = $audit;
    }

    public function stubPath(): string
    {
        return $this->app->basePath('public/install/index.php');
    }

    /** Create a new token (replacing any previous one) and the setup page. -> the token (shown once) */
    public function issueToken(): string
    {
        $token = bin2hex(random_bytes(24));
        $hours = max(1, (int)config('auth.install_token_hours', 24));
        $this->db->setMeta(self::META_KEY, (string)json_encode(['hash' => hash('sha256', $token),
            'expires_at' => time() + $hours * 3600, 'created_at' => $this->db->now()]));
        $this->writeStub();
        return $token;
    }

    public function isValidToken(string $token): bool
    {
        $stored = json_decode((string)$this->db->meta(self::META_KEY), true);
        return $token !== '' && is_array($stored) && (int)($stored['expires_at'] ?? 0) > time()
            && hash_equals((string)($stored['hash'] ?? ''), hash('sha256', $token));
    }

    public function revokeToken(): void
    {
        $this->db->deleteMeta(self::META_KEY);
    }

    public function hasAdmin(): bool
    {
        return $this->users->count() > 0;
    }

    /**
     * Create the administrator (username "admin"), or reset its password when it already exists: the account becomes active
     * again and its other sessions end. -> users row
     */
    public function setUpAdmin(string $password, string $ip): array
    {
        $user = $this->users->findByIdentity(self::ADMIN_USERNAME);
        if ($user === null) {
            $id = $this->users->create(['username' => self::ADMIN_USERNAME, 'password' => $password, 'name' => 'Administrator']);
            $this->audit->record(null, 'admin_created', 'user:' . $id, trans('install.audit_created'), $ip);
        } else {
            $id = (int)$user['id'];
            $this->users->update($id, ['status' => User::STATUS_ACTIVE]);
            $this->users->changePassword($id, $password);
            $this->audit->record(null, 'admin_reset', 'user:' . $id, trans('install.audit_reset'), $ip);
        }
        return $this->users->find($id);
    }

    private function writeStub(): void
    {
        $file = $this->stubPath();
        $dir = dirname($file);
        if ((!is_dir($dir) && !@mkdir($dir, 0775, true)) || @file_put_contents($file, self::STUB, LOCK_EX) === false) {
            throw new RuntimeException('Cannot write ' . $file . ' (check the permissions of public/).');
        }
        // created by root (setup script): hand the folder to the web server user so the page can delete itself
        if (function_exists('posix_geteuid') && posix_geteuid() === 0) {
            $owner = @fileowner(dirname($dir));
            $group = @filegroup(dirname($dir));
            if ($owner !== false && $owner !== 0) {
                @chown($dir, $owner);
                @chown($file, $owner);
                @chgrp($dir, $group);
                @chgrp($file, $group);
            }
        }
    }

    /** Delete the setup page and its folder. -> false when the files could not be removed. */
    public function removeStub(): bool
    {
        $file = $this->stubPath();
        if (is_file($file)) {
            @unlink($file);
        }
        if (function_exists('opcache_invalidate')) {
            @opcache_invalidate($file, true);
        }
        @chdir(dirname(dirname($file)));                    // PHP-CGI runs inside the folder; Windows cannot remove a working directory
        @rmdir(dirname($file));
        return !is_file($file);
    }
}

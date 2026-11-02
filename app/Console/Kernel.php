<?php

namespace App\Console;

use App\Core\Application;
use App\Core\Database;
use App\Core\DatabaseException;
use App\Core\Env;
use App\Core\HttpException;
use App\Services\Crypto;
use App\Services\Installer;
use App\Services\RunnerClient;
use App\Support\Lang;
use Throwable;

/** Command-line interface: php bin/console <command> [--option[=value]] */
final class Kernel
{
    const COMMANDS = [
        'about' => ['about', 'Show the version, environment, database and runner settings'],
        'key:generate' => ['generateKey', 'Create APP_KEY in .env (an existing key is kept; --force replaces it)'],
        'env:set' => ['setEnv', 'Write KEY=VALUE lines from standard input into .env (--if-missing: only empty keys)'],
        'db:check' => ['checkDatabase', 'Connect to the database and create missing tables'],
        'install:token' => ['issueInstallToken', 'Create the one-time administrator setup link (--url=, --if-no-admin, --quiet)'],
        'runner:check' => ['checkRunner', 'Call the runner service with the shared secret (RUNNER_URL, RUNNER_SECRET)'],
        'route:list' => ['listRoutes', 'List the API routes'],
    ];

    /** @var Application */
    private $app;
    /** @var array<string, string|bool> */
    private $options = [];

    public function __construct(Application $app)
    {
        $this->app = $app;
    }

    public function run(array $argv): int
    {
        Lang::setLocale('en');
        $command = $argv[1] ?? 'list';
        foreach (array_slice($argv, 2) as $argument) {
            if (preg_match('/^--([a-z-]+)(?:=(.*))?$/', $argument, $m)) {
                $this->options[$m[1]] = array_key_exists(2, $m) ? $m[2] : true;
            }
        }
        if (!isset(self::COMMANDS[$command])) {
            return $this->listCommands($command === 'list' || $command === 'help' ? 0 : 1);
        }
        try {
            return $this->{self::COMMANDS[$command][0]}();
        } catch (DatabaseException $e) {
            $this->error('Database: ' . $e->getMessage());
        } catch (Throwable $e) {
            $this->error(get_class($e) . ': ' . $e->getMessage());
        }
        return 1;
    }

    private function line(string $text = ''): void
    {
        fwrite(STDOUT, $text . PHP_EOL);
    }

    private function error(string $text): void
    {
        fwrite(STDERR, $text . PHP_EOL);
    }

    private function option(string $name, $default = null)
    {
        return $this->options[$name] ?? $default;
    }

    private function envFile(): string
    {
        $file = $this->app->basePath('.env');
        if (!is_file($file) && is_file($this->app->basePath('.env.example'))) {
            copy($this->app->basePath('.env.example'), $file);
            @chmod($file, 0640);
        }
        return $file;
    }

    private function listCommands(int $exitCode): int
    {
        $this->line('Arsika Rollout Manager ' . $this->app->version() . ' — php bin/console <command> [options]');
        $this->line();
        foreach (self::COMMANDS as $name => $command) {
            $this->line(sprintf('  %-15s %s', $name, $command[1]));
        }
        return $exitCode;
    }

    private function about(): int
    {
        $config = $this->app->config();
        $this->line('Arsika Rollout Manager dashboard ' . $this->app->version());
        $this->line('  Environment   ' . $config->get('app.env') . ($config->get('app.debug') ? ' (debug)' : ''));
        $this->line('  URL           ' . ($config->get('app.url') ?: '(APP_URL not set)'));
        $this->line('  PHP           ' . PHP_VERSION);
        $this->line('  APP_KEY       ' . ($config->get('app.key') ? 'set' : 'MISSING (php bin/console key:generate)'));
        $this->line('  Database      ' . $config->get('database.default'));
        $this->line('  Runner        ' . $config->get('runner.url') . ($config->get('runner.secret') ? '' : ' (RUNNER_SECRET MISSING)')
            . ($this->app->make(RunnerClient::class)->alive() ? ' (running)' : ' (not reachable)'));
        return 0;
    }

    private function generateKey(): int
    {
        $file = $this->envFile();
        $current = (string)(Env::parse(is_file($file) ? (string)file_get_contents($file) : '')['APP_KEY'] ?? '');
        if ($current !== '' && !$this->option('force')) {
            $this->line('APP_KEY already set in .env (kept). Changing it makes every password unusable.');
            return 0;
        }
        Env::write($file, ['APP_KEY' => Crypto::generateKey()]);
        $this->line('APP_KEY written to .env.');
        return 0;
    }

    private function setEnv(): int
    {
        $file = $this->envFile();
        $existing = Env::parse(is_file($file) ? (string)file_get_contents($file) : '');
        $changes = [];
        while (($line = fgets(STDIN)) !== false) {
            $line = rtrim($line, "\r\n");
            if (!preg_match('/^([A-Z][A-Z0-9_]*)=(.*)$/', $line, $m)) {
                continue;
            }
            if ($this->option('if-missing') && (string)($existing[$m[1]] ?? '') !== '') {
                continue;
            }
            $changes[$m[1]] = $m[2];
        }
        if ($changes) {
            Env::write($file, $changes);
        }
        $this->line($changes ? 'Updated .env: ' . implode(', ', array_keys($changes)) : 'Nothing to change in .env.');
        return 0;
    }

    private function checkDatabase(): int
    {
        $db = $this->app->make(Database::class);
        $db->pdo();
        $this->line('Database OK: ' . $db->serverVersion() . ' (host ' . $db->host() . ')');
        $this->line('  Schema version ' . $db->meta('schema_version') . ' · ' . (int)$db->value('SELECT COUNT(*) FROM users') . ' administrator account(s)');
        return 0;
    }

    private function issueInstallToken(): int
    {
        $installer = $this->app->make(Installer::class);
        if ($this->option('if-no-admin') && $installer->hasAdmin()) {
            if (!$this->option('quiet')) {
                $this->line('An administrator account already exists; no setup link was created.');
            }
            return 0;
        }
        $token = $installer->issueToken();
        $base = rtrim((string)($this->option('url') ?: config('app.url') ?: 'http://localhost'), '/');
        $url = $base . '/install/?token=' . $token;
        if ($this->option('quiet')) {
            $this->line($url);
            return 0;
        }
        $this->line('One-time administrator setup link (valid for ' . (int)config('auth.install_token_hours', 24) . ' hours):');
        $this->line();
        $this->line('  ' . $url);
        $this->line();
        $this->line('Open it in a browser and choose the administrator password (username: admin).');
        $this->line('The page deletes itself afterwards; then sign in at ' . $base . '/#/login');
        return 0;
    }

    private function checkRunner(): int
    {
        try {
            $status = $this->app->make(RunnerClient::class)->get('/v1/status');
        } catch (HttpException $e) {
            $this->error('Runner: ' . $e->getMessage() . ($e->getDetails() !== '' ? ' (' . $e->getDetails() . ')' : ''));
            return 1;
        }
        $this->line('Runner OK: version ' . $status['version'] . ' · Python ' . $status['python'] . ' · ' . $status['sites'] . ' site(s) · '
            . (int)($status['github_tokens'] ?? 0) . ' with a GitHub token');
        return 0;
    }

    private function listRoutes(): int
    {
        foreach ($this->app->router()->routes() as $route) {
            $this->line(sprintf('  %-7s %-58s %s@%s  [%s]', $route->method, $route->path,
                str_replace('App\\Http\\Controllers\\', '', $route->action[0]), $route->action[1], implode(', ', $route->middleware)));
        }
        return 0;
    }
}

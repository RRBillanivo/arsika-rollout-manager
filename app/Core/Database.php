<?php

namespace App\Core;

use PDO;
use PDOException;
use PDOStatement;

/**
 * PDO connection with automatic schema creation (database/schema/<driver>.sql).
 *   mysql  : production (MySQL / MariaDB), configured in .env
 *   sqlite : local development and tests (storage/database/anemia.sqlite)
 * All timestamps are produced by PHP in the configured time zone, so both drivers store identical values.
 */
final class Database
{
    const SCHEMA_VERSION = 2;                     // 2: servers and site_servers (dashboard 1.2.0)

    /** @var array */
    private $config;
    /** @var string */
    private $schemaPath;
    /** @var PDO|null */
    private $pdo = null;
    /** @var string */
    private $driver = '';
    /** @var string */
    private $host = '';

    public function __construct(array $config, string $schemaPath)
    {
        $this->config = $config;
        $this->schemaPath = $schemaPath;
    }

    public function pdo(): PDO
    {
        if ($this->pdo === null) {
            $this->connect();
            $this->migrate();
        }
        return $this->pdo;
    }

    /** true when a connection was opened during this request (never connects by itself). */
    public function isConnected(): bool
    {
        return $this->pdo !== null;
    }

    public function driver(): string
    {
        $this->pdo();
        return $this->driver;
    }

    public function host(): string
    {
        $this->pdo();
        return $this->host;
    }

    public function serverVersion(): string
    {
        return $this->driver() === 'mysql'
            ? 'MySQL/MariaDB ' . (string)$this->value('SELECT VERSION()')
            : 'SQLite ' . (string)$this->value('SELECT sqlite_version()');
    }

    private function connect(): void
    {
        $driver = (string)($this->config['default'] ?? 'sqlite');
        $options = [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                    PDO::ATTR_EMULATE_PREPARES => false, PDO::ATTR_TIMEOUT => 5];
        if ($driver === 'mysql') {
            if (!extension_loaded('pdo_mysql')) {
                throw new DatabaseException(trans('database.pdo_mysql_missing'));
            }
            $c = (array)($this->config['connections']['mysql'] ?? []);
            $host = (string)($c['host'] ?? '127.0.0.1');
            $error = null;
            foreach ($host === 'localhost' ? ['localhost', '127.0.0.1'] : [$host] as $candidate) {   // socket failed -> TCP
                try {
                    $dsn = 'mysql:host=' . $candidate . ';port=' . (int)($c['port'] ?? 3306) . ';dbname=' . (string)($c['database'] ?? '')
                        . ';charset=' . (string)($c['charset'] ?? 'utf8mb4');
                    $this->pdo = new PDO($dsn, (string)($c['username'] ?? ''), (string)($c['password'] ?? ''), $options);
                    $this->host = $candidate;
                    break;
                } catch (PDOException $e) {
                    $error = $e;
                }
            }
            if ($this->pdo === null) {
                throw new DatabaseException(trans('database.connection_failed', ['reason' => $this->describe($error)]));
            }
            $this->driver = 'mysql';
            return;
        }
        if (!extension_loaded('pdo_sqlite')) {
            throw new DatabaseException(trans('database.not_configured'));
        }
        $file = (string)($this->config['connections']['sqlite']['database'] ?? '');
        if (!is_dir(dirname($file)) && !@mkdir(dirname($file), 0770, true)) {
            throw new DatabaseException(trans('database.sqlite_directory', ['path' => dirname($file)]));
        }
        try {
            $this->pdo = new PDO('sqlite:' . $file, null, null, $options);
            $this->pdo->exec('PRAGMA busy_timeout = 5000');
            $this->pdo->exec('PRAGMA journal_mode = WAL');
        } catch (PDOException $e) {
            throw new DatabaseException(trans('database.sqlite_failed', ['reason' => $e->getMessage()]));
        }
        $this->driver = 'sqlite';
        $this->host = 'file';
    }

    /** Connection error without credentials (PDO never prints passwords, but the text is still shortened). */
    private function describe(?PDOException $e): string
    {
        if ($e === null) {
            return trans('database.reason_unknown');
        }
        $message = $e->getMessage();
        foreach (['[1045]' => 'reason_access_denied', '[1049]' => 'reason_unknown_database', '[2002]' => 'reason_no_server'] as $code => $key) {
            if (strpos($message, $code) !== false) {
                return trans('database.' . $key);
            }
        }
        return substr($message, 0, 200);
    }

    /** Create the tables when the schema version stored in app_meta is older than SCHEMA_VERSION. */
    public function migrate(): void
    {
        try {
            if ((int)$this->value("SELECT meta_value FROM app_meta WHERE meta_key = 'schema_version'") >= self::SCHEMA_VERSION) {
                return;
            }
        } catch (PDOException $e) {
            // app_meta does not exist yet: new database
        }
        $file = $this->schemaPath . DIRECTORY_SEPARATOR . $this->driver . '.sql';
        try {
            foreach (preg_split('/;\s*(?:\r?\n|$)/', (string)file_get_contents($file)) as $sql) {
                $sql = trim((string)preg_replace('/^\s*--.*$/m', '', $sql));
                if ($sql !== '') {
                    $this->pdo->exec($sql);
                }
            }
            $this->setMeta('schema_version', (string)self::SCHEMA_VERSION);
            if ($this->meta('created_at') === null) {
                $this->setMeta('created_at', $this->now());
            }
        } catch (PDOException $e) {
            throw new DatabaseException(trans('database.migration_failed', ['reason' => substr($e->getMessage(), 0, 200)]));
        }
    }

    // ------------------------------------------------------------------ queries

    public function statement(string $sql, array $params = []): PDOStatement
    {
        $statement = $this->pdo()->prepare($sql);
        $statement->execute(array_values($params));
        return $statement;
    }

    public function first(string $sql, array $params = []): ?array
    {
        $row = $this->statement($sql, $params)->fetch();
        return is_array($row) ? $row : null;
    }

    public function select(string $sql, array $params = []): array
    {
        return $this->statement($sql, $params)->fetchAll();
    }

    /** First column of the first row (e.g. COUNT(*)), or null. */
    public function value(string $sql, array $params = [])
    {
        $value = $this->statement($sql, $params)->fetchColumn();
        return $value === false ? null : $value;
    }

    /** INSERT from column => value; returns the new auto-increment id (0 for text keys). */
    public function insert(string $table, array $data): int
    {
        $columns = array_keys($data);
        $this->statement('INSERT INTO ' . $table . ' (' . implode(', ', $columns) . ') VALUES ('
            . implode(', ', array_fill(0, count($columns), '?')) . ')', array_values($data));
        return (int)$this->pdo()->lastInsertId();
    }

    /** UPDATE one row by key from column => value; returns the number of changed rows. */
    public function update(string $table, $id, array $data, string $key = 'id'): int
    {
        if (!$data) {
            return 0;
        }
        $set = implode(', ', array_map(static function ($column) {
            return $column . ' = ?';
        }, array_keys($data)));
        return $this->statement('UPDATE ' . $table . ' SET ' . $set . ' WHERE ' . $key . ' = ?',
            array_merge(array_values($data), [$id]))->rowCount();
    }

    /** DATETIME value in the configured time zone. */
    public function now(?int $timestamp = null): string
    {
        return date('Y-m-d H:i:s', $timestamp === null ? time() : $timestamp);
    }

    /** SQL expression for the first $length characters of a date column: 10 = day, 13 = hour (same on both drivers). */
    public function datePrefix(string $column, int $length): string
    {
        return 'SUBSTR(CAST(' . $column . ' AS CHAR), 1, ' . $length . ')';
    }

    public function meta(string $key): ?string
    {
        $value = $this->value('SELECT meta_value FROM app_meta WHERE meta_key = ?', [$key]);
        return $value === null ? null : (string)$value;
    }

    public function setMeta(string $key, ?string $value): void
    {
        if ($this->statement('UPDATE app_meta SET meta_value = ? WHERE meta_key = ?', [$value, $key])->rowCount() === 0
            && $this->value('SELECT COUNT(*) FROM app_meta WHERE meta_key = ?', [$key]) == 0) {
            $this->statement('INSERT INTO app_meta (meta_key, meta_value) VALUES (?, ?)', [$key, $value]);
        }
    }

    public function deleteMeta(string $key): void
    {
        $this->statement('DELETE FROM app_meta WHERE meta_key = ?', [$key]);
    }
}

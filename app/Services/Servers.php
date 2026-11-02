<?php

namespace App\Services;

use App\Core\Database;
use App\Core\HttpException;

/**
 * The servers this dashboard controls: "local" (the runner of this server, from .env) and the runners of other servers
 * (servers table; secrets encrypted with APP_KEY). site_servers records which server a site lives on; a site without a
 * row belongs to this server, so a dashboard of version 1.1 keeps working unchanged.
 */
final class Servers
{
    const LOCAL = 'local';

    /** @var Database */
    private $db;
    /** @var Crypto */
    private $crypto;

    public function __construct(Database $db, Crypto $crypto)
    {
        $this->db = $db;
        $this->crypto = $crypto;
    }

    /** The other servers (without their secrets), by name. */
    public function remote(): array
    {
        return array_map([$this, 'publicRow'], $this->db->select('SELECT * FROM servers ORDER BY name, id'));
    }

    public function find(string $key): ?array
    {
        if ($key === self::LOCAL) {
            return ['id' => self::LOCAL, 'name' => '', 'url' => (string)config('runner.url'), 'local' => true, 'tls_pin' => '',
                    'configured' => (string)config('runner.secret', '') !== ''];
        }
        if (!ctype_digit($key)) {
            return null;
        }
        $row = $this->db->first('SELECT * FROM servers WHERE id = ?', [(int)$key]);
        return $row ? $this->publicRow($row) : null;
    }

    public function get(string $key): array
    {
        $server = $this->find($key);
        if ($server === null) {
            throw HttpException::notFound(trans('servers.not_found'), 'server_not_found');
        }
        return $server;
    }

    /** this server first, then the others */
    public function all(): array
    {
        return array_merge([$this->get(self::LOCAL)], $this->remote());
    }

    public function client(string $key): RunnerClient
    {
        if ($key === self::LOCAL) {
            return new RunnerClient();
        }
        $row = ctype_digit($key) ? $this->db->first('SELECT * FROM servers WHERE id = ?', [(int)$key]) : null;
        if (!$row) {
            throw HttpException::notFound(trans('servers.not_found'), 'server_not_found');
        }
        return new RunnerClient((string)$row['url'], (string)$this->crypto->decrypt((string)$row['secret']), (string)$row['tls_pin'], 0,
            (string)$row['name']);
    }

    // ------------------------------------------------------------------ sites

    /** Key of the server a site lives on. */
    public function serverOf(string $site): string
    {
        $id = $this->db->value('SELECT server_id FROM site_servers WHERE site_id = ?', [$site]);
        return $id === null ? self::LOCAL : (string)$id;
    }

    public function clientForSite(string $site): RunnerClient
    {
        return $this->client($this->serverOf($site));
    }

    /** site id => server key, for the sites on other servers */
    public function siteMap(): array
    {
        $map = [];
        foreach ($this->db->select('SELECT site_id, server_id FROM site_servers') as $row) {
            $map[(string)$row['site_id']] = (string)$row['server_id'];
        }
        return $map;
    }

    public function assign(string $site, string $server): void
    {
        $this->db->statement('DELETE FROM site_servers WHERE site_id = ?', [$site]);
        if ($server !== self::LOCAL) {
            $this->db->insert('site_servers', ['site_id' => $site, 'server_id' => (int)$server, 'created_at' => $this->db->now()]);
        }
    }

    public function forget(string $site): void
    {
        $this->db->statement('DELETE FROM site_servers WHERE site_id = ?', [$site]);
    }

    // ------------------------------------------------------------------ servers

    public function add(string $name, string $url, string $secret, string $pin): string
    {
        $now = $this->db->now();
        return (string)$this->db->insert('servers', ['name' => $name, 'url' => $url, 'secret' => (string)$this->crypto->encrypt($secret),
            'tls_pin' => $pin === '' ? null : $pin, 'created_at' => $now, 'updated_at' => $now]);
    }

    /** @param array{name?: string, url?: string, secret?: string, tls_pin?: string} $changes */
    public function change(string $key, array $changes): void
    {
        $data = ['updated_at' => $this->db->now()];
        foreach (['name', 'url'] as $field) {
            if (isset($changes[$field])) {
                $data[$field] = $changes[$field];
            }
        }
        if (isset($changes['secret']) && $changes['secret'] !== '') {
            $data['secret'] = (string)$this->crypto->encrypt($changes['secret']);
        }
        if (array_key_exists('tls_pin', $changes)) {
            $data['tls_pin'] = $changes['tls_pin'] === '' ? null : $changes['tls_pin'];
        }
        $this->db->update('servers', (int)$key, $data);
    }

    /** The server and the records of its sites; the sites themselves stay untouched on that server. */
    public function remove(string $key): int
    {
        $sites = (int)$this->db->value('SELECT COUNT(*) FROM site_servers WHERE server_id = ?', [(int)$key]);
        $this->db->statement('DELETE FROM site_servers WHERE server_id = ?', [(int)$key]);
        $this->db->statement('DELETE FROM servers WHERE id = ?', [(int)$key]);
        return $sites;
    }

    public function urlTaken(string $url, string $except = ''): bool
    {
        return $this->db->value('SELECT id FROM servers WHERE url = ? AND id <> ?', [$url, $except === '' ? 0 : (int)$except]) !== null;
    }

    /** The saved secret of a server (for a connection test with a changed address or pin). */
    public function secretOf(string $key): string
    {
        $value = ctype_digit($key) ? $this->db->value('SELECT secret FROM servers WHERE id = ?', [(int)$key]) : null;
        return $value === null ? '' : (string)$this->crypto->decrypt((string)$value);
    }

    private function publicRow(array $row): array
    {
        return ['id' => (string)$row['id'], 'name' => (string)$row['name'], 'url' => (string)$row['url'], 'local' => false,
                'tls_pin' => (string)($row['tls_pin'] ?? ''), 'configured' => true, 'created_at' => $row['created_at'],
                'updated_at' => $row['updated_at']];
    }
}

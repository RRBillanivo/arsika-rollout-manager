<?php

namespace App\Http\Controllers;

use App\Core\HttpException;
use App\Core\Request;
use App\Core\Response;
use App\Services\RunnerClient;
use App\Services\Servers;

/**
 * The servers this dashboard controls. Another server runs only the runner (deploy/install.sh --agent), which answers
 * on HTTPS with a self-signed certificate; the dashboard pins that certificate's public key and signs every request
 * with the server's own secret. A connection is tested before a server is saved.
 */
final class ServerController extends Controller
{
    const PIN = '/^sha256\/\/[A-Za-z0-9+\/]{43}=$/';

    /** @var Servers */
    private $servers;

    public function __construct(Servers $servers)
    {
        $this->servers = $servers;
    }

    /** GET /api/v1/servers — every server with the state of its runner (all asked at the same time). */
    public function index(Request $request): Response
    {
        $servers = $this->servers->all();
        $requests = [];
        foreach ($servers as $server) {
            if ($server['configured']) {
                $requests[$server['id']] = [$this->servers->client($server['id']), 'GET', '/v1/status'];
            }
        }
        $answers = RunnerClient::parallel($requests);
        $counts = array_count_values($this->servers->siteMap());
        foreach ($servers as $i => $server) {
            $answer = $answers[$server['id']] ?? null;
            $servers[$i]['status'] = is_array($answer) ? $answer : null;
            $servers[$i]['error'] = $answer instanceof HttpException ? $answer->getMessage() : ($server['configured'] ? '' : trans('runner.not_configured'));
            $servers[$i]['site_count'] = is_array($answer) ? (int)($answer['sites'] ?? 0) : ($counts[$server['id']] ?? null);
        }
        return Response::json($servers);
    }

    /** POST /api/v1/servers/test {url, secret, tls_pin, server?} — try a connection without saving anything. */
    public function test(Request $request): Response
    {
        $existing = $request->string('server', 10);
        $values = $this->validated($request, $existing !== '' ? $this->servers->get($existing) : null, false);
        return Response::json(['status' => $this->reach($values)]);
    }

    /** POST /api/v1/servers {name, url, secret, tls_pin, password} */
    public function store(Request $request): Response
    {
        $this->confirmPassword($request);
        $values = $this->validated($request, null, true);
        $status = $this->reach($values);
        $client = new RunnerClient($values['url'], $values['secret'], $values['tls_pin'], 0, $values['name']);
        $sites = array_column((array)$client->get('/v1/sites?health=0'), 'id');
        $this->checkSiteIds($sites, '');
        $key = $this->servers->add($values['name'], $values['url'], $values['secret'], $values['tls_pin']);
        foreach ($sites as $site) {
            $this->servers->assign((string)$site, $key);
        }
        $this->audit($request, 'server_added', 'server:' . $key, $values['name'] . ' · ' . $values['url'] . ' · ' . count($sites) . ' site(s)');
        return Response::json($this->servers->get($key) + ['status' => $status, 'error' => '', 'site_count' => count($sites)], 201);
    }

    /** PUT /api/v1/servers/{server} {name, url, secret (empty: unchanged), tls_pin, password} */
    public function update(Request $request, string $server): Response
    {
        $this->confirmPassword($request);
        $current = $this->servers->get($server);
        if ($current['local']) {
            throw HttpException::badRequest(trans('servers.local_fixed'));
        }
        $values = $this->validated($request, $current, false);
        $status = $this->reach($values);
        $changes = ['name' => $values['name'], 'url' => $values['url'], 'tls_pin' => $values['tls_pin']];
        if ($request->string('secret', 300) !== '') {
            $changes['secret'] = $values['secret'];
        }
        $this->servers->change($server, $changes);
        $changed = array_keys(array_filter(['name' => $values['name'] !== $current['name'], 'url' => $values['url'] !== $current['url'],
            'tls_pin' => $values['tls_pin'] !== $current['tls_pin'], 'secret' => isset($changes['secret'])]));
        $this->audit($request, 'server_updated', 'server:' . $server, $values['name'] . ($changed ? ' · ' . implode(', ', $changed) : ''));
        return Response::json($this->servers->get($server) + ['status' => $status, 'error' => '', 'site_count' => (int)($status['sites'] ?? 0)]);
    }

    /** DELETE /api/v1/servers/{server} {password} — the dashboard forgets the server; nothing changes on it. */
    public function destroy(Request $request, string $server): Response
    {
        $this->confirmPassword($request);
        $current = $this->servers->get($server);
        if ($current['local']) {
            throw HttpException::badRequest(trans('servers.local_fixed'));
        }
        $sites = $this->servers->remove($server);
        $this->audit($request, 'server_removed', 'server:' . $server, $current['name'] . ' · ' . $current['url'] . ' · ' . $sites . ' site(s)');
        return Response::json(['deleted' => $server, 'sites' => $sites]);
    }

    /** -> [name, url, secret, tls_pin]; a field left empty while editing keeps the saved value. */
    private function validated(Request $request, ?array $current, bool $nameRequired): array
    {
        $errors = [];
        $name = trim($request->string('name', 100));
        if ($name === '' && $current !== null) {
            $name = $current['name'];
        }
        if ($name === '' && $nameRequired) {
            $errors['name'] = trans('servers.name_required');
        } elseif (strlen($name) > 80) {
            $errors['name'] = trans('servers.name_long');
        }
        $url = $this->normalUrl($request->string('url', 300) ?: (string)($current['url'] ?? ''));
        if ($url === null) {
            $errors['url'] = trans('servers.url_invalid');
        } elseif ($this->servers->urlTaken($url, $current ? (string)$current['id'] : '')) {
            $errors['url'] = trans('servers.url_taken');
        }
        $secret = $request->string('secret', 300);
        if ($secret === '' && $current !== null && !$current['local']) {
            $secret = $this->servers->secretOf((string)$current['id']);
        }
        if ($secret === '') {
            $errors['secret'] = trans('servers.secret_required');
        } elseif (!preg_match('/^[\x21-\x7e]{16,256}$/', $secret)) {
            $errors['secret'] = trans('servers.secret_invalid');
        }
        $pin = $request->has('tls_pin') ? trim($request->string('tls_pin', 300)) : (string)($current['tls_pin'] ?? '');
        foreach ($pin === '' ? [] : explode(';', $pin) as $part) {
            if (!preg_match(self::PIN, trim($part))) {
                $errors['tls_pin'] = trans('servers.pin_invalid');
            }
        }
        if ($errors) {
            throw HttpException::validation($errors);
        }
        return ['name' => $name !== '' ? $name : (string)parse_url((string)$url, PHP_URL_HOST), 'url' => (string)$url, 'secret' => $secret,
                'tls_pin' => $pin];
    }

    /** https://host[:port] (http only for an address on this machine); null when invalid. */
    private function normalUrl(string $url): ?string
    {
        $parts = parse_url(trim($url));
        if (!is_array($parts) || empty($parts['host']) || !in_array(strtolower($parts['scheme'] ?? ''), ['http', 'https'], true)
            || isset($parts['user']) || isset($parts['query']) || isset($parts['fragment']) || !in_array($parts['path'] ?? '', ['', '/'], true)) {
            return null;
        }
        $scheme = strtolower($parts['scheme']);
        $host = strtolower($parts['host']);
        if ($scheme === 'http' && !in_array($host, ['127.0.0.1', 'localhost', '[::1]'], true)) {
            return null;                                             // the network only sees HTTPS
        }
        return $scheme . '://' . $host . (isset($parts['port']) ? ':' . (int)$parts['port'] : '');
    }

    /** The runner's status, or a validation error on the address field with the reason. */
    private function reach(array $values)
    {
        $client = new RunnerClient($values['url'], $values['secret'], $values['tls_pin'], 15, $values['name'] ?: $values['url']);
        try {
            return $client->get('/v1/status');
        } catch (HttpException $e) {
            $field = ['runner_rejected' => 'secret', 'runner_pin_mismatch' => 'tls_pin', 'runner_certificate' => 'tls_pin'][$e->getErrorCode()] ?? 'url';
            throw HttpException::validation([$field => $e->getMessage()], $e->getMessage());
        }
    }

    /** A site id names one site in the whole dashboard. */
    private function checkSiteIds(array $sites, string $except): void
    {
        $map = $this->servers->siteMap();
        $local = [];
        try {
            $local = array_column((array)(new RunnerClient())->get('/v1/sites?health=0'), 'id');
        } catch (HttpException $e) {
            // this server's runner is not reachable or not configured: only the recorded sites are compared
        }
        $taken = [];
        foreach ($sites as $site) {
            if ((isset($map[$site]) && $map[$site] !== $except) || in_array($site, $local, true)) {
                $taken[] = $site;
            }
        }
        if ($taken) {
            throw HttpException::validation(['url' => trans('servers.site_ids_taken', ['sites' => implode(', ', $taken)])]);
        }
    }
}

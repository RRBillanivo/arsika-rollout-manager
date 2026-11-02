<?php

namespace App\Http\Controllers;

use App\Core\HttpException;
use App\Core\Request;
use App\Core\Response;
use App\Services\RunnerClient;
use App\Services\Servers;

/**
 * Registered sites, their GitHub releases, jobs (deploy, rollback, adopt, backup, restore, cleanup) and backups.
 * Each site lives on one server; its requests go to the runner of that server. The runners do the work; this
 * controller adds authentication, password confirmation and the audit trail.
 */
final class SiteController extends Controller
{
    /** Job types that change data or files irreversibly: the password is asked again. */
    const CONFIRMED_JOBS = ['adopt', 'restore', 'delete-release', 'cleanup-adopt'];
    const SITE_FIELDS = ['id', 'preset', 'name', 'domain', 'path', 'repo', 'asset_pattern', 'web_user', 'keep_releases', 'shared',
        'docroot_link', 'docroot_target', 'build', 'activate', 'services', 'reload_php_fpm', 'health_url', 'health_contains', 'health_local',
        'backup_before_deploy', 'backup_env_file', 'backup_paths', 'backup_exclude', 'backup_keep', 'adopt_keep',
        'deploy_mode', 'source', 'compose_files', 'compose_project', 'compose_build', 'backup_db', 'backup_db_service', 'backup_volumes'];

    /** @var Servers */
    private $servers;

    public function __construct(Servers $servers)
    {
        $this->servers = $servers;
    }

    /**
     * GET /api/v1/sites — every site of every server with its version, latest release, health and last job, plus the
     * servers that did not answer: {sites: [...], servers: [{id, name, local, ok, error}]}.
     */
    public function index(Request $request): Response
    {
        $servers = array_values(array_filter($this->servers->all(), static function ($server) {
            return $server['configured'] || !$server['local'];
        }));
        $requests = [];
        foreach ($servers as $server) {
            $requests[$server['id']] = [$this->servers->client($server['id']), 'GET', '/v1/sites'];
        }
        $answers = RunnerClient::parallel($requests);
        $map = $this->servers->siteMap();
        $localKnown = is_array($answers[Servers::LOCAL] ?? null) || !isset($requests[Servers::LOCAL]);
        $sites = [];
        $states = [];
        foreach ($servers as $server) {                              // this server first: its sites are known before the others
            $answer = $answers[$server['id']] ?? null;
            $states[] = ['id' => $server['id'], 'name' => $server['name'], 'local' => $server['local'], 'ok' => is_array($answer),
                         'error' => $answer instanceof HttpException ? $answer->getMessage() : ''];
            foreach (is_array($answer) ? $answer : [] as $site) {
                $owner = $map[$site['id']] ?? Servers::LOCAL;
                if (!$server['local'] && !isset($map[$site['id']]) && !isset($sites[$site['id']]) && $localKnown) {
                    $this->servers->assign($site['id'], $server['id']);       // a site created on that server by another dashboard
                    $map[$site['id']] = $owner = $server['id'];
                }
                if ($owner !== $server['id'] || isset($sites[$site['id']])) {
                    continue;                                                 // the same id on two servers: the recorded one wins
                }
                $sites[$site['id']] = $site + ['server' => $this->badge($server)];
            }
        }
        ksort($sites);
        return Response::json(['sites' => array_values($sites), 'servers' => $states]);
    }

    /** GET /api/v1/presets?server= — the presets of that server's runner (its web server user differs per server). */
    public function presets(Request $request): Response
    {
        return Response::json($this->servers->client($this->serverKey($request))->get('/v1/presets'));
    }

    /** POST /api/v1/sites {server, settings..., github_token, password} */
    public function store(Request $request): Response
    {
        $this->confirmPassword($request);
        $key = $this->serverKey($request);
        $server = $this->servers->get($key);
        $fields = $this->fields($request);
        $id = strtolower(trim((string)($fields['id'] ?? '')));
        if ($id !== '' && $this->idTaken($id, $key)) {
            throw HttpException::validation(['id' => trans('sites.id_taken_elsewhere')]);
        }
        $site = $this->servers->client($key)->post('/v1/sites', $fields + $this->tokenFields($request));
        $this->servers->assign($site['id'], $key);
        $this->audit($request, 'site_created', 'site:' . $site['id'], $site['domain'] . ' · ' . $site['repo']
            . ($server['local'] ? '' : ' · ' . $server['name'])
            . (!empty($site['github_token_set']) ? ' · token ' . $site['github_token_hint'] : ''));
        return Response::json($site + ['server' => $this->badge($server)], 201);
    }

    /** GET /api/v1/sites/{site} */
    public function show(Request $request, string $site): Response
    {
        $key = $this->servers->serverOf($site);
        return Response::json((array)$this->servers->client($key)->get('/v1/sites/' . $site) + ['server' => $this->badge($this->servers->get($key))]);
    }

    /** PUT /api/v1/sites/{site} {settings..., github_token | remove_github_token, password} */
    public function update(Request $request, string $site): Response
    {
        $this->confirmPassword($request);
        $fields = $this->fields($request);
        unset($fields['id'], $fields['preset']);
        $token = $this->tokenFields($request);
        $key = $this->servers->serverOf($site);
        $updated = $this->servers->client($key)->put('/v1/sites/' . $site, $fields + $token);
        $changes = array_keys($fields);
        if ($token) {
            $changes[] = isset($token['github_token']) ? 'github_token ' . $updated['github_token_hint'] : 'github_token removed';
        }
        $this->audit($request, 'site_updated', 'site:' . $site, implode(', ', $changes));
        return Response::json((array)$updated + ['server' => $this->badge($this->servers->get($key))]);
    }

    /** DELETE /api/v1/sites/{site} {password} — unregisters the site; its files on the server are not touched. */
    public function destroy(Request $request, string $site): Response
    {
        $this->confirmPassword($request);
        $result = $this->servers->clientForSite($site)->delete('/v1/sites/' . $site);
        $this->servers->forget($site);
        $this->audit($request, 'site_deleted', 'site:' . $site);
        return Response::json($result);
    }

    /** GET /api/v1/sites/{site}/releases?refresh=1 */
    public function releases(Request $request, string $site): Response
    {
        return Response::json($this->servers->clientForSite($site)->get('/v1/sites/' . $site . '/releases' . ($request->query('refresh') ? '?refresh=1' : '')));
    }

    /** GET /api/v1/sites/{site}/health — checked now (not cached). */
    public function health(Request $request, string $site): Response
    {
        return Response::json($this->servers->clientForSite($site)->get('/v1/sites/' . $site . '/health'));
    }

    /** POST /api/v1/sites/{site}/jobs {type, version, params, password (for adopt, restore and deletions)} */
    public function job(Request $request, string $site): Response
    {
        $type = $request->string('type', 30);
        $user = in_array($type, self::CONFIRMED_JOBS, true) ? $this->confirmPassword($request) : $this->user($request);
        $params = $request->input('params', []);
        $job = $this->servers->clientForSite($site)->post('/v1/sites/' . $site . '/jobs', [
            'type' => $type, 'version' => $request->string('version', 64) ?: null,
            'params' => is_array($params) ? $params : [], 'actor' => (string)$user['username'],
        ]);
        $this->audit($request, 'job_' . str_replace('-', '_', $type), 'site:' . $site,
            trim('#' . $job['id'] . ' ' . ($job['version'] ?? '') . (isset($params['backup_id']) ? ' ' . $params['backup_id'] : '')));
        return Response::json($job, 201);
    }

    /** GET /api/v1/sites/{site}/backups */
    public function backups(Request $request, string $site): Response
    {
        return Response::json($this->servers->clientForSite($site)->get('/v1/sites/' . $site . '/backups'));
    }

    /** DELETE /api/v1/sites/{site}/backups/{backup} {password} */
    public function destroyBackup(Request $request, string $site, string $backup): Response
    {
        $this->confirmPassword($request);
        $result = $this->servers->clientForSite($site)->delete('/v1/sites/' . $site . '/backups/' . rawurlencode($backup));
        $this->audit($request, 'backup_deleted', 'site:' . $site, $backup);
        return Response::json($result);
    }

    /**
     * POST /api/v1/github/check {repo, asset_pattern, source, github_token?, site? | server?} — can the runner that will
     * install the site read this repository's releases? Uses the token typed in the form, else the saved token of the
     * site being edited.
     */
    public function checkRepository(Request $request): Response
    {
        $repo = $request->string('repo', 200);
        if ($repo === '') {
            throw HttpException::validation(['repo' => trans('sites.repo_required')]);
        }
        $check = ['repo' => $repo, 'asset_pattern' => $request->string('asset_pattern', 100) ?: '*.zip',
                  'source' => $request->string('source', 10) ?: 'asset'];
        $token = $request->string('github_token', 255);
        $site = $request->string('site', 40);
        if ($token !== '') {
            $check['github_token'] = $token;
        }
        if ($site !== '') {
            $check['site'] = $site;
        }
        $client = $site !== '' ? $this->servers->clientForSite($site) : $this->servers->client($this->serverKey($request));
        return Response::json($client->post('/v1/github/check', $check));
    }

    private function serverKey(Request $request): string
    {
        $key = $request->string('server', 10) ?: (string)$request->query('server', '');
        return $key === '' ? Servers::LOCAL : $key;
    }

    /** Is the id used by a site on another server? (The runner of the chosen server checks its own sites.) */
    private function idTaken(string $id, string $key): bool
    {
        $owner = $this->servers->siteMap()[$id] ?? null;
        if ($owner !== null) {
            return $owner !== $key;
        }
        if ($key === Servers::LOCAL) {
            return false;
        }
        try {
            (new RunnerClient())->get('/v1/sites/' . $id);
            return true;                                             // registered on this server
        } catch (HttpException $e) {
            return false;
        }
    }

    private function badge(array $server): array
    {
        return ['id' => $server['id'], 'name' => $server['name'], 'local' => $server['local']];
    }

    private function fields(Request $request): array
    {
        $fields = [];
        foreach (self::SITE_FIELDS as $key) {
            if ($request->has($key)) {
                $fields[$key] = $request->input($key);
            }
        }
        return $fields;
    }

    /** The site's read-only GitHub token: write-only, kept by the runner, never logged or sent back. */
    private function tokenFields(Request $request): array
    {
        $token = $request->string('github_token', 255);
        if ($token !== '') {
            return ['github_token' => $token];
        }
        return $request->boolean('remove_github_token') ? ['remove_github_token' => true] : [];
    }
}

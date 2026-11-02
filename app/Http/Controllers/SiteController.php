<?php

namespace App\Http\Controllers;

use App\Core\HttpException;
use App\Core\Request;
use App\Core\Response;
use App\Services\RunnerClient;

/**
 * Registered sites, their GitHub releases, jobs (deploy, rollback, adopt, backup, restore, cleanup) and backups.
 * The runner does the work; this controller adds authentication, password confirmation and the audit trail.
 */
final class SiteController extends Controller
{
    /** Job types that change data or files irreversibly: the password is asked again. */
    const CONFIRMED_JOBS = ['adopt', 'restore', 'delete-release', 'cleanup-adopt'];
    const SITE_FIELDS = ['id', 'preset', 'name', 'domain', 'path', 'repo', 'asset_pattern', 'web_user', 'keep_releases', 'shared',
        'docroot_link', 'docroot_target', 'build', 'activate', 'services', 'reload_php_fpm', 'health_url', 'health_contains',
        'backup_before_deploy', 'backup_env_file', 'backup_paths', 'backup_exclude', 'backup_keep', 'adopt_keep'];

    /** @var RunnerClient */
    private $runner;

    public function __construct(RunnerClient $runner)
    {
        $this->runner = $runner;
    }

    /** GET /api/v1/sites — every site with its version, latest release, health and last job. */
    public function index(Request $request): Response
    {
        return Response::json($this->runner->get('/v1/sites'));
    }

    /** GET /api/v1/presets */
    public function presets(Request $request): Response
    {
        return Response::json($this->runner->get('/v1/presets'));
    }

    /** POST /api/v1/sites {settings..., github_token, password} */
    public function store(Request $request): Response
    {
        $this->confirmPassword($request);
        $site = $this->runner->post('/v1/sites', $this->fields($request) + $this->tokenFields($request));
        $this->audit($request, 'site_created', 'site:' . $site['id'], $site['domain'] . ' · ' . $site['repo']
            . (!empty($site['github_token_set']) ? ' · token ' . $site['github_token_hint'] : ''));
        return Response::json($site, 201);
    }

    /** GET /api/v1/sites/{site} */
    public function show(Request $request, string $site): Response
    {
        return Response::json($this->runner->get('/v1/sites/' . $site));
    }

    /** PUT /api/v1/sites/{site} {settings..., github_token | remove_github_token, password} */
    public function update(Request $request, string $site): Response
    {
        $this->confirmPassword($request);
        $fields = $this->fields($request);
        unset($fields['id'], $fields['preset']);
        $token = $this->tokenFields($request);
        $updated = $this->runner->put('/v1/sites/' . $site, $fields + $token);
        $changes = array_keys($fields);
        if ($token) {
            $changes[] = isset($token['github_token']) ? 'github_token ' . $updated['github_token_hint'] : 'github_token removed';
        }
        $this->audit($request, 'site_updated', 'site:' . $site, implode(', ', $changes));
        return Response::json($updated);
    }

    /** DELETE /api/v1/sites/{site} {password} — unregisters the site; its files on the server are not touched. */
    public function destroy(Request $request, string $site): Response
    {
        $this->confirmPassword($request);
        $result = $this->runner->delete('/v1/sites/' . $site);
        $this->audit($request, 'site_deleted', 'site:' . $site);
        return Response::json($result);
    }

    /** GET /api/v1/sites/{site}/releases?refresh=1 */
    public function releases(Request $request, string $site): Response
    {
        return Response::json($this->runner->get('/v1/sites/' . $site . '/releases' . ($request->query('refresh') ? '?refresh=1' : '')));
    }

    /** GET /api/v1/sites/{site}/health — checked now (not cached). */
    public function health(Request $request, string $site): Response
    {
        return Response::json($this->runner->get('/v1/sites/' . $site . '/health'));
    }

    /** POST /api/v1/sites/{site}/jobs {type, version, params, password (for adopt, restore and deletions)} */
    public function job(Request $request, string $site): Response
    {
        $type = $request->string('type', 30);
        $user = in_array($type, self::CONFIRMED_JOBS, true) ? $this->confirmPassword($request) : $this->user($request);
        $params = $request->input('params', []);
        $job = $this->runner->post('/v1/sites/' . $site . '/jobs', [
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
        return Response::json($this->runner->get('/v1/sites/' . $site . '/backups'));
    }

    /** DELETE /api/v1/sites/{site}/backups/{backup} {password} */
    public function destroyBackup(Request $request, string $site, string $backup): Response
    {
        $this->confirmPassword($request);
        $result = $this->runner->delete('/v1/sites/' . $site . '/backups/' . rawurlencode($backup));
        $this->audit($request, 'backup_deleted', 'site:' . $site, $backup);
        return Response::json($result);
    }

    /**
     * POST /api/v1/github/check {repo, asset_pattern, github_token?, site?} — can the runner read this repository's releases?
     * Uses the token typed in the form, else the saved token of the site being edited.
     */
    public function checkRepository(Request $request): Response
    {
        $repo = $request->string('repo', 200);
        if ($repo === '') {
            throw HttpException::validation(['repo' => trans('sites.repo_required')]);
        }
        $check = ['repo' => $repo, 'asset_pattern' => $request->string('asset_pattern', 100) ?: '*.zip'];
        $token = $request->string('github_token', 255);
        $site = $request->string('site', 40);
        if ($token !== '') {
            $check['github_token'] = $token;
        }
        if ($site !== '') {
            $check['site'] = $site;
        }
        return Response::json($this->runner->post('/v1/github/check', $check));
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

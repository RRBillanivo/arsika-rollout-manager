<?php

namespace App\Http\Controllers;

use App\Core\Request;
use App\Core\Response;
use App\Services\RunnerClient;

/** Runner state and the GitHub token (stored by the runner in its root-only configuration, never shown again). */
final class SettingsController extends Controller
{
    /** @var RunnerClient */
    private $runner;

    public function __construct(RunnerClient $runner)
    {
        $this->runner = $runner;
    }

    /** GET /api/v1/settings */
    public function show(Request $request): Response
    {
        return Response::json(['runner' => $this->runner->get('/v1/status'), 'settings' => $this->runner->get('/v1/settings'),
                               'php' => PHP_VERSION, 'dashboard_version' => app()->version()]);
    }

    /** PUT /api/v1/settings {github_token, password} — an empty token removes it. */
    public function update(Request $request): Response
    {
        $this->confirmPassword($request);
        $settings = $this->runner->put('/v1/settings', ['github_token' => $request->string('github_token', 255)]);
        $this->audit($request, 'github_token_changed', 'settings', $settings['github_token_set'] ? 'token ' . $settings['github_token_hint'] : 'token removed');
        return Response::json($settings);
    }
}

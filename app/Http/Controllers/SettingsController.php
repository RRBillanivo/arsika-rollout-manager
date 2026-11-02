<?php

namespace App\Http\Controllers;

use App\Core\Request;
use App\Core\Response;
use App\Services\RunnerClient;

/** Runner state and versions. GitHub tokens belong to the sites (see SiteController). */
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
        return Response::json(['runner' => $this->runner->get('/v1/status'), 'php' => PHP_VERSION,
                               'dashboard_version' => app()->version()]);
    }
}

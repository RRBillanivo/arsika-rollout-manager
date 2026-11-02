<?php

namespace App\Http\Controllers;

use App\Core\HttpException;
use App\Core\Request;
use App\Core\Response;
use App\Services\RunnerClient;

/** State of this server's runner and versions (other servers: ServerController). GitHub tokens belong to the sites. */
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
        try {
            $runner = $this->runner->get('/v1/status');
            $error = '';
        } catch (HttpException $e) {
            $runner = null;
            $error = $e->getMessage();
        }
        return Response::json(['runner' => $runner, 'runner_error' => $error, 'php' => PHP_VERSION, 'dashboard_version' => app()->version()]);
    }
}

<?php

namespace App\Http\Controllers;

use App\Core\Request;
use App\Core\Response;
use App\Services\RunnerClient;

/** Job history and live job logs. */
final class JobController extends Controller
{
    /** @var RunnerClient */
    private $runner;

    public function __construct(RunnerClient $runner)
    {
        $this->runner = $runner;
    }

    /** GET /api/v1/jobs?site=&limit= */
    public function index(Request $request): Response
    {
        $query = array_filter(['site' => (string)$request->query('site', ''), 'limit' => (string)(int)$request->query('limit', 50)]);
        return Response::json($this->runner->get('/v1/jobs?' . http_build_query($query)));
    }

    /** GET /api/v1/jobs/{job}?offset= — the job and the log text from byte "offset" (poll while it runs). */
    public function show(Request $request, string $job): Response
    {
        return Response::json($this->runner->get('/v1/jobs/' . (int)$job . '?offset=' . max(0, (int)$request->query('offset', 0))));
    }
}

<?php

namespace App\Http\Controllers;

use App\Core\HttpException;
use App\Core\Request;
use App\Core\Response;
use App\Services\RunnerClient;
use App\Services\Servers;

/** Job history and live job logs. Job numbers count per server, so a job is addressed through its site. */
final class JobController extends Controller
{
    /** @var Servers */
    private $servers;

    public function __construct(Servers $servers)
    {
        $this->servers = $servers;
    }

    /** GET /api/v1/jobs?site=&limit= — the jobs of a site (without site: the jobs of this server's runner). */
    public function index(Request $request): Response
    {
        $site = (string)$request->query('site', '');
        $query = array_filter(['site' => $site, 'limit' => (string)(int)$request->query('limit', 50)]);
        $client = $site !== '' && preg_match('/^[a-z0-9][a-z0-9-]{0,39}$/', $site) ? $this->servers->clientForSite($site) : new RunnerClient();
        return Response::json($client->get('/v1/jobs?' . http_build_query($query)));
    }

    /** GET /api/v1/sites/{site}/jobs/{job}?offset= — the job and the log text from byte "offset" (poll while it runs). */
    public function forSite(Request $request, string $site, string $job): Response
    {
        $record = $this->servers->clientForSite($site)->get('/v1/jobs/' . (int)$job . '?offset=' . max(0, (int)$request->query('offset', 0)));
        if (($record['site_id'] ?? '') !== $site) {
            throw HttpException::notFound(trans('jobs.not_found'), 'job_not_found');
        }
        return Response::json($record);
    }

    /** GET /api/v1/jobs/{job}?offset= — a job of this server's runner (links of version 1.1). */
    public function show(Request $request, string $job): Response
    {
        return Response::json((new RunnerClient())->get('/v1/jobs/' . (int)$job . '?offset=' . max(0, (int)$request->query('offset', 0))));
    }
}

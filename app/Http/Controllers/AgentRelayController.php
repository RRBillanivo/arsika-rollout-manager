<?php

namespace App\Http\Controllers;

use App\Core\Request;
use App\Core\Response;
use App\Services\Servers;

/**
 * For the agent of a hosting that the runner cannot open (another network, no public address): the agent calls the
 * dashboard instead, and its calls are passed on to the runner of the site. No session and no CSRF token: every call
 * is signed by the agent with the site's secret (or the one-time key of its file while it connects), and the runner
 * checks the signature; the dashboard keeps nothing.
 */
final class AgentRelayController extends Controller
{
    /** headers of the agent's call => query parameters of the runner */
    const FIELDS = ['ts' => 'X-Rollout-Timestamp', 'nonce' => 'X-Rollout-Nonce', 'sig' => 'X-Rollout-Signature',
                    'agent' => 'X-Rollout-Agent', 'url' => 'X-Rollout-Agent-Url', 'result' => 'X-Rollout-Result',
                    'status' => 'X-Rollout-Result-Status', 'type' => 'X-Rollout-Result-Type', 'wait' => 'X-Rollout-Wait'];

    /** @var Servers */
    private $servers;

    public function __construct(Servers $servers)
    {
        $this->servers = $servers;
    }

    /** GET /api/v1/agent/ping — the agent's page checks that the hosting can reach the dashboard (and compares the clocks). */
    public function ping(Request $request): Response
    {
        return Response::json(['app' => 'Arsika Rollout Manager', 'time' => time()]);
    }

    /**
     * POST /api/v1/agent/{site}/poll — the answer of the agent to the runner's last request (the body), then the
     * runner's next request for it; the runner holds the call up to X-Rollout-Wait seconds while there is nothing to do.
     */
    public function poll(Request $request, string $site): Response
    {
        $query = [];
        foreach (self::FIELDS as $key => $header) {
            $query[$key] = substr($request->header($header), 0, 600);
        }
        if (function_exists('set_time_limit')) {
            @set_time_limit(90);
        }
        $data = $this->servers->clientForSite($site)
            ->request('POST', '/v1/sites/' . $site . '/agent/poll?' . http_build_query($query), null, $request->body());
        return Response::json($data);
    }
}

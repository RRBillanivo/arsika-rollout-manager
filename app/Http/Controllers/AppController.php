<?php

namespace App\Http\Controllers;

use App\Core\Application;
use App\Core\HttpException;
use App\Core\Request;
use App\Core\Response;
use App\Models\User;
use App\Services\RunnerClient;
use App\Services\Servers;
use App\Services\SessionManager;

/** Bootstrap data for the single-page app. */
final class AppController extends Controller
{
    /** @var Application */
    private $app;
    /** @var SessionManager */
    private $session;
    /** @var User */
    private $users;
    /** @var RunnerClient */
    private $runner;
    /** @var Servers */
    private $servers;

    public function __construct(Application $app, SessionManager $session, User $users, RunnerClient $runner, Servers $servers)
    {
        $this->app = $app;
        $this->session = $session;
        $this->users = $users;
        $this->runner = $runner;
        $this->servers = $servers;
    }

    /**
     * GET /api/v1/app — version, CSRF token, signed-in account and (for administrators) the state of this server's
     * runner. A dashboard that only controls other servers may have no runner of its own.
     */
    public function show(Request $request): Response
    {
        $user = $this->session->user();
        $runner = null;
        if ($user !== null && !$this->runner->configured() && $this->servers->remote()) {
            $runner = ['ok' => true, 'configured' => false];
        } elseif ($user !== null) {
            try {
                $status = $this->runner->get('/v1/status');
                $runner = ['ok' => true, 'version' => $status['version'] ?? '', 'active_job' => $status['active_job'] ?? null];
            } catch (HttpException $e) {
                $runner = ['ok' => false, 'code' => $e->getErrorCode(), 'message' => $e->getMessage()];
            }
        }
        return Response::json([
            'app' => ['name' => config('app.name'), 'version' => $this->app->version()],
            'csrf_token' => $this->session->token(),
            'user' => $user ? $this->users->toArray($user) : null,
            'runner' => $runner,
            'password_min_length' => max(8, (int)config('auth.password_min_length', 12)),
        ]);
    }
}

<?php

namespace App\Http\Middleware;

use App\Core\HttpException;
use App\Core\Request;
use App\Core\Response;
use App\Services\SessionManager;

/** Requires a signed-in account and makes it available as $request->user(). */
final class Authenticate
{
    /** @var SessionManager */
    private $session;

    public function __construct(SessionManager $session)
    {
        $this->session = $session;
    }

    public function handle(Request $request, callable $next): Response
    {
        $user = $this->session->user();
        if ($user === null) {
            throw HttpException::unauthorized(trans('auth.login_required'));
        }
        $request->setUser($user);
        return $next($request);
    }
}

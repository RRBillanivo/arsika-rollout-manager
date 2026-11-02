<?php

namespace App\Http\Middleware;

use App\Core\HttpException;
use App\Core\Request;
use App\Core\Response;
use App\Services\SessionManager;

/** State-changing requests must send the session's CSRF token in the X-CSRF-Token header (see GET /api/v1/app). */
final class VerifyCsrfToken
{
    const SAFE_METHODS = ['GET', 'HEAD', 'OPTIONS'];

    /** @var SessionManager */
    private $session;

    public function __construct(SessionManager $session)
    {
        $this->session = $session;
    }

    public function handle(Request $request, callable $next): Response
    {
        if (!in_array($request->method(), self::SAFE_METHODS, true) && !$this->session->verifyCsrf($request->header('X-CSRF-Token'))) {
            throw HttpException::forbidden(trans('auth.csrf_expired'), 'csrf_token_mismatch');
        }
        return $next($request);
    }
}

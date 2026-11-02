<?php

namespace App\Http\Controllers;

use App\Core\HttpException;
use App\Core\Request;
use App\Core\Response;
use App\Models\LoginAttempt;
use App\Models\User;
use App\Services\Crypto;
use App\Services\SessionManager;

/** Sign-in and sign-out of administrators (username + password). */
final class AuthController extends Controller
{
    /** @var SessionManager */
    private $session;
    /** @var User */
    private $users;
    /** @var LoginAttempt */
    private $attempts;
    /** @var Crypto */
    private $crypto;

    public function __construct(SessionManager $session, User $users, LoginAttempt $attempts, Crypto $crypto)
    {
        $this->session = $session;
        $this->users = $users;
        $this->attempts = $attempts;
        $this->crypto = $crypto;
    }

    /** POST /api/v1/auth/login {username, password} */
    public function login(Request $request): Response
    {
        $username = strtolower($request->string('username', 64));
        $password = $request->password('password');
        if ($username === '' || $password === '') {
            throw new HttpException(422, 'validation_failed', trans('auth.credentials_required'));
        }
        $maxAttempts = (int)config('auth.lockout.max_attempts', 5);
        $minutes = (int)config('auth.lockout.decay_minutes', 15);
        list($failures, $ipFailures) = $this->attempts->recentFailures($username, $request->ip(), $minutes * 60);
        if ($failures >= $maxAttempts || $ipFailures >= (int)config('auth.lockout.max_attempts_per_ip', 20)) {
            throw HttpException::tooManyRequests(trans('auth.locked', ['minutes' => $minutes]));
        }
        $user = $this->users->findByIdentity($username);
        if ($user === null) {
            $this->crypto->equalizeTiming($password);
            $valid = false;
        } else {
            $valid = $this->users->verifyPassword($user, $password);
        }
        if (!$valid) {
            $this->attempts->record($user ? (int)$user['id'] : null, $username, false, $request);
            $left = $maxAttempts - 1 - $failures;
            throw new HttpException(401, 'invalid_credentials', trans('auth.failed')
                . ($left > 0 && $left <= 2 ? ' ' . trans('auth.attempts_left', ['count' => $left, 'minutes' => $minutes]) : ''));
        }
        if ($user['status'] !== User::STATUS_ACTIVE) {
            $this->attempts->record((int)$user['id'], $username, false, $request);
            throw HttpException::forbidden(trans('auth.blocked'), 'account_blocked');
        }
        $this->users->recordLogin($user, $password);
        $user = $this->users->find((int)$user['id']);
        $this->session->login($user, false);
        $this->attempts->record((int)$user['id'], $username, true, $request);
        return Response::json(['user' => $this->users->toArray($user), 'csrf_token' => $this->session->token()]);
    }

    /** POST /api/v1/auth/logout */
    public function logout(Request $request): Response
    {
        $this->session->logout();
        return Response::json(['csrf_token' => $this->session->token()]);
    }
}

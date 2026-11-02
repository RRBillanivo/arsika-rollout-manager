<?php

namespace App\Http\Controllers;

use App\Core\HttpException;
use App\Core\Request;
use App\Models\AuditLog;
use App\Models\User;
use App\Services\RateLimiter;

/** Base controller: helpers shared by all controllers. */
abstract class Controller
{
    const CONFIRM_MAX_FAILURES = 10;
    const CONFIRM_WINDOW = 900;

    /** The signed-in account (guaranteed by the "auth" middleware). */
    protected function user(Request $request): array
    {
        $user = $request->user();
        if ($user === null) {
            throw HttpException::unauthorized(trans('auth.login_required'));
        }
        return $user;
    }

    /**
     * Sensitive actions (restore, deletions, site settings, GitHub token) need the password again, so an open session
     * left on another computer cannot be misused. Ten wrong passwords in 15 minutes lock the confirmation.
     */
    protected function confirmPassword(Request $request): array
    {
        $user = $this->user($request);
        $limiter = app(RateLimiter::class);
        $key = 'confirm|' . $user['id'];
        if ($limiter->attempts($key, self::CONFIRM_WINDOW) >= self::CONFIRM_MAX_FAILURES) {
            throw HttpException::tooManyRequests(trans('auth.confirm_locked'));
        }
        if (!app(User::class)->verifyPassword($user, $request->password('password'))) {
            $limiter->hit($key, PHP_INT_MAX, self::CONFIRM_WINDOW, '');
            throw HttpException::validation(['password' => trans('auth.confirm_wrong')], trans('auth.confirm_wrong'));
        }
        return $user;
    }

    protected function audit(Request $request, string $action, string $target = '', string $details = ''): void
    {
        $user = $request->user();
        app(AuditLog::class)->record($user ? (int)$user['id'] : null, $action, $target, $details, $request->ip());
    }
}

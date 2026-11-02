<?php

namespace App\Http\Controllers;

use App\Core\Request;
use App\Core\Response;
use App\Models\AuditLog;
use App\Models\LoginAttempt;

/** Audit trail: the last 100 sign-ins and the last 200 administrator actions. */
final class ActivityController extends Controller
{
    /** @var LoginAttempt */
    private $attempts;
    /** @var AuditLog */
    private $audit;

    public function __construct(LoginAttempt $attempts, AuditLog $audit)
    {
        $this->attempts = $attempts;
        $this->audit = $audit;
    }

    /** GET /api/v1/activity */
    public function index(Request $request): Response
    {
        return Response::json(['logins' => $this->attempts->latest(100), 'actions' => $this->audit->latest(200),
                               'summary' => $this->attempts->lastDaySummary()]);
    }
}

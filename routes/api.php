<?php

/*
 * API routes. Every route lives under /api/v1 and answers JSON ({"data": ...} or {"error": ...}).
 * Middleware: csrf (X-CSRF-Token on state-changing requests), auth (signed-in administrator).
 */

use App\Core\Router;
use App\Http\Controllers\AccountController;
use App\Http\Controllers\AgentRelayController;
use App\Http\Controllers\ActivityController;
use App\Http\Controllers\AppController;
use App\Http\Controllers\AuthController;
use App\Http\Controllers\JobController;
use App\Http\Controllers\ServerController;
use App\Http\Controllers\SettingsController;
use App\Http\Controllers\SiteController;

return static function (Router $router): void {
    $router->pattern('site', '[a-z0-9][a-z0-9-]{0,39}');
    $router->pattern('backup', '[0-9A-Za-z][0-9A-Za-z._+-]{0,100}');
    $router->pattern('job', '\d{1,10}');
    $router->pattern('server', '(?:local|\d{1,10})');
    $router->pattern('upload', '[0-9a-f]{16}');
    $router->pattern('version', '[0-9A-Za-z][0-9A-Za-z._+-]{0,63}');

    // the agent of a hosting that the runner cannot open (another network) calls the dashboard instead: no session, no
    // CSRF token; every call is signed by the agent and checked by the runner of the site
    $router->group(['prefix' => '/api/v1/agent'], static function (Router $router): void {
        $router->get('/ping', [AgentRelayController::class, 'ping']);
        $router->post('/{site}/poll', [AgentRelayController::class, 'poll']);
    });

    $router->group(['prefix' => '/api/v1', 'middleware' => ['csrf']], static function (Router $router): void {
        $router->get('/app', [AppController::class, 'show']);
        $router->post('/auth/login', [AuthController::class, 'login']);
        $router->post('/auth/logout', [AuthController::class, 'logout']);

        $router->group(['middleware' => ['auth']], static function (Router $router): void {
            $router->get('/account', [AccountController::class, 'show']);
            $router->put('/account/password', [AccountController::class, 'updatePassword']);

            $router->get('/presets', [SiteController::class, 'presets']);
            $router->post('/github/check', [SiteController::class, 'checkRepository']);
            $router->get('/sites', [SiteController::class, 'index']);
            $router->post('/sites', [SiteController::class, 'store']);
            $router->get('/sites/{site}', [SiteController::class, 'show']);
            $router->put('/sites/{site}', [SiteController::class, 'update']);
            $router->delete('/sites/{site}', [SiteController::class, 'destroy']);
            $router->get('/sites/{site}/releases', [SiteController::class, 'releases']);
            $router->get('/sites/{site}/health', [SiteController::class, 'health']);
            $router->post('/sites/{site}/jobs', [SiteController::class, 'job']);
            $router->get('/sites/{site}/backups', [SiteController::class, 'backups']);
            $router->delete('/sites/{site}/backups/{backup}', [SiteController::class, 'destroyBackup']);
            $router->get('/sites/{site}/jobs/{job}', [JobController::class, 'forSite']);
            $router->post('/sites/{site}/agent-file', [SiteController::class, 'agentFile']);
            $router->post('/sites/{site}/agent/connect', [SiteController::class, 'connectAgent']);
            $router->get('/sites/{site}/plan', [SiteController::class, 'plan']);
            $router->post('/sites/{site}/uploads', [SiteController::class, 'uploadStart']);
            $router->post('/sites/{site}/uploads/{upload}', [SiteController::class, 'uploadChunk']);
            $router->post('/sites/{site}/uploads/{upload}/finish', [SiteController::class, 'uploadFinish']);
            $router->delete('/sites/{site}/uploads/{version}', [SiteController::class, 'destroyUpload']);
            $router->post('/sites/{site}/env/read', [SiteController::class, 'readEnv']);
            $router->put('/sites/{site}/env', [SiteController::class, 'saveEnv']);

            $router->get('/jobs', [JobController::class, 'index']);
            $router->get('/jobs/{job}', [JobController::class, 'show']);

            $router->get('/servers', [ServerController::class, 'index']);
            $router->post('/servers', [ServerController::class, 'store']);
            $router->post('/servers/test', [ServerController::class, 'test']);
            $router->put('/servers/{server}', [ServerController::class, 'update']);
            $router->delete('/servers/{server}', [ServerController::class, 'destroy']);
            $router->post('/servers/{server}/update', [ServerController::class, 'updateRunner']);
            $router->get('/servers/{server}/jobs/{job}', [JobController::class, 'forServer']);

            $router->get('/settings', [SettingsController::class, 'show']);

            $router->get('/activity', [ActivityController::class, 'index']);
        });
    });
};

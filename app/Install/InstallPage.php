<?php

namespace App\Install;

use App\Core\Application;
use App\Core\DatabaseException;
use App\Core\HttpException;
use App\Core\Request;
use App\Core\Response;
use App\Services\Installer;
use App\Services\RateLimiter;
use App\Services\Validator;
use App\Support\Lang;
use App\Support\Log;
use Throwable;

/**
 * The one-time administrator setup page (public/install/index.php, created by `php bin/console install:token`).
 * GET shows the form when the token in the link is valid; POST sets the password of the "admin" account, revokes the
 * token, deletes the page and redirects to the sign-in page. Indonesian and English (?lang=id|en).
 */
final class InstallPage
{
    /** @var Application */
    private $app;
    /** @var Installer */
    private $installer;
    /** @var Validator */
    private $validator;
    /** @var RateLimiter */
    private $limiter;

    public function __construct(Application $app, Installer $installer, Validator $validator, RateLimiter $limiter)
    {
        $this->app = $app;
        $this->installer = $installer;
        $this->validator = $validator;
        $this->limiter = $limiter;
    }

    public function handle(Request $request): Response
    {
        $this->app->instance(Request::class, $request);
        $requested = (string)$request->query('lang', '');
        Lang::setLocale(in_array($requested, Lang::LOCALES, true) ? $requested : Lang::detect($request));
        try {
            $response = $request->method() === 'POST' ? $this->submit($request) : $this->show($request);
        } catch (HttpException $e) {
            $response = $this->page(trans('install.invalid_title'), $this->box('red', $e->getMessage()), $e->getStatus());
        } catch (DatabaseException $e) {
            $response = $this->page(trans('install.invalid_title'), $this->box('red', $e->getMessage()), 503);
        } catch (Throwable $e) {
            Log::error($e);
            $response = $this->page(trans('install.invalid_title'), $this->box('red', trans('errors.server')), 500);
        }
        if (in_array($requested, Lang::LOCALES, true)) {
            $response->withHeader('Set-Cookie', 'lang=' . $requested . '; Path=/; Max-Age=31536000; SameSite=Lax');
        }
        return $response
            ->withHeader('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; form-action 'self'; base-uri 'self'; frame-ancestors 'none'")
            ->withHeader('Referrer-Policy', 'no-referrer')
            ->withHeader('X-Robots-Tag', 'noindex, nofollow');
    }

    private function show(Request $request): Response
    {
        $token = (string)$request->query('token', '');
        if (!$this->installer->isValidToken($token)) {
            $this->limiter->hit('install|' . $request->ip(), 20, 900, trans('install.throttled'));
            return $this->invalidLink();
        }
        return $this->form($token, []);
    }

    private function submit(Request $request): Response
    {
        $this->limiter->hit('install|' . $request->ip(), 20, 900, trans('install.throttled'));
        $token = $request->string('token', 100);
        if (!$this->installer->isValidToken($token)) {
            return $this->invalidLink();
        }
        $errors = [];
        list($password, $error) = $this->validator->password($request->password('password'), Installer::ADMIN_USERNAME);
        if ($error !== null) {
            $errors['password'] = $error;
        } elseif ($request->password('password_confirmation') !== $password) {
            $errors['password_confirmation'] = trans('validation.password_confirmation');
        }
        if ($errors) {
            return $this->form($token, $errors, 422);
        }

        $this->installer->setUpAdmin($password, $request->ip());
        $this->installer->revokeToken();
        $home = rtrim(str_replace('\\', '/', dirname(dirname((string)($_SERVER['SCRIPT_NAME'] ?? '/install/index.php')))), '/') . '/';
        if ($this->installer->removeStub()) {
            return Response::redirect($home . '?installed=1#/login');
        }
        return $this->page(trans('install.done_title'), $this->box('yellow', trans('install.stub_left'))
            . '<a class="btn btn--primary btn--block btn--large" href="' . $this->e($home) . '#/login">' . $this->e(trans('install.go_login')) . '</a>');
    }

    private function invalidLink(): Response
    {
        return $this->page(trans('install.invalid_title'), '<p class="muted">' . $this->e(trans('install.invalid_body')) . '</p>'
            . '<pre class="mono">php bin/console install:token</pre>', 403);
    }

    private function form(string $token, array $errors, int $status = 200): Response
    {
        $reset = $this->installer->hasAdmin();
        $field = function (string $name, string $label, string $help) use ($errors): string {
            $error = isset($errors[$name]) ? '<span class="field__error">' . $this->e($errors[$name]) . '</span>' : '';
            return '<label class="field' . ($error !== '' ? ' field--error' : '') . '"><span class="field__label">' . $this->e($label) . '</span>'
                . '<input type="password" name="' . $name . '" autocomplete="new-password" required>'
                . ($help !== '' ? '<span class="field__help">' . $this->e($help) . '</span>' : '') . $error . '</label>';
        };
        $body = '<p class="muted">' . $this->e(trans($reset ? 'install.reset_intro' : 'install.intro')) . '</p>'
            . $this->box('blue', trans('install.username_info'))
            . '<form method="post" novalidate>'
            . '<input type="hidden" name="token" value="' . $this->e($token) . '">'
            . '<input type="text" name="username" value="admin" autocomplete="username" hidden>'
            . $field('password', trans('install.password'), trans('install.password_help'))
            . $field('password_confirmation', trans('install.password_confirmation'), '')
            . ($errors ? $this->box('red', trans('validation.failed')) : '')
            . '<button class="btn btn--primary btn--block btn--large" type="submit">' . $this->e(trans('install.submit')) . '</button>'
            . '</form>';
        return $this->page(trans($reset ? 'install.reset_title' : 'install.page_title'), $body, $status, $token);
    }

    private function box(string $colour, string $text): string
    {
        return '<div class="box box--' . $colour . '"><div>' . $this->e($text) . '</div></div>';
    }

    private function page(string $title, string $body, int $status = 200, string $token = ''): Response
    {
        $name = $this->e((string)config('app.name', 'Arsika Rollout Manager'));
        $switch = '';
        foreach (Lang::LOCALES as $locale) {
            $query = http_build_query(array_filter(['token' => $token, 'lang' => $locale]));
            $switch .= $locale === Lang::locale() ? '<b>' . strtoupper($locale) . '</b>'
                : '<a href="?' . $this->e($query) . '" hreflang="' . $locale . '">' . strtoupper($locale) . '</a>';
        }
        $html = '<!doctype html><html lang="' . Lang::locale() . '"><head><meta charset="utf-8">'
            . '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow">'
            . '<meta name="color-scheme" content="light dark"><title>' . $this->e($title) . ' · ' . $name . '</title>'
            . '<link rel="icon" href="../assets/img/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="../assets/css/app.css">'
            . '<script src="../assets/js/theme-init.js"></script></head><body>'
            . '<main class="content container"><section class="auth">'
            . '<aside class="auth__panel auth__panel--simple" aria-hidden="true"><div class="auth__panel-content">'
            . '<img src="../assets/img/logo.svg" alt="" width="52" height="52"><h2>' . $name . '</h2>'
            . '<p>' . $this->e(trans('install.panel')) . '</p></div></aside>'
            . '<div class="auth__form card"><nav class="language-links" aria-label="Language">' . $switch . '</nav>'
            . '<h1>' . $this->e($title) . '</h1>' . $body . '</div>'
            . '</section></main></body></html>';
        return Response::html($html, $status);
    }

    private function e(string $text): string
    {
        return htmlspecialchars($text, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    }
}

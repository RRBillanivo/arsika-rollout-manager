<?php

use App\Core\Application;
use App\Core\Env;
use App\Support\Lang;

if (!function_exists('app')) {
    /** The application instance, or a service resolved from its container. */
    function app(?string $id = null)
    {
        $app = Application::getInstance();
        return $id === null ? $app : $app->make($id);
    }
}

if (!function_exists('config')) {
    /** Configuration value in dot notation, e.g. config('upload.max_photos'). */
    function config(string $key, $default = null)
    {
        return Application::getInstance()->config()->get($key, $default);
    }
}

if (!function_exists('env')) {
    /** Environment value from .env / the process environment ("true", "false", "null" and "" are converted). */
    function env(string $key, $default = null)
    {
        return Env::get($key, $default);
    }
}

if (!function_exists('base_path')) {
    function base_path(string $path = ''): string
    {
        return Application::getInstance()->basePath($path);
    }
}

if (!function_exists('storage_path')) {
    function storage_path(string $path = ''): string
    {
        return Application::getInstance()->basePath('storage' . ($path !== '' ? '/' . $path : ''));
    }
}

if (!function_exists('public_path')) {
    function public_path(string $path = ''): string
    {
        return Application::getInstance()->basePath('public' . ($path !== '' ? '/' . $path : ''));
    }
}

if (!function_exists('trans')) {
    /** User-facing text from lang/id.php, with :placeholders replaced. */
    function trans(string $key, array $replace = []): string
    {
        return Lang::get($key, $replace);
    }
}

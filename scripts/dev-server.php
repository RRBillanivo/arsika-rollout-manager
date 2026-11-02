<?php

/*
 * Router for PHP's built-in web server (local development only):
 *   php -S 127.0.0.1:8003 -t public scripts/dev-server.php
 * Mirrors the production web server rules: /api/* -> public/index.php, /install/ -> the one-time setup page,
 * static files from public/, everything else 404.
 */

if (PHP_SAPI !== 'cli-server') {
    http_response_code(404);
    exit;
}

$public = realpath(dirname(__DIR__) . '/public');
$path = rawurldecode((string)parse_url((string)$_SERVER['REQUEST_URI'], PHP_URL_PATH));

$notFound = static function (): bool {
    http_response_code(404);
    header('Content-Type: text/plain; charset=utf-8');
    echo 'Not found';
    return true;
};
$runScript = static function (string $file, string $scriptName): bool {
    $_SERVER['SCRIPT_NAME'] = $scriptName;
    $_SERVER['SCRIPT_FILENAME'] = $file;
    require $file;
    return true;
};

if (preg_match('#/\.#', $path)) {
    return $notFound();                                         // dotfiles (.env, .htaccess, ...)
}
if ($path === '/api' || strncmp($path, '/api/', 5) === 0) {
    return $runScript($public . '/index.php', '/index.php');
}
if (in_array($path, ['/install', '/install/', '/install/index.php'], true)) {
    return is_file($public . '/install/index.php') ? $runScript($public . '/install/index.php', '/install/index.php') : $notFound();
}

$file = realpath($public . ($path === '/' ? '/index.html' : $path));
if ($file === false || strpos($file, $public) !== 0 || !is_file($file) || strtolower(pathinfo($file, PATHINFO_EXTENSION)) === 'php') {
    return $notFound();
}
$types = ['html' => 'text/html; charset=utf-8', 'css' => 'text/css; charset=utf-8', 'js' => 'text/javascript; charset=utf-8',
          'svg' => 'image/svg+xml', 'png' => 'image/png', 'jpg' => 'image/jpeg', 'ico' => 'image/x-icon', 'json' => 'application/json',
          'woff2' => 'font/woff2', 'webmanifest' => 'application/manifest+json', 'txt' => 'text/plain; charset=utf-8'];
header('Content-Type: ' . ($types[strtolower(pathinfo($file, PATHINFO_EXTENSION))] ?? 'application/octet-stream'));
header('Cache-Control: no-cache');
readfile($file);
return true;

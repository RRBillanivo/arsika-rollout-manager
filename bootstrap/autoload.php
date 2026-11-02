<?php

/*
 * Class loading. Composer's autoloader is used when `composer install` has been run; otherwise this
 * file registers an equivalent PSR-4 loader for the App\ namespace, so the application has no
 * mandatory third-party dependencies.
 */

$root = dirname(__DIR__);

if (is_file($root . '/vendor/autoload.php')) {
    require $root . '/vendor/autoload.php';
    return;
}

spl_autoload_register(static function (string $class) use ($root): void {
    if (strncmp($class, 'App\\', 4) !== 0) {
        return;
    }
    $file = $root . '/app/' . str_replace('\\', '/', substr($class, 4)) . '.php';
    if (is_file($file)) {
        require $file;
    }
});

require $root . '/app/Support/helpers.php';

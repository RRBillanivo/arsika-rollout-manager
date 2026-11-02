<?php

/*
 * Creates the application instance: class loading, environment (.env), configuration (config/*.php),
 * time zone and error handling. Used by public/index.php, the installer page and bin/console.
 */

require __DIR__ . '/autoload.php';

return App\Core\Application::create(dirname(__DIR__));

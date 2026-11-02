<?php

/*
 * Front controller of the JSON API (/api/v1/...). The web server sends every /api/ request here;
 * the single-page app itself is public/index.html.
 */

$app = require dirname(__DIR__) . '/bootstrap/app.php';

$app->handle(App\Core\Request::capture())->send();

<?php

namespace App\Core;

use RuntimeException;

/** The database is not usable (not configured, wrong credentials, server down): answered with 503 and a clear message. */
final class DatabaseException extends RuntimeException
{
}

<?php

namespace App\Core;

use RuntimeException;

/** The installation is incomplete (e.g. APP_KEY missing): answered with 503 and instructions. */
final class ConfigurationException extends RuntimeException
{
}

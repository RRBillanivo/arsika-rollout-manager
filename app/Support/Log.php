<?php

namespace App\Support;

use Throwable;

/** Error log: storage/logs/app-YYYY-MM-DD.log (falls back to PHP's error_log when the folder is not writable). */
final class Log
{
    public static function error($error, array $context = []): void
    {
        if ($error instanceof Throwable) {
            $context += ['file' => $error->getFile() . ':' . $error->getLine()];
            $error = get_class($error) . ': ' . $error->getMessage();
        }
        $line = '[' . date('Y-m-d H:i:s') . '] ERROR ' . (string)$error
            . ($context ? ' ' . json_encode($context, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) : '') . "\n";
        $dir = storage_path('logs');
        if ((!is_dir($dir) && !@mkdir($dir, 0775, true)) || @file_put_contents($dir . '/app-' . date('Y-m-d') . '.log', $line,
                FILE_APPEND | LOCK_EX) === false) {
            error_log(rtrim($line));
        }
    }
}

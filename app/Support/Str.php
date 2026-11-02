<?php

namespace App\Support;

/** Multibyte-safe string helpers. */
final class Str
{
    public static function length(string $value): int
    {
        return function_exists('mb_strlen') ? mb_strlen($value, 'UTF-8') : strlen($value);
    }

    public static function limit(string $value, int $max): string
    {
        return function_exists('mb_substr') ? mb_substr($value, 0, $max, 'UTF-8') : substr($value, 0, $max);
    }

    /** Free text from users: control characters removed, whitespace collapsed, cut to $max characters. */
    public static function clean($value, int $max = 0): string
    {
        $value = preg_replace('/[\x00-\x1F\x7F]+/u', ' ', (string)$value);
        $value = trim((string)preg_replace('/\s+/u', ' ', (string)$value));
        return $max > 0 ? self::limit($value, $max) : $value;
    }

    /** Escape a user search term for a LIKE pattern (used with the default "\" escape character). */
    public static function likePattern(string $term): string
    {
        return '%' . str_replace(['\\', '%', '_'], ['\\\\', '\\%', '\\_'], strtolower($term)) . '%';
    }

    /** "20M" / "512K" / "1G" (php.ini notation) -> bytes. */
    public static function toBytes($value): int
    {
        $value = trim((string)$value);
        if ($value === '') {
            return 0;
        }
        $number = (float)$value;
        switch (strtolower(substr($value, -1))) {
            case 'g':
                return (int)($number * 1073741824);
            case 'm':
                return (int)($number * 1048576);
            case 'k':
                return (int)($number * 1024);
        }
        return (int)$number;
    }
}

<?php

namespace App\Support;

use App\Core\Request;

/**
 * Translator for user-facing texts in lang/<locale>.php (Indonesian and English). Keys use dot notation ("auth.failed")
 * and values may contain :placeholders. Missing keys fall back to Indonesian, then to the key itself.
 */
final class Lang
{
    const LOCALES = ['id', 'en'];
    const FALLBACK = 'id';

    /** @var string */
    private static $locale = self::FALLBACK;
    /** @var array<string, array> */
    private static $lines = [];

    public static function setLocale(string $locale): void
    {
        self::$locale = in_array($locale, self::LOCALES, true) ? $locale : self::FALLBACK;
    }

    public static function locale(): string
    {
        return self::$locale;
    }

    /** Locale requested by the client: X-Locale header (the app), "lang" cookie, then Accept-Language. */
    public static function detect(Request $request): string
    {
        $candidates = [$request->header('X-Locale'), (string)($_COOKIE['lang'] ?? '')];
        foreach (explode(',', $request->header('Accept-Language')) as $part) {
            $candidates[] = substr(trim($part), 0, 2);
        }
        foreach ($candidates as $candidate) {
            $candidate = strtolower(trim($candidate));
            if (in_array($candidate, self::LOCALES, true)) {
                return $candidate;
            }
        }
        return self::FALLBACK;
    }

    public static function get(string $key, array $replace = []): string
    {
        $value = self::lookup(self::$locale, $key);
        if ($value === null && self::$locale !== self::FALLBACK) {
            $value = self::lookup(self::FALLBACK, $key);
        }
        if ($value === null) {
            return $key;
        }
        if ($replace) {
            // longest placeholder first, so ":maximum" is not broken by ":max"
            uksort($replace, static function ($a, $b) {
                return strlen((string)$b) - strlen((string)$a);
            });
            foreach ($replace as $name => $text) {
                $value = str_replace(':' . $name, (string)$text, $value);
            }
        }
        return $value;
    }

    private static function lookup(string $locale, string $key): ?string
    {
        if (!isset(self::$lines[$locale])) {
            $file = base_path('lang/' . $locale . '.php');
            self::$lines[$locale] = is_file($file) ? (array)require $file : [];
        }
        $value = self::$lines[$locale];
        foreach (explode('.', $key) as $segment) {
            if (!is_array($value) || !array_key_exists($segment, $value)) {
                return null;
            }
            $value = $value[$segment];
        }
        return is_string($value) ? $value : null;
    }
}

<?php

namespace App\Core;

use RuntimeException;

/**
 * Reads and writes the .env file (KEY=VALUE per line, "#" comments, optional single or double quotes).
 * Variables set in the real process environment take precedence over the file.
 */
final class Env
{
    /** @var array<string, string> */
    private static $values = [];

    public static function load(string $file): void
    {
        self::$values = is_file($file) ? self::parse((string)file_get_contents($file)) : [];
    }

    /** @return array<string, string> */
    public static function parse(string $content): array
    {
        $values = [];
        foreach (preg_split('/\r\n|\r|\n/', $content) as $line) {
            $line = trim($line);
            if ($line === '' || $line[0] === '#') {
                continue;
            }
            if (strncmp($line, 'export ', 7) === 0) {
                $line = ltrim(substr($line, 7));
            }
            $pos = strpos($line, '=');
            if ($pos === false) {
                continue;
            }
            $key = trim(substr($line, 0, $pos));
            if (preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $key)) {
                $values[$key] = self::parseValue(trim(substr($line, $pos + 1)));
            }
        }
        return $values;
    }

    private static function parseValue(string $raw): string
    {
        if ($raw === '') {
            return '';
        }
        $quote = $raw[0];
        if (($quote === '"' || $quote === "'") && strlen($raw) >= 2 && substr($raw, -1) === $quote) {
            $inner = substr($raw, 1, -1);
            return $quote === '"' ? strtr($inner, ['\\\\' => '\\', '\\"' => '"', '\\n' => "\n"]) : $inner;
        }
        $comment = strpos($raw, ' #');
        return rtrim($comment === false ? $raw : substr($raw, 0, $comment));
    }

    /** The value exactly as written (no conversion of "true"/"false"/"null"), e.g. for passwords. */
    public static function raw(string $key, ?string $default = null): ?string
    {
        $value = getenv($key);
        if ($value === false) {
            $value = self::$values[$key] ?? null;
        }
        return $value === null ? $default : $value;
    }

    public static function get(string $key, $default = null)
    {
        $value = self::raw($key);
        if ($value === null) {
            return $default;
        }
        switch (strtolower($value)) {
            case 'true':
                return true;
            case 'false':
                return false;
            case 'null':
                return null;
        }
        return $value;
    }

    /**
     * Set keys in an .env file, keeping comments, order and every other key. Missing keys are appended.
     * The file is replaced atomically and is only readable by its owner and group.
     */
    public static function write(string $file, array $changes): void
    {
        if (is_link($file) && ($target = realpath($file)) !== false) {
            $file = $target;                // an installed release links .env to shared/.env: write the shared file
        }
        $lines = is_file($file) ? preg_split('/\r\n|\r|\n/', rtrim((string)file_get_contents($file), "\r\n")) : [];
        foreach ($lines as $i => $line) {
            if (preg_match('/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=/', $line, $m) && array_key_exists($m[1], $changes)) {
                $lines[$i] = $m[1] . '=' . self::quote((string)$changes[$m[1]]);
                unset($changes[$m[1]]);
            }
        }
        foreach ($changes as $key => $value) {
            $lines[] = $key . '=' . self::quote((string)$value);
        }
        $tmp = $file . '.tmp' . bin2hex(random_bytes(4));
        if (@file_put_contents($tmp, implode("\n", $lines) . "\n", LOCK_EX) === false) {
            throw new RuntimeException('Cannot write ' . $file);
        }
        @chmod($tmp, 0640);
        if (!@rename($tmp, $file)) {
            @unlink($tmp);
            throw new RuntimeException('Cannot replace ' . $file);
        }
    }

    private static function quote(string $value): string
    {
        if ($value === '' || preg_match('/^[A-Za-z0-9_.:\/@+\-]+$/', $value)) {
            return $value;
        }
        return '"' . str_replace(['\\', '"', "\n"], ['\\\\', '\\"', '\\n'], $value) . '"';
    }
}

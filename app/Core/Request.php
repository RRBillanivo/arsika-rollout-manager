<?php

namespace App\Core;

use App\Support\Str;

/** The current HTTP request: method, path, query, JSON/form input, uploaded files, client information. */
final class Request
{
    /** @var array */
    private $server;
    /** @var array */
    private $query;
    /** @var array */
    private $post;
    /** @var array */
    private $files;
    /** @var string */
    private $rawBody;
    /** @var array|null */
    private $json = null;
    /** @var array|null the signed-in user (set by the Authenticate middleware) */
    private $user = null;

    public function __construct(array $server, array $query, array $post, array $files, string $rawBody)
    {
        $this->server = $server;
        $this->query = $query;
        $this->post = $post;
        $this->files = $files;
        $this->rawBody = $rawBody;
    }

    public static function capture(): self
    {
        $type = (string)($_SERVER['CONTENT_TYPE'] ?? '');
        $raw = stripos($type, 'multipart/form-data') === false ? (string)file_get_contents('php://input') : '';
        return new self($_SERVER, $_GET, $_POST, $_FILES, $raw);
    }

    public function method(): string
    {
        return strtoupper((string)($this->server['REQUEST_METHOD'] ?? 'GET'));
    }

    /** Path relative to the application root, e.g. "/api/v1/auth/login" (also when installed in a sub-directory). */
    public function path(): string
    {
        $path = rawurldecode((string)parse_url((string)($this->server['REQUEST_URI'] ?? '/'), PHP_URL_PATH));
        $base = rtrim(str_replace('\\', '/', dirname((string)($this->server['SCRIPT_NAME'] ?? '/index.php'))), '/');
        if ($base !== '' && strncmp($path, $base . '/', strlen($base) + 1) === 0) {
            $path = substr($path, strlen($base));
        }
        return '/' . trim($path, '/');
    }

    public function query(string $key, $default = null)
    {
        $value = $this->query[$key] ?? $default;
        return is_scalar($value) ? $value : $default;
    }

    /** @return array decoded JSON body (or form fields for multipart requests) */
    public function all(): array
    {
        if ($this->json === null) {
            $decoded = $this->rawBody !== '' ? json_decode($this->rawBody, true) : null;
            $this->json = is_array($decoded) ? $decoded : [];
        }
        return $this->json + $this->post;
    }

    public function has(string $key): bool
    {
        return array_key_exists($key, $this->all());
    }

    public function input(string $key, $default = null)
    {
        $all = $this->all();
        return array_key_exists($key, $all) ? $all[$key] : $default;
    }

    /** Trimmed text input, cut to $max characters ('' when absent or not a scalar). */
    public function string(string $key, int $max = 500): string
    {
        $value = $this->input($key, '');
        return is_scalar($value) ? Str::limit(trim((string)$value), $max) : '';
    }

    /** Password input exactly as typed (not trimmed), '' when absent. */
    public function password(string $key): string
    {
        $value = $this->input($key, '');
        return is_string($value) ? substr($value, 0, 256) : '';
    }

    public function boolean(string $key): bool
    {
        return filter_var($this->input($key, false), FILTER_VALIDATE_BOOLEAN);
    }

    /** One uploaded file from a single file input, or null. */
    public function file(string $field): ?array
    {
        $f = $this->files[$field] ?? null;
        if (!is_array($f) || is_array($f['name'] ?? null) || (int)($f['error'] ?? UPLOAD_ERR_NO_FILE) === UPLOAD_ERR_NO_FILE) {
            return null;
        }
        return ['name' => (string)$f['name'], 'tmp' => (string)$f['tmp_name'], 'error' => (int)$f['error'], 'size' => (int)$f['size']];
    }

    /** Uploaded files from a multiple file input ("photos[]"). */
    public function fileList(string $field): array
    {
        $f = $this->files[$field] ?? null;
        if (!is_array($f) || !is_array($f['name'] ?? null)) {
            return [];
        }
        $list = [];
        foreach ($f['name'] as $i => $name) {
            if ((int)$f['error'][$i] === UPLOAD_ERR_NO_FILE) {
                continue;
            }
            $list[] = ['name' => (string)$name, 'tmp' => (string)$f['tmp_name'][$i], 'error' => (int)$f['error'][$i],
                       'size' => (int)$f['size'][$i]];
        }
        return $list;
    }

    /** The body as sent (a piece of an uploaded archive). */
    public function body(): string
    {
        return $this->rawBody;
    }

    /** true when the body was larger than post_max_size (PHP then drops all fields and files). */
    public function exceedsPostLimit(): bool
    {
        $length = (int)($this->server['CONTENT_LENGTH'] ?? 0);
        $limit = Str::toBytes(ini_get('post_max_size'));
        return empty($this->post) && empty($this->files) && $limit > 0 && $length > $limit
            && stripos((string)($this->server['CONTENT_TYPE'] ?? ''), 'multipart/form-data') !== false;
    }

    public function header(string $name): string
    {
        $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
        return (string)($this->server[$key] ?? '');
    }

    public function isSecure(): bool
    {
        return (!empty($this->server['HTTPS']) && $this->server['HTTPS'] !== 'off')
            || strtolower((string)($this->server['HTTP_X_FORWARDED_PROTO'] ?? '')) === 'https';
    }

    /**
     * Client IP. Behind Cloudflare REMOTE_ADDR is a Cloudflare address, so CF-Connecting-IP is used. That header can be
     * forged when the site is not behind Cloudflare, so the IP is only used for statistics and loose per-IP limits;
     * the main login limit is per account.
     */
    public function ip(): string
    {
        $cf = (string)($this->server['HTTP_CF_CONNECTING_IP'] ?? '');
        if ($cf !== '' && filter_var($cf, FILTER_VALIDATE_IP)) {
            return $cf;
        }
        $ip = (string)($this->server['REMOTE_ADDR'] ?? '');
        return filter_var($ip, FILTER_VALIDATE_IP) ? $ip : '0.0.0.0';
    }

    /** Short browser/device description from the User-Agent (login log). */
    public function device(): string
    {
        $ua = (string)($this->server['HTTP_USER_AGENT'] ?? '');
        $os = '';
        foreach (['Android' => '/Android/i', 'iOS' => '/iPhone|iPad|iOS/i', 'Windows' => '/Windows/i', 'macOS' => '/Mac OS X|Macintosh/i',
                  'Linux' => '/Linux/i'] as $name => $pattern) {
            if (preg_match($pattern, $ua)) {
                $os = $name;
                break;
            }
        }
        $browser = '';
        foreach (['Edge' => '/Edg\//', 'Opera' => '/OPR\//', 'Samsung Internet' => '/SamsungBrowser/', 'Chrome' => '/Chrome\//',
                  'Firefox' => '/Firefox\//', 'Safari' => '/Safari\//'] as $name => $pattern) {
            if (preg_match($pattern, $ua)) {
                $browser = $name;
                break;
            }
        }
        $text = trim($browser . ($browser !== '' && $os !== '' ? ' · ' : '') . $os);
        return $text !== '' ? $text : substr((string)preg_replace('/[^\x20-\x7E]/', '', $ua), 0, 60);
    }

    public function user(): ?array
    {
        return $this->user;
    }

    public function setUser(?array $user): void
    {
        $this->user = $user;
    }
}

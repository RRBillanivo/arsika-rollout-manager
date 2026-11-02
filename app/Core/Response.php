<?php

namespace App\Core;

/**
 * HTTP response. JSON documents use one envelope:
 *   success: {"data": ..., "meta": {...}}
 *   error:   {"error": {"code": "...", "message": "...", "fields": {...}}}
 */
class Response
{
    const JSON_FLAGS = JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION | JSON_INVALID_UTF8_SUBSTITUTE;

    /** @var int */
    private $status;
    /** @var array<string, string> */
    private $headers;
    /** @var string */
    private $content;
    /** @var callable|null writes the body itself (streams, files) */
    private $writer = null;

    public function __construct(string $content = '', int $status = 200, array $headers = [])
    {
        $this->content = $content;
        $this->status = $status;
        $this->headers = $headers + ['Cache-Control' => 'no-store'];
    }

    public static function json($data, int $status = 200, array $meta = []): self
    {
        $body = ['data' => $data];
        if ($meta) {
            $body['meta'] = $meta;
        }
        return new self((string)json_encode($body, self::JSON_FLAGS), $status, ['Content-Type' => 'application/json; charset=utf-8']);
    }

    public static function error(int $status, string $code, string $message, array $fields = [], string $details = ''): self
    {
        $error = ['code' => $code, 'message' => $message];
        if ($fields) {
            $error['fields'] = $fields;
        }
        if ($details !== '') {
            $error['details'] = $details;
        }
        return new self((string)json_encode(['error' => $error], self::JSON_FLAGS), $status,
            ['Content-Type' => 'application/json; charset=utf-8']);
    }

    public static function noContent(): self
    {
        return new self('', 204);
    }

    /** A private file (photo, avatar) the browser may cache for $maxAge seconds. */
    public static function file(string $path, string $mime, int $maxAge = 0): self
    {
        $response = new self('', 200, ['Content-Type' => $mime, 'Content-Length' => (string)filesize($path),
            'Cache-Control' => $maxAge > 0 ? 'private, max-age=' . $maxAge : 'no-store']);
        $response->writer = static function () use ($path): void {
            readfile($path);
        };
        return $response;
    }

    /** A download whose body is written by $writer (e.g. CSV export). */
    public static function download(callable $writer, string $filename, string $mime): self
    {
        $response = new self('', 200, ['Content-Type' => $mime,
            'Content-Disposition' => 'attachment; filename="' . str_replace('"', '', $filename) . '"']);
        $response->writer = $writer;
        return $response;
    }

    public static function html(string $html, int $status = 200): self
    {
        return new self($html, $status, ['Content-Type' => 'text/html; charset=utf-8']);
    }

    public static function redirect(string $location, int $status = 303): self
    {
        return new self('', $status, ['Location' => $location]);
    }

    public function withHeader(string $name, string $value): self
    {
        $this->headers[$name] = $value;
        return $this;
    }

    public function getStatus(): int
    {
        return $this->status;
    }

    public function getContent(): string
    {
        return $this->content;
    }

    public function getHeader(string $name): ?string
    {
        return $this->headers[$name] ?? null;
    }

    public function send(): void
    {
        if (!headers_sent()) {
            http_response_code($this->status);
            header_remove('X-Powered-By');
            $defaults = ['X-Content-Type-Options' => 'nosniff', 'Referrer-Policy' => 'same-origin', 'X-Frame-Options' => 'DENY'];
            foreach ($this->headers + $defaults as $name => $value) {
                header($name . ': ' . $value);
            }
        }
        if ($this->writer !== null) {
            ($this->writer)();
        } else {
            echo $this->content;
        }
    }
}

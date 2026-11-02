<?php

namespace App\Core;

/** One registered route. */
final class Route
{
    /** @var string */
    public $method;
    /** @var string */
    public $path;
    /** @var array{0: class-string, 1: string} */
    public $action;
    /** @var string[] middleware aliases */
    public $middleware;
    /** @var string traffic channel counted for the admin dashboard: web, model or admin */
    public $traffic;

    public function __construct(string $method, string $path, array $action, array $middleware, string $traffic)
    {
        $this->method = $method;
        $this->path = $path;
        $this->action = $action;
        $this->middleware = $middleware;
        $this->traffic = $traffic;
    }

    public function middleware(string ...$aliases): self
    {
        $this->middleware = array_merge($this->middleware, $aliases);
        return $this;
    }

    public function traffic(string $channel): self
    {
        $this->traffic = $channel;
        return $this;
    }

    /** Parameters by name when $path matches, otherwise null. */
    public function match(string $path, array $patterns): ?array
    {
        $regex = preg_replace_callback('/\\\\\{([a-z_]+)\\\\\}/', static function ($m) use ($patterns) {
            return '(?P<' . $m[1] . '>' . ($patterns[$m[1]] ?? '[^/]+') . ')';
        }, preg_quote($this->path, '#'));
        if (!preg_match('#^' . $regex . '$#', $path, $m)) {
            return null;
        }
        return array_filter($m, 'is_string', ARRAY_FILTER_USE_KEY);
    }
}

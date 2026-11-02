<?php

namespace App\Core;

/**
 * Minimal router: HTTP method + path pattern -> [Controller::class, 'method'].
 *   $router->get('/sites/{site}', [SiteController::class, 'show']);
 *   $router->group(['prefix' => '/api/v1', 'middleware' => ['auth']], function ($router) {...});
 * Parameters match one path segment unless a pattern is registered for their name with pattern().
 */
final class Router
{
    /** @var Route[] */
    private $routes = [];
    /** @var array[] */
    private $groups = [];
    /** @var array<string, string> parameter name => regular expression */
    private $patterns = [];

    public function pattern(string $name, string $regex): void
    {
        $this->patterns[$name] = $regex;
    }

    public function group(array $attributes, callable $routes): void
    {
        $this->groups[] = $attributes;
        $routes($this);
        array_pop($this->groups);
    }

    public function get(string $uri, array $action): Route
    {
        return $this->add('GET', $uri, $action);
    }

    public function post(string $uri, array $action): Route
    {
        return $this->add('POST', $uri, $action);
    }

    public function put(string $uri, array $action): Route
    {
        return $this->add('PUT', $uri, $action);
    }

    public function patch(string $uri, array $action): Route
    {
        return $this->add('PATCH', $uri, $action);
    }

    public function delete(string $uri, array $action): Route
    {
        return $this->add('DELETE', $uri, $action);
    }

    private function add(string $method, string $uri, array $action): Route
    {
        $prefix = '';
        $middleware = [];
        $traffic = 'web';
        foreach ($this->groups as $group) {
            $prefix .= '/' . trim((string)($group['prefix'] ?? ''), '/');
            $middleware = array_merge($middleware, (array)($group['middleware'] ?? []));
            $traffic = (string)($group['traffic'] ?? $traffic);
        }
        $path = '/' . trim((string)preg_replace('#/+#', '/', $prefix . '/' . $uri), '/');
        return $this->routes[] = new Route($method, $path, $action, $middleware, $traffic);
    }

    /**
     * Find the route for a request. -> [Route, parameters]
     * @throws HttpException 404 when no route matches the path, 405 when the path exists for other methods
     */
    public function match(string $method, string $path): array
    {
        $path = '/' . trim($path, '/');
        $allowed = [];
        foreach ($this->routes as $route) {
            $params = $route->match($path, $this->patterns);
            if ($params === null) {
                continue;
            }
            if ($route->method === $method || ($method === 'HEAD' && $route->method === 'GET')) {
                return [$route, $params];
            }
            $allowed[] = $route->method;
        }
        if ($allowed) {
            throw new HttpException(405, 'method_not_allowed', trans('errors.method_not_allowed'), [], '',
                ['Allow' => implode(', ', array_unique($allowed))]);
        }
        throw HttpException::notFound(trans('errors.route_not_found', ['route' => $method . ' ' . $path]), 'route_not_found');
    }

    /** @return Route[] */
    public function routes(): array
    {
        return $this->routes;
    }
}

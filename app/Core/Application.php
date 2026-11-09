<?php

namespace App\Core;

use App\Http\Middleware\Authenticate;
use App\Http\Middleware\VerifyCsrfToken;
use App\Services\Crypto;
use App\Services\SessionManager;
use App\Support\Lang;
use App\Support\Log;
use LogicException;
use ReflectionClass;
use ReflectionNamedType;
use Throwable;

/**
 * The application: paths, configuration, a small service container (constructor autowiring) and the HTTP kernel
 * (routing, middleware pipeline and error rendering).
 */
final class Application
{
    const VERSION = '1.6.0';

    /** @var self|null */
    private static $instance = null;
    /** @var string */
    private $basePath;
    /** @var Config */
    private $config;
    /** @var array<string, callable> */
    private $bindings = [];
    /** @var array<string, object> */
    private $instances = [];
    /** @var array<string, class-string> middleware aliases used in routes/api.php */
    private $middleware = [
        'csrf' => VerifyCsrfToken::class,
        'auth' => Authenticate::class,
    ];

    private function __construct(string $basePath)
    {
        $this->basePath = rtrim($basePath, '\\/');
    }

    public static function create(string $basePath): self
    {
        $app = self::$instance = new self($basePath);
        Env::load($app->basePath('.env'));
        $app->config = new Config($app->basePath('config'));
        date_default_timezone_set((string)$app->config->get('app.timezone', 'Asia/Jakarta'));
        ini_set('display_errors', '0');                  // PHP notices must never corrupt a JSON response
        error_reporting(E_ALL);
        $app->bind(Database::class, static function (self $app) {
            return new Database((array)$app->config->get('database'), $app->basePath('database/schema'));
        });
        $app->bind(Crypto::class, static function (self $app) {
            return new Crypto((string)$app->config->get('app.key', ''));
        });
        return $app;
    }

    public static function getInstance(): self
    {
        if (self::$instance === null) {
            throw new LogicException('The application has not been created (require bootstrap/app.php first).');
        }
        return self::$instance;
    }

    public function basePath(string $path = ''): string
    {
        return $this->basePath . ($path !== '' ? DIRECTORY_SEPARATOR . str_replace('/', DIRECTORY_SEPARATOR, $path) : '');
    }

    public function config(): Config
    {
        return $this->config;
    }

    public function version(): string
    {
        return (string)$this->config->get('app.version', self::VERSION);
    }

    // ------------------------------------------------------------------ container

    public function bind(string $id, callable $factory): void
    {
        $this->bindings[$id] = $factory;
        unset($this->instances[$id]);
    }

    public function instance(string $id, $object): void
    {
        $this->instances[$id] = $object;
    }

    /** Shared instance of $id: from a binding, or built by injecting the constructor's class-typed parameters. */
    public function make(string $id)
    {
        if ($id === self::class) {
            return $this;
        }
        if (!isset($this->instances[$id])) {
            $this->instances[$id] = isset($this->bindings[$id]) ? ($this->bindings[$id])($this) : $this->build($id);
        }
        return $this->instances[$id];
    }

    private function build(string $class)
    {
        $reflection = new ReflectionClass($class);
        $constructor = $reflection->getConstructor();
        if ($constructor === null) {
            return new $class();
        }
        $arguments = [];
        foreach ($constructor->getParameters() as $parameter) {
            $type = $parameter->getType();
            if ($type instanceof ReflectionNamedType && !$type->isBuiltin()) {
                $arguments[] = $this->make($type->getName());
            } elseif ($parameter->isDefaultValueAvailable()) {
                $arguments[] = $parameter->getDefaultValue();
            } else {
                throw new LogicException('Cannot resolve $' . $parameter->getName() . ' of ' . $class);
            }
        }
        return $reflection->newInstanceArgs($arguments);
    }

    // ------------------------------------------------------------------ HTTP kernel

    public function router(): Router
    {
        if (!isset($this->instances[Router::class])) {
            $router = new Router();
            (require $this->basePath('routes/api.php'))($router);
            $this->instances[Router::class] = $router;
        }
        return $this->instances[Router::class];
    }

    public function handle(Request $request): Response
    {
        $this->instance(Request::class, $request);
        Lang::setLocale(Lang::detect($request));
        try {
            list($route, $params) = $this->router()->match($request->method(), $request->path());
            return $this->dispatch($route, array_values($params), $request);
        } catch (Throwable $e) {
            return $this->renderException($e);
        }
    }

    /** Run the route's middleware (outermost first) around the controller action. */
    private function dispatch(Route $route, array $params, Request $request): Response
    {
        $action = function (Request $request) use ($route, $params): Response {
            list($class, $method) = $route->action;
            return $this->make($class)->{$method}($request, ...$params);
        };
        $pipeline = array_reduce(array_reverse($route->middleware), function (callable $next, string $alias) {
            if (!isset($this->middleware[$alias])) {
                throw new LogicException('Unknown middleware: ' . $alias);
            }
            $middleware = $this->make($this->middleware[$alias]);
            return static function (Request $request) use ($middleware, $next): Response {
                return $middleware->handle($request, $next);
            };
        }, $action);
        return $pipeline($request);
    }

    public function renderException(Throwable $e): Response
    {
        if ($e instanceof HttpException) {
            $response = Response::error($e->getStatus(), $e->getErrorCode(), $e->getMessage(), $e->getFields(), $e->getDetails());
            foreach ($e->getHeaders() as $name => $value) {
                $response->withHeader($name, $value);
            }
            return $response;
        }
        if ($e instanceof DatabaseException) {
            return Response::error(503, 'database_unavailable', $e->getMessage());
        }
        if ($e instanceof ConfigurationException) {
            return Response::error(503, 'not_configured', $e->getMessage());
        }
        Log::error($e);
        return Response::error(500, 'server_error', trans('errors.server'), [], $this->exceptionDetails($e));
    }

    /** Technical details are only shown to signed-in administrators (or with APP_DEBUG=true). */
    private function exceptionDetails(Throwable $e): string
    {
        $show = (bool)$this->config->get('app.debug', false);
        if (!$show) {
            try {
                $show = $this->make(Database::class)->isConnected() && $this->make(SessionManager::class)->user() !== null;
            } catch (Throwable $ignored) {
                $show = false;
            }
        }
        return $show ? get_class($e) . ': ' . $e->getMessage() . ' (' . basename($e->getFile()) . ':' . $e->getLine() . ')' : '';
    }
}

<?php

namespace App\Services;

use App\Core\HttpException;

/**
 * Client of one runner: the runner of this server (RUNNER_URL and RUNNER_SECRET in .env, normally http://127.0.0.1:8790)
 * or the runner of another server (an https:// address, its secret and the public key pin of its certificate).
 * Every request is signed with the shared secret:
 *   X-Rollout-Signature = hex HMAC-SHA256(secret, METHOD \n PATH_WITH_QUERY \n TIMESTAMP \n NONCE \n hex SHA-256(body))
 * Runner errors are passed on to the browser with their status, code, message and fields.
 */
final class RunnerClient
{
    const CURLE_SSL_PINNEDPUBKEYNOTMATCH = 90;
    const CURLE_PEER_FAILED_VERIFICATION = 60;
    const CURLE_SSL_CONNECT_ERROR = 35;

    /** @var string */
    private $url;
    /** @var string */
    private $secret;
    /** @var string */
    private $pin;
    /** @var int */
    private $timeout;
    /** @var string name of another server ('' for the runner of this server) */
    private $server;

    /** Without arguments: the runner of this server (as the service container builds it). */
    public function __construct(string $url = '', string $secret = '', string $pin = '', int $timeout = 0, string $server = '')
    {
        $this->server = $url === '' ? '' : ($server !== '' ? $server : $url);
        $this->url = rtrim($url !== '' ? $url : (string)config('runner.url', 'http://127.0.0.1:8790'), '/');
        $this->secret = $url !== '' ? $secret : (string)config('runner.secret', '');
        $this->pin = trim($pin);
        $this->timeout = $timeout > 0 ? $timeout : (int)config('runner.timeout', 30);
    }

    /** @return mixed the "data" member of the answer */
    public function get(string $path)
    {
        return $this->request('GET', $path);
    }

    public function post(string $path, array $data = [])
    {
        return $this->request('POST', $path, $data);
    }

    public function put(string $path, array $data = [])
    {
        return $this->request('PUT', $path, $data);
    }

    public function delete(string $path)
    {
        return $this->request('DELETE', $path);
    }

    public function configured(): bool
    {
        return $this->secret !== '';
    }

    /** true when the runner answers /v1/status. */
    public function alive(): bool
    {
        try {
            $this->get('/v1/status');
            return true;
        } catch (HttpException $e) {
            return false;
        }
    }

    public function request(string $method, string $path, ?array $data = null)
    {
        $curl = $this->handle($method, $path, $data);
        $raw = curl_exec($curl);
        $status = (int)curl_getinfo($curl, CURLINFO_HTTP_CODE);
        $errno = curl_errno($curl);
        $error = curl_error($curl);
        curl_close($curl);
        return $this->answer($raw, $status, $errno, $error);
    }

    /**
     * Several requests at the same time (the overview asks every server at once, so a server that is down costs a
     * few seconds once, not once per server).
     * @param array<string, array{0: self, 1: string, 2: string}> $requests key => [client, method, path]
     * @return array<string, mixed> key => data, or the HttpException of that request
     */
    public static function parallel(array $requests): array
    {
        $results = [];
        $handles = [];
        $multi = curl_multi_init();
        foreach ($requests as $key => $request) {
            try {
                $handles[$key] = [$request[0], $request[0]->handle($request[1], $request[2])];
                curl_multi_add_handle($multi, $handles[$key][1]);
            } catch (HttpException $e) {
                $results[$key] = $e;
            }
        }
        $codes = [];
        do {
            $status = curl_multi_exec($multi, $running);
            while (($info = curl_multi_info_read($multi)) !== false) {
                $codes[self::handleId($info['handle'])] = (int)$info['result'];
            }
            if ($running > 0 && curl_multi_select($multi, 1.0) === -1) {
                usleep(20000);
            }
        } while ($running > 0 && $status === CURLM_OK);
        while (($info = curl_multi_info_read($multi)) !== false) {
            $codes[self::handleId($info['handle'])] = (int)$info['result'];
        }
        foreach ($handles as $key => $item) {
            list($client, $curl) = $item;
            $errno = $codes[self::handleId($curl)] ?? 0;
            try {
                $results[$key] = $client->answer((string)curl_multi_getcontent($curl), (int)curl_getinfo($curl, CURLINFO_HTTP_CODE), $errno,
                    $errno ? curl_strerror($errno) : '');
            } catch (HttpException $e) {
                $results[$key] = $e;
            }
            curl_multi_remove_handle($multi, $curl);
            curl_close($curl);
        }
        curl_multi_close($multi);
        return $results;
    }

    /** @param resource|object $handle */
    private static function handleId($handle): int
    {
        return is_object($handle) ? spl_object_id($handle) : (int)$handle;
    }

    /** @return resource|object a prepared curl handle */
    public function handle(string $method, string $path, ?array $data = null)
    {
        if ($this->secret === '') {
            throw new HttpException(503, 'runner_not_configured', $this->server === '' ? trans('runner.not_configured')
                : trans('runner.server_not_configured', ['server' => $this->server]));
        }
        if (!function_exists('curl_init')) {
            throw new HttpException(503, 'runner_unavailable', trans('runner.curl_missing'));
        }
        $body = $data === null || $method === 'GET' ? '' : (string)json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        $target = rtrim((string)parse_url($this->url, PHP_URL_PATH), '/') . $path;
        $timestamp = time();
        $nonce = bin2hex(random_bytes(16));
        $signature = hash_hmac('sha256', $method . "\n" . $target . "\n" . $timestamp . "\n" . $nonce . "\n" . hash('sha256', $body), $this->secret);
        $curl = curl_init($this->url . $path);
        $options = [
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => $this->server === '' ? 3 : 5,
            CURLOPT_TIMEOUT => $this->timeout,
            CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'X-Rollout-Timestamp: ' . $timestamp, 'X-Rollout-Nonce: ' . $nonce,
                                   'X-Rollout-Signature: ' . $signature, 'Expect:'],
            CURLOPT_POSTFIELDS => $body,
            CURLOPT_PROXY => '',
        ];
        if ($this->pin !== '' && stripos($this->url, 'https://') === 0) {
            // install.sh --agent creates a self-signed certificate: its public key is pinned instead of trusting a
            // certificate authority, so only the server that holds that key is accepted (also when its address changes)
            if (!defined('CURLOPT_PINNEDPUBLICKEY')) {
                curl_close($curl);
                throw new HttpException(503, 'runner_unavailable', trans('runner.pin_unsupported'));
            }
            $options[CURLOPT_SSL_VERIFYPEER] = false;
            $options[CURLOPT_SSL_VERIFYHOST] = 0;
            $options[CURLOPT_PINNEDPUBLICKEY] = $this->pin;
        }
        curl_setopt_array($curl, $options);
        return $curl;
    }

    /** @param string|false $raw */
    public function answer($raw, int $status, int $errno, string $error)
    {
        if ($raw === false || $status === 0) {
            $code = $this->server === '' ? 'runner_unavailable' : [self::CURLE_SSL_PINNEDPUBKEYNOTMATCH => 'runner_pin_mismatch',
                self::CURLE_PEER_FAILED_VERIFICATION => 'runner_certificate'][$errno] ?? 'runner_unavailable';
            throw new HttpException(503, $code, $this->unavailable($errno), [], $error);
        }
        $document = json_decode((string)$raw, true);
        if (!is_array($document)) {
            throw new HttpException(502, 'runner_error', trans('runner.bad_answer'), [], substr((string)$raw, 0, 300));
        }
        if ($status >= 400 || isset($document['error'])) {
            $e = (array)($document['error'] ?? []);
            if ($status === 401) {
                throw new HttpException(503, 'runner_rejected', $this->server === '' ? trans('runner.rejected')
                    : trans('runner.server_rejected', ['server' => $this->server]), [], (string)($e['message'] ?? ''));
            }
            throw new HttpException($status >= 400 ? $status : 502, (string)($e['code'] ?? 'runner_error'), (string)($e['message'] ?? trans('runner.bad_answer')),
                (array)($e['fields'] ?? []));
        }
        return $document['data'] ?? null;
    }

    private function unavailable(int $errno): string
    {
        if ($this->server === '') {
            return trans('runner.unavailable');
        }
        if ($errno === self::CURLE_SSL_PINNEDPUBKEYNOTMATCH) {
            return trans('runner.server_pin_mismatch', ['server' => $this->server]);
        }
        if ($errno === self::CURLE_PEER_FAILED_VERIFICATION) {
            return trans('runner.server_certificate', ['server' => $this->server]);
        }
        return trans('runner.server_unavailable', ['server' => $this->server]);
    }
}

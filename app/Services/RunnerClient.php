<?php

namespace App\Services;

use App\Core\HttpException;

/**
 * Client of the runner service (runner/arsika_rollout_manager on 127.0.0.1).
 * Every request is signed with the shared secret:
 *   X-Rollout-Signature = hex HMAC-SHA256(secret, METHOD \n PATH_WITH_QUERY \n TIMESTAMP \n NONCE \n hex SHA-256(body))
 * Runner errors are passed on to the browser with their status, code, message and fields.
 */
final class RunnerClient
{
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
        $secret = (string)config('runner.secret', '');
        if ($secret === '') {
            throw new HttpException(503, 'runner_not_configured', trans('runner.not_configured'));
        }
        if (!function_exists('curl_init')) {
            throw new HttpException(503, 'runner_unavailable', trans('runner.curl_missing'));
        }
        $body = $data === null || $method === 'GET' ? '' : (string)json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        $timestamp = time();
        $nonce = bin2hex(random_bytes(16));
        $signature = hash_hmac('sha256', $method . "\n" . $path . "\n" . $timestamp . "\n" . $nonce . "\n" . hash('sha256', $body), $secret);
        $curl = curl_init((string)config('runner.url') . $path);
        curl_setopt_array($curl, [
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => 3,
            CURLOPT_TIMEOUT => (int)config('runner.timeout', 30),
            CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'X-Rollout-Timestamp: ' . $timestamp, 'X-Rollout-Nonce: ' . $nonce,
                                   'X-Rollout-Signature: ' . $signature, 'Expect:'],
            CURLOPT_POSTFIELDS => $body,
            CURLOPT_PROXY => '',
        ]);
        $raw = curl_exec($curl);
        $status = (int)curl_getinfo($curl, CURLINFO_HTTP_CODE);
        $error = curl_error($curl);
        curl_close($curl);
        if ($raw === false || $status === 0) {
            throw new HttpException(503, 'runner_unavailable', trans('runner.unavailable'), [], $error);
        }
        $document = json_decode((string)$raw, true);
        if (!is_array($document)) {
            throw new HttpException(502, 'runner_error', trans('runner.bad_answer'), [], substr((string)$raw, 0, 300));
        }
        if ($status >= 400 || isset($document['error'])) {
            $e = (array)($document['error'] ?? []);
            if ($status === 401) {
                throw new HttpException(503, 'runner_rejected', trans('runner.rejected'), [], (string)($e['message'] ?? ''));
            }
            throw new HttpException($status >= 400 ? $status : 502, (string)($e['code'] ?? 'runner_error'), (string)($e['message'] ?? trans('runner.bad_answer')),
                (array)($e['fields'] ?? []));
        }
        return $document['data'] ?? null;
    }
}

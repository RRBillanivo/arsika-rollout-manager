<?php

namespace App\Core;

use RuntimeException;

/**
 * An error that is answered with a JSON error document:
 *   {"error": {"code": "...", "message": "...", "fields": {...}, "details": "..."}}
 * The message is shown to the user as is (Indonesian); the code is stable for the client.
 */
class HttpException extends RuntimeException
{
    /** @var int */
    private $status;
    /** @var string */
    private $errorCode;
    /** @var array<string, string> */
    private $fields;
    /** @var string */
    private $details;
    /** @var array<string, string> */
    private $headers;

    public function __construct(int $status, string $errorCode, string $message, array $fields = [], string $details = '',
                                array $headers = [])
    {
        parent::__construct($message, $status);
        $this->status = $status;
        $this->errorCode = $errorCode;
        $this->fields = $fields;
        $this->details = $details;
        $this->headers = $headers;
    }

    public static function badRequest(string $message, string $code = 'bad_request'): self
    {
        return new self(400, $code, $message);
    }

    public static function unauthorized(string $message): self
    {
        return new self(401, 'unauthenticated', $message);
    }

    public static function forbidden(string $message, string $code = 'forbidden'): self
    {
        return new self(403, $code, $message);
    }

    public static function notFound(string $message, string $code = 'not_found'): self
    {
        return new self(404, $code, $message);
    }

    public static function conflict(string $message, string $code = 'conflict'): self
    {
        return new self(409, $code, $message);
    }

    public static function payloadTooLarge(string $message): self
    {
        return new self(413, 'payload_too_large', $message);
    }

    /** @param array<string, string> $fields field name => message */
    public static function validation(array $fields, ?string $message = null): self
    {
        return new self(422, 'validation_failed', $message ?? trans('validation.failed'), $fields);
    }

    public static function tooManyRequests(string $message): self
    {
        return new self(429, 'too_many_requests', $message);
    }

    public static function badGateway(string $message, string $details = ''): self
    {
        return new self(502, 'bad_gateway', $message, [], $details);
    }

    public function getStatus(): int
    {
        return $this->status;
    }

    public function getErrorCode(): string
    {
        return $this->errorCode;
    }

    /** @return array<string, string> */
    public function getFields(): array
    {
        return $this->fields;
    }

    public function getDetails(): string
    {
        return $this->details;
    }

    /** @return array<string, string> */
    public function getHeaders(): array
    {
        return $this->headers;
    }
}

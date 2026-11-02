<?php

namespace App\Services;

use App\Core\ConfigurationException;
use RuntimeException;

/**
 * Cryptography.
 *   - Passwords: Argon2id (PHP; fallbacks: libsodium Argon2id, then bcrypt) over an HMAC-SHA256 "pepper" keyed with the
 *     server key, so a leaked database alone cannot be brute-forced. No MD5/SHA-1 password hashing anywhere.
 *   - Data encryption (available for sensitive fields): AES-256-GCM (authenticated encryption) with a random nonce per value.
 * Every key is derived (HKDF-SHA256) from the 32-byte master key APP_KEY in .env. Never change or lose APP_KEY:
 * encrypted data and all passwords would become unusable.
 */
final class Crypto
{
    const ARGON2 = ['memory_cost' => 65536, 'time_cost' => 3, 'threads' => 1];   // 64 MB, 3 passes

    /** @var string */
    private $appKey;
    /** @var string|null */
    private $master = null;
    /** @var array<string, string> */
    private $derived = [];
    /** @var string|null */
    private $dummyHash = null;

    public function __construct(string $appKey)
    {
        $this->appKey = $appKey;
    }

    /** New random APP_KEY value ("base64:..."). */
    public static function generateKey(): string
    {
        return 'base64:' . base64_encode(random_bytes(32));
    }

    private function master(): string
    {
        if ($this->master === null) {
            if ($this->appKey === '') {
                throw new ConfigurationException(trans('errors.app_key_missing'));
            }
            $raw = strncmp($this->appKey, 'base64:', 7) === 0 ? base64_decode(substr($this->appKey, 7), true) : $this->appKey;
            if (!is_string($raw) || strlen($raw) < 32) {
                throw new ConfigurationException(trans('errors.app_key_invalid'));
            }
            $this->master = substr($raw, 0, 32);
        }
        return $this->master;
    }

    private function key(string $purpose): string
    {
        if (!isset($this->derived[$purpose])) {
            $this->derived[$purpose] = hash_hkdf('sha256', $this->master(), 32, $purpose);
        }
        return $this->derived[$purpose];
    }

    // ------------------------------------------------------------------ data encryption

    /** Text -> "v1:<base64 nonce|tag|ciphertext>" (AES-256-GCM). null and '' stay null. */
    public function encrypt(?string $plaintext): ?string
    {
        if ($plaintext === null || $plaintext === '') {
            return null;
        }
        $key = $this->key('rollout/data/v1');
        if (function_exists('openssl_encrypt')) {
            $iv = random_bytes(12);
            $tag = '';
            $ciphertext = openssl_encrypt($plaintext, 'aes-256-gcm', $key, OPENSSL_RAW_DATA, $iv, $tag, 'rollout', 16);
            if ($ciphertext !== false) {
                return 'v1:' . base64_encode($iv . $tag . $ciphertext);
            }
        }
        if (function_exists('sodium_crypto_aead_xchacha20poly1305_ietf_encrypt')) {
            $nonce = random_bytes(24);
            return 's1:' . base64_encode($nonce . sodium_crypto_aead_xchacha20poly1305_ietf_encrypt($plaintext, 'rollout', $nonce, $key));
        }
        throw new RuntimeException('Neither OpenSSL nor Sodium is available for data encryption.');
    }

    /** Reverse of encrypt(); null when empty, damaged or encrypted with another key. */
    public function decrypt(?string $payload): ?string
    {
        if ($payload === null || $payload === '') {
            return null;
        }
        $version = substr($payload, 0, 3);
        $raw = base64_decode(substr($payload, 3), true);
        if ($raw === false) {
            return null;
        }
        $key = $this->key('rollout/data/v1');
        if ($version === 'v1:' && strlen($raw) > 28 && function_exists('openssl_decrypt')) {
            $plaintext = openssl_decrypt(substr($raw, 28), 'aes-256-gcm', $key, OPENSSL_RAW_DATA, substr($raw, 0, 12),
                substr($raw, 12, 16), 'rollout');
            return $plaintext === false ? null : $plaintext;
        }
        if ($version === 's1:' && strlen($raw) > 24 && function_exists('sodium_crypto_aead_xchacha20poly1305_ietf_decrypt')) {
            $plaintext = sodium_crypto_aead_xchacha20poly1305_ietf_decrypt(substr($raw, 24), 'rollout', substr($raw, 0, 24), $key);
            return $plaintext === false ? null : $plaintext;
        }
        return null;
    }

    // ------------------------------------------------------------------ passwords

    private function pepper(string $password): string
    {
        return base64_encode(hash_hmac('sha256', $password, $this->key('rollout/password/v1'), true));
    }

    /** Password hashing scheme used on this server (shown on the admin System page). */
    public function passwordAlgorithm(): string
    {
        if (defined('PASSWORD_ARGON2ID')) {
            return 'Argon2id (64 MB, 3 putaran) + pepper HMAC-SHA256';
        }
        if (function_exists('sodium_crypto_pwhash_str')) {
            return 'Argon2id libsodium + pepper HMAC-SHA256';
        }
        return 'bcrypt (cost 12) + pepper HMAC-SHA256';
    }

    public function hashPassword(string $password): string
    {
        $peppered = $this->pepper($password);
        if (defined('PASSWORD_ARGON2ID')) {
            return password_hash($peppered, PASSWORD_ARGON2ID, self::ARGON2);
        }
        if (function_exists('sodium_crypto_pwhash_str')) {
            return sodium_crypto_pwhash_str($peppered, SODIUM_CRYPTO_PWHASH_OPSLIMIT_INTERACTIVE, SODIUM_CRYPTO_PWHASH_MEMLIMIT_INTERACTIVE);
        }
        return password_hash($peppered, PASSWORD_BCRYPT, ['cost' => 12]);
    }

    public function verifyPassword(string $password, string $hash): bool
    {
        if ($hash === '') {
            return false;
        }
        $peppered = $this->pepper($password);
        if (password_verify($peppered, $hash)) {
            return true;
        }
        // libsodium Argon2id hash on a server whose password_hash() lacks Argon2 support
        if (strncmp($hash, '$argon2id$', 10) === 0 && !defined('PASSWORD_ARGON2ID') && function_exists('sodium_crypto_pwhash_str_verify')) {
            return sodium_crypto_pwhash_str_verify($hash, $peppered);
        }
        return false;
    }

    /** true when $hash uses a weaker algorithm or parameters than this server would use now. */
    public function needsRehash(string $hash): bool
    {
        if (defined('PASSWORD_ARGON2ID')) {
            return password_needs_rehash($hash, PASSWORD_ARGON2ID, self::ARGON2);
        }
        if (function_exists('sodium_crypto_pwhash_str')) {
            return strncmp($hash, '$argon2id$', 10) !== 0;
        }
        return password_needs_rehash($hash, PASSWORD_BCRYPT, ['cost' => 12]);
    }

    /** Spend the same time as a real check when the account does not exist (no account enumeration by timing). */
    public function equalizeTiming(string $password): void
    {
        if ($this->dummyHash === null) {
            $this->dummyHash = $this->hashPassword(bin2hex(random_bytes(8)));
        }
        $this->verifyPassword($password, $this->dummyHash);
    }

    // ------------------------------------------------------------------ fingerprints

    /** Keyed IP fingerprint for unique-visitor counts (the IP itself is not stored). */
    public function ipFingerprint(string $ip): string
    {
        return substr(hash_hmac('sha256', $ip, $this->key('rollout/ip/v1')), 0, 16);
    }
}

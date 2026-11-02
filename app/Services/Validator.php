<?php

namespace App\Services;

use App\Support\Str;

/** Password rules. Every rule returns [clean value, null] or [null, error message]. */
final class Validator
{
    /** Passwords that are too common (rejected even when long enough). */
    const COMMON_PASSWORDS = ['password1234', 'password12345', 'passw0rd1234', '123456789012', 'qwerty123456', 'qwertyuiop12',
        'admin1234567', 'administrator1', 'iloveyou1234', 'bismillah123', 'indonesia123', 'katasandi123', 'rahasia12345'];

    public function password($value, string $username = ''): array
    {
        $password = (string)$value;
        $min = max(8, (int)config('auth.password_min_length', 12));
        if (Str::length($password) < $min) {
            return [null, trans('validation.password_min', ['min' => $min])];
        }
        if (Str::length($password) > 128) {
            return [null, trans('validation.password_max')];
        }
        if (!preg_match('/\p{L}/u', $password) || !preg_match('/\d/', $password)) {
            return [null, trans('validation.password_mixed')];
        }
        if (in_array(strtolower($password), self::COMMON_PASSWORDS, true)) {
            return [null, trans('validation.password_common')];
        }
        if ($username !== '' && stripos($password, $username) !== false && Str::length($password) < $min + Str::length($username)) {
            return [null, trans('validation.password_contains_username')];
        }
        return [$password, null];
    }
}

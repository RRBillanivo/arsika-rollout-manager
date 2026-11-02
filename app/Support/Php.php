<?php

namespace App\Support;

/** Information about the PHP runtime. */
final class Php
{
    /** true when a function exists and is not listed in disable_functions (aaPanel disables several). */
    public static function functionEnabled(string $name): bool
    {
        if (!function_exists($name)) {
            return false;
        }
        $disabled = array_map('trim', explode(',', (string)ini_get('disable_functions')));
        return !in_array($name, $disabled, true);
    }

    public static function uploadErrorMessage(int $code): string
    {
        switch ($code) {
            case UPLOAD_ERR_INI_SIZE:
                return trans('upload.ini_size', ['limit' => (string)ini_get('upload_max_filesize')]);
            case UPLOAD_ERR_FORM_SIZE:
                return trans('upload.form_size');
            case UPLOAD_ERR_PARTIAL:
                return trans('upload.partial');
            case UPLOAD_ERR_NO_TMP_DIR:
                return trans('upload.no_tmp_dir');
            case UPLOAD_ERR_CANT_WRITE:
                return trans('upload.cant_write');
        }
        return trans('upload.failed', ['code' => $code]);
    }

    /** Move an uploaded file (or copy a local file in tests/CLI). */
    public static function moveUploadedFile(string $tmp, string $target): bool
    {
        return is_uploaded_file($tmp) ? move_uploaded_file($tmp, $target) : @copy($tmp, $target);
    }
}

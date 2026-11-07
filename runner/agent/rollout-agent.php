<?php
/*
 * Arsika Rollout Manager: hosting agent (https://github.com/RRBillanivo/arsika-rollout-manager, MIT license).
 *
 * The dashboard generates this file for one site. Put it into the folder of the website (a file manager, FTP: the
 * hosting, or a virtual machine where you may only drop files), open it in the browser and click "Connect". From then
 * on the runner of the dashboard installs the site's versions here without a terminal: it uploads the release in
 * pieces, this file unpacks it next to the live files, runs the database changes of the release (Laravel: php artisan
 * migrate, inside PHP), swaps the files and keeps the previous version for a rollback.
 *
 *   <website folder>/                  live files of the current version + this file
 *   ../.rollout-agent-<site>/          (or .rollout-agent-<site>-<random>/ inside the website folder when the parent
 *       state.php                      cannot be used) releases, uploads, backups in progress; state.php holds the secret
 *       releases/<version>/            a release that is not live (the previous one, for a rollback); Laravel: the
 *                                      application of every release, only its public/ goes into the website folder
 *       releases/_pre_rollout/         the files that were live before the first installation
 *       shared/                        Laravel: .env and storage/ of the site
 *
 * Every request except the page shown in the browser must be signed with the site's secret (HMAC-SHA256 over method,
 * path, timestamp, a one-time nonce and the body). The key written into this file only works once, to connect.
 * Delete this file to disconnect the site.
 */

const RA_VERSION = '1.5.3';
const RA_SITE = '__RA_SITE__';
const RA_DASHBOARD = '__RA_DASHBOARD__';
const RA_PAIRING_KEY = '__RA_PAIRING_KEY__';
const RA_WINDOW = 300;                  // seconds a signed request stays valid (hosting clocks drift a little)
const RA_BUDGET = 20;                   // seconds of work per request; longer work continues in the next request
const RA_PRE = '_pre_rollout';

error_reporting(E_ALL);
if (function_exists('ini_set')) {                       // hostings disable functions (aaPanel: putenv, symlink, exec ...)
    ini_set('display_errors', '0');
}
if (function_exists('set_time_limit')) {
    @set_time_limit(120);
}
$GLOBALS['ra_start'] = microtime(true);

final class RaError extends Exception
{
    public $status;
    public $name;

    public function __construct(int $status, string $name, string $message)
    {
        parent::__construct($message);
        $this->status = $status;
        $this->name = $name;
    }
}

final class RaRaw
{
    public $bytes;

    public function __construct(string $bytes)
    {
        $this->bytes = $bytes;
    }
}

// ------------------------------------------------------------------ basics

function ra_is_template(): bool
{
    return strncmp(RA_PAIRING_KEY, '__RA_', 5) === 0;
}

function ra_left(): bool
{
    $max = (int)ini_get('max_execution_time');
    $budget = $max > 0 ? min(RA_BUDGET, max(5, (int)($max * 0.6))) : RA_BUDGET;
    return microtime(true) < $GLOBALS['ra_start'] + $budget;
}

function ra_site(): string
{
    return __DIR__;
}

function ra_self(): string
{
    return basename(__FILE__);
}

function ra_exists(string $path): bool
{
    return file_exists($path) || is_link($path);
}

/**
 * The data folder: above the website folder when it can be used there; otherwise inside the website folder with a
 * random name (nginx does not read .htaccess, so the name must not be guessable) and an .htaccess that denies access.
 */
function ra_data(): string
{
    static $dir = null;
    if ($dir !== null) {
        return $dir;
    }
    $name = '.rollout-agent-' . RA_SITE;
    $outside = dirname(__DIR__) . '/' . $name;
    $hidden = glob(__DIR__ . '/' . $name . '-*', GLOB_ONLYDIR) ?: [];
    if ($hidden) {
        return $dir = $hidden[0];
    }
    $random = __DIR__ . '/' . $name . '-' . bin2hex(random_bytes(8));
    if (@is_dir(__DIR__ . '/' . $name)) {                          // agent 1.4.0 used a name that can be guessed
        return $dir = @rename(__DIR__ . '/' . $name, $random) ? $random : __DIR__ . '/' . $name;
    }
    if (@is_dir($outside) || ra_outside_usable($outside)) {
        return $dir = $outside;
    }
    if (!@mkdir($random, 0711, true) && !is_dir($random)) {
        throw new RaError(500, 'data_dir', 'cannot create a data folder in ' . __DIR__);
    }
    $deny = "<IfModule mod_authz_core.c>\n    Require all denied\n</IfModule>\n<IfModule !mod_authz_core.c>\n    Order allow,deny\n    Deny from all\n</IfModule>\n";
    @file_put_contents($random . '/.htaccess', $deny);
    @file_put_contents($random . '/index.html', '');
    return $dir = $random;
}

/** The folder above the website is used when it is writable and on the same file system (renames must work). */
function ra_outside_usable(string $outside): bool
{
    if (!@is_writable(dirname($outside)) || !@mkdir($outside, 0711)) {
        return false;
    }
    $probe = $outside . '/probe-' . bin2hex(random_bytes(4));
    $back = __DIR__ . '/.rollout-probe-' . bin2hex(random_bytes(4));
    $ok = @file_put_contents($probe, 'x') !== false && @rename($probe, $back) && @rename($back, $probe);
    @unlink($probe);
    @unlink($back);
    if (!$ok) {
        @rmdir($outside);
    }
    return $ok;
}

function ra_dir(string $sub): string
{
    $dir = ra_data() . '/' . $sub;
    if (!is_dir($dir) && !@mkdir($dir, $sub === 'shared' ? 0711 : 0700, true) && !is_dir($dir)) {
        throw new RaError(500, 'data_dir', 'cannot create ' . $dir);
    }
    return $dir;
}

/** A path of the site: "@shared/..." is in the data folder (Laravel: .env, storage/), anything else in the website folder. */
function ra_path(string $rel): string
{
    $rel = ra_rel($rel);
    if (strncmp($rel, '@shared/', 8) === 0) {
        return ra_dir('shared') . '/' . ra_rel(substr($rel, 8));
    }
    return __DIR__ . '/' . $rel;
}

/** For reading: before a Laravel website is adopted, its .env and storage/ are still in the website folder. */
function ra_existing_path(string $rel): string
{
    $path = ra_path($rel);
    if (!ra_exists($path) && strncmp(ra_rel($rel), '@shared/', 8) === 0 && ra_exists(__DIR__ . '/' . substr(ra_rel($rel), 8))) {
        return __DIR__ . '/' . substr(ra_rel($rel), 8);
    }
    return $path;
}

/** Replace a file in one step (a reader never sees half of it). */
function ra_write_file(string $path, string $content, int $mode = 0644): void
{
    if (!is_dir(dirname($path)) && !@mkdir(dirname($path), 0755, true) && !is_dir(dirname($path))) {
        throw new RaError(500, 'write', 'cannot create ' . dirname($path));
    }
    $tmp = dirname($path) . '/.rollout-tmp-' . bin2hex(random_bytes(4));
    if (@file_put_contents($tmp, $content, LOCK_EX) === false) {
        throw new RaError(500, 'write', 'cannot write ' . $path);
    }
    @chmod($tmp, $mode);
    if (!@rename($tmp, $path)) {
        @unlink($tmp);
        throw new RaError(500, 'write', 'cannot replace ' . $path);
    }
}

function ra_disabled(string $function): bool
{
    return !function_exists($function) || in_array($function, array_map('trim', explode(',', (string)ini_get('disable_functions'))), true);
}

/** An address of this machine or of a local network (a virtual machine without a certificate). */
function ra_local_host(string $host): bool
{
    $host = strtolower(trim($host, '[]'));
    if (filter_var($host, FILTER_VALIDATE_IP)) {
        return !filter_var($host, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE);
    }
    return $host === 'localhost' || strpos($host, '.') === false || (bool)preg_match('/\.(local|lan|internal|home\.arpa|localdomain)$/', $host);
}

function ra_clip(string $text, int $limit = 65536): string
{
    return strlen($text) > $limit ? "…\n" . substr($text, -$limit) : $text;
}

function ra_load(): array
{
    $raw = @file_get_contents(ra_data() . '/state.php');
    if ($raw === false) {
        return [];
    }
    $data = json_decode((string)substr($raw, (int)strpos($raw, "\n") + 1), true);
    return is_array($data) ? $data : [];
}

function ra_save(array $state): void
{
    $file = ra_data() . '/state.php';
    $tmp = $file . '.' . bin2hex(random_bytes(4));
    if (@file_put_contents($tmp, "<?php exit; ?>\n" . json_encode($state, JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT), LOCK_EX) === false) {
        throw new RaError(500, 'state', 'cannot write ' . $file);
    }
    @chmod($tmp, 0600);
    if (!@rename($tmp, $file)) {
        @unlink($tmp);
        throw new RaError(500, 'state', 'cannot replace ' . $file);
    }
}

/** A relative path inside a folder: no leading slash, no "..", no backslash. */
function ra_rel(string $path, string $what = 'path'): string
{
    $path = trim(str_replace('\\', '/', $path));
    $path = trim($path, '/');
    if ($path === '' || $path === '.' || strpos($path, "\0") !== false || preg_match('#(^|/)\.\.?(/|$)#', $path) || preg_match('#^[A-Za-z]:#', $path)) {
        throw new RaError(422, 'bad_path', 'not a relative ' . $what . ': ' . $path);
    }
    return $path;
}

function ra_sub(string $target): string
{
    $target = trim(str_replace('\\', '/', $target), '/');
    return $target === '' || $target === '.' ? '' : ra_rel($target, 'folder');
}

function ra_version(string $version): string
{
    if ($version === RA_PRE || preg_match('/^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$/', $version)) {
        return $version;
    }
    throw new RaError(422, 'bad_version', 'invalid version: ' . $version);
}

function ra_list(string $dir): array
{
    $names = @scandir($dir);
    return $names === false ? [] : array_values(array_diff($names, ['.', '..']));
}

/** Delete a file or folder tree; false when the time of this request ran out (call again). */
function ra_delete(string $path): bool
{
    if (is_link($path) || is_file($path)) {
        @chmod($path, 0644);
        if (!@unlink($path) && ra_exists($path)) {
            throw new RaError(500, 'delete', 'cannot delete ' . $path);
        }
        return true;
    }
    if (!is_dir($path)) {
        return true;
    }
    foreach (ra_list($path) as $name) {
        if (!ra_left()) {
            return false;
        }
        if (!ra_delete($path . '/' . $name)) {
            return false;
        }
    }
    if (!@rmdir($path) && is_dir($path)) {
        throw new RaError(500, 'delete', 'cannot delete ' . $path);
    }
    return true;
}

function ra_bytes(string $value): int
{
    $value = trim($value);
    $unit = strtolower(substr($value, -1));
    $number = (int)$value;
    return $unit === 'g' ? $number << 30 : ($unit === 'm' ? $number << 20 : ($unit === 'k' ? $number << 10 : $number));
}

function ra_url(): string
{
    $https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https')
        || (string)($_SERVER['SERVER_PORT'] ?? '') === '443';
    $path = (string)parse_url((string)($_SERVER['REQUEST_URI'] ?? '/' . ra_self()), PHP_URL_PATH);
    return ($https ? 'https' : 'http') . '://' . ($_SERVER['HTTP_HOST'] ?? 'localhost') . $path;
}

// ------------------------------------------------------------------ requests

function ra_auth(array &$state, string $action, string $body): string
{
    $now = time();
    $failures = array_values(array_filter($state['failures'] ?? [], function ($t) use ($now) { return $t > $now - 600; }));
    if (count($failures) >= 30) {
        throw new RaError(429, 'locked', 'too many invalid requests: try again in 10 minutes');
    }
    $ts = (int)($_SERVER['HTTP_X_ROLLOUT_TIMESTAMP'] ?? 0);
    $nonce = (string)($_SERVER['HTTP_X_ROLLOUT_NONCE'] ?? '');
    $signature = (string)($_SERVER['HTTP_X_ROLLOUT_SIGNATURE'] ?? '');
    $message = "POST\n" . (string)($_SERVER['REQUEST_URI'] ?? '') . "\n" . $ts . "\n" . $nonce . "\n" . hash('sha256', $body);
    $used = null;
    $secret = (string)($state['secret'] ?? '');
    if (abs($now - $ts) <= RA_WINDOW && preg_match('/^[0-9a-f]{16,64}$/', $nonce)) {
        if ($secret !== '' && hash_equals(hash_hmac('sha256', $message, $secret), $signature)) {
            $used = 'secret';
        } elseif (in_array($action, ['hello', 'pair'], true) && ra_pairing_open($state)
            && hash_equals(hash_hmac('sha256', $message, RA_PAIRING_KEY), $signature)) {
            $used = 'pairing';
        }
    }
    if ($used === null) {
        $failures[] = $now;
        $state['failures'] = $failures;
        ra_save($state);
        throw new RaError(401, 'bad_signature', abs($now - $ts) > RA_WINDOW ? 'request expired or clock mismatch' : 'invalid signature');
    }
    $nonces = array_filter($state['nonces'] ?? [], function ($t) use ($now) { return $t > $now - 2 * RA_WINDOW; });
    if (isset($nonces[$nonce])) {
        throw new RaError(401, 'replayed', 'request already used');
    }
    $nonces[$nonce] = $now;
    $state['nonces'] = $nonces;
    $state['failures'] = $failures;
    return $used;
}

/** The key in this file connects once; a new file (new key) may connect again (whoever uploads it controls the hosting). */
function ra_pairing_open(array $state): bool
{
    return !ra_is_template() && (string)($state['pairing_hash'] ?? '') !== hash('sha256', RA_PAIRING_KEY);
}

function ra_send(int $status, array $document): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($document, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
}

function ra_info(array $state): array
{
    $pdo = class_exists('PDO') ? PDO::getAvailableDrivers() : [];
    return [
        'agent' => RA_VERSION, 'site' => RA_SITE, 'php' => PHP_VERSION, 'paired' => (string)($state['secret'] ?? '') !== '',
        'site_dir' => __DIR__, 'file' => ra_self(), 'data_dir' => ra_data(), 'data_outside' => strpos(ra_data(), __DIR__ . '/') !== 0,
        'writable' => is_writable(__DIR__), 'zip' => class_exists('ZipArchive'), 'zlib' => function_exists('gzopen'),
        'pdo' => array_values($pdo), 'max_execution_time' => (int)ini_get('max_execution_time'),
        'memory_limit' => (string)ini_get('memory_limit'), 'post_max_size' => ra_bytes((string)ini_get('post_max_size')),
        'disk_free' => function_exists('disk_free_space') ? @disk_free_space(__DIR__) : null,
        'dashboard' => RA_DASHBOARD, 'web_server' => substr((string)($_SERVER['SERVER_SOFTWARE'] ?? ''), 0, 80),
        'symlink' => !ra_disabled('symlink'),
    ];
}

// ------------------------------------------------------------------ status

function ra_protected(array $extra): array
{
    $names = ['.well-known', 'cgi-bin', '.user.ini', 'error_log', ra_self(), basename(ra_data())];
    foreach ($extra as $name) {
        $name = trim((string)$name, '/');
        if ($name !== '' && strpos($name, '/') === false) {
            $names[] = $name;
        }
    }
    return array_values(array_unique($names));
}

/** Live entries that a new release replaces (not protected, not shared at the top level). */
function ra_live(array $protect, array $shared, ?string $releaseRoot): array
{
    $top = [];
    foreach ($shared as $path) {
        if (strpos($path, '/') === false) {
            $top[] = $path;
        }
    }
    $names = [];
    foreach (ra_list(__DIR__) as $name) {
        if (in_array($name, $protect, true) || in_array($name, $top, true) || strncmp($name, '.rollout-', 9) === 0) {
            continue;
        }
        if (is_dir(__DIR__ . '/' . $name) && (glob(__DIR__ . '/' . $name . '/rollout-agent-*.php') ?: glob(__DIR__ . '/' . $name . '/.rollout-agent-*'))) {
            continue;                       // the folder of another website with its own agent (e.g. a subdomain)
        }
        if ($name === '.htaccess' && ($releaseRoot === null || !is_file($releaseRoot . '/.htaccess'))) {
            continue;                       // kept unless the release brings its own
        }
        $names[] = $name;
    }
    return $names;
}

function ra_releases(array $state): array
{
    $found = [];
    foreach (ra_list(ra_dir('releases')) as $name) {
        if ($name === RA_PRE || substr($name, -4) === '.tmp' || !is_dir(ra_data() . '/releases/' . $name)) {
            continue;
        }
        $info = $state['releases'][$name] ?? [];
        $found[] = ['version' => $name, 'installed_at' => $info['extracted_at'] ?? '', 'activated_at' => $info['activated_at'] ?? null,
                    'sha256' => $info['sha256'] ?? null];
    }
    if (!empty($state['current']) && !in_array($state['current'], array_column($found, 'version'), true)) {
        $info = $state['releases'][$state['current']] ?? [];
        $found[] = ['version' => $state['current'], 'installed_at' => $info['extracted_at'] ?? '', 'activated_at' => $info['activated_at'] ?? null,
                    'sha256' => $info['sha256'] ?? null];
    }
    return $found;
}

function ra_status(array &$state, array $p): array
{
    $shared = ra_rel_list($p['shared'] ?? []);
    return ra_info($state) + [
        'current' => $state['current'] ?? null, 'previous' => $state['previous'] ?? null, 'releases' => ra_releases($state),
        'pre_rollout' => is_dir(ra_data() . '/releases/' . RA_PRE) && ra_list(ra_data() . '/releases/' . RA_PRE) !== [],
        'live' => count(ra_live(ra_protected($p['protect'] ?? []), $shared, null)), 'journal' => !empty($state['journal']),
        'migrations' => $state['migrations'] ?? (object)[],
        'env' => !empty($p['env_file']) && is_file(ra_path((string)$p['env_file'])),
    ];
}

function ra_rel_list($items): array
{
    $list = [];
    foreach (is_array($items) ? $items : [] as $item) {
        $list[] = ra_rel((string)$item);
    }
    return $list;
}

// ------------------------------------------------------------------ connecting

function ra_pair(array &$state, array $p): array
{
    $secret = (string)($p['secret'] ?? '');
    if (!preg_match('/^[0-9a-f]{64}$/', $secret)) {
        throw new RaError(422, 'bad_secret', 'the secret must be 64 hex characters');
    }
    $state['secret'] = $secret;
    $state['pairing_hash'] = hash('sha256', RA_PAIRING_KEY);
    $state['paired_at'] = gmdate('c');
    $state['nonces'] = [];
    return ra_info($state);
}

function ra_forget(array &$state, array $p): array
{
    $state['secret'] = '';
    return ['paired' => false];
}

// ------------------------------------------------------------------ uploads and releases

function ra_upload_name(string $name): string
{
    if (!preg_match('/^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$/', $name)) {
        throw new RaError(422, 'bad_name', 'invalid file name');
    }
    return $name;
}

function ra_upload(array &$state, array $p, string $body): array
{
    $name = ra_upload_name((string)($p['name'] ?? ''));
    $offset = max(0, (int)($p['offset'] ?? 0));
    $file = ra_dir('uploads') . '/' . $name . '.part';
    $handle = @fopen($file, 'c+b');
    if ($handle === false) {
        throw new RaError(500, 'upload', 'cannot write ' . $file);
    }
    $size = (int)fstat($handle)['size'];
    if ($offset > $size) {
        fclose($handle);
        throw new RaError(409, 'offset', 'offset ' . $offset . ' is past the uploaded size ' . $size);
    }
    ftruncate($handle, $offset);
    fseek($handle, $offset);
    fwrite($handle, $body);
    fclose($handle);
    clearstatcache(true, $file);
    return ['size' => filesize($file)];
}

/** The uploaded zip is complete and unchanged; its entries are checked before anything is extracted. */
function ra_finish_upload(array &$state, array $p): array
{
    $name = ra_upload_name((string)($p['name'] ?? ''));
    $part = ra_dir('uploads') . '/' . $name . '.part';
    $file = ra_dir('uploads') . '/' . $name;
    if (!is_file($part) && is_file($file)) {
        $part = $file;
    }
    clearstatcache(true, $part);
    if (!is_file($part) || filesize($part) !== (int)($p['size'] ?? -1) || !hash_equals((string)($p['sha256'] ?? ''), hash_file('sha256', $part))) {
        throw new RaError(422, 'damaged', 'the uploaded file is incomplete or damaged: upload it again');
    }
    if ($part !== $file && !@rename($part, $file)) {
        throw new RaError(500, 'upload', 'cannot rename the upload');
    }
    $entries = 0;
    if (substr($name, -4) === '.zip') {
        $strip = ra_zip_check($file);
        $state['uploads'][$name] = ['sha256' => $p['sha256'], 'strip' => $strip];
        $zip = new ZipArchive();
        $zip->open($file);
        $entries = $zip->numFiles;
        $zip->close();
    }
    return ['entries' => $entries];
}

/** Unsafe entries refuse the archive; a single top folder (GitHub source archives) is stripped. -> prefix */
function ra_zip_check(string $file): string
{
    if (!class_exists('ZipArchive')) {
        throw new RaError(500, 'no_zip', 'the PHP extension zip (ZipArchive) is not enabled on this hosting');
    }
    $zip = new ZipArchive();
    if ($zip->open($file) !== true) {
        throw new RaError(422, 'bad_zip', 'not a zip archive');
    }
    $tops = [];
    $nested = false;
    for ($i = 0; $i < $zip->numFiles; $i++) {
        $name = (string)$zip->getNameIndex($i);
        $clean = str_replace('\\', '/', $name);
        if ($clean === '' || $clean[0] === '/' || preg_match('#(^|/)\.\.(/|$)#', $clean) || preg_match('#^[A-Za-z]:#', $clean)) {
            $zip->close();
            throw new RaError(422, 'unsafe', 'unsafe path in the archive: ' . $name);
        }
        $parts = explode('/', trim($clean, '/'));
        $tops[$parts[0]] = true;
        $nested = $nested || count($parts) > 1;
    }
    $zip->close();
    if (count($tops) === 1 && $nested) {
        $top = array_keys($tops)[0] . '/';
        return $top;
    }
    return '';
}

function ra_extract(array &$state, array $p): array
{
    $name = ra_upload_name((string)($p['name'] ?? ''));
    $version = ra_version((string)($p['version'] ?? ''));
    $file = ra_dir('uploads') . '/' . $name;
    $strip = (string)($state['uploads'][$name]['strip'] ?? '');
    $start = max(0, (int)($p['start'] ?? 0));
    $dest = ra_dir('releases') . '/' . $version . '.tmp';
    if ($start === 0 && ra_exists($dest) && !ra_delete($dest)) {
        return ['next' => 0, 'done' => false];
    }
    $zip = new ZipArchive();
    if (!is_file($file) || $zip->open($file) !== true) {
        throw new RaError(404, 'no_upload', 'the release archive is not on the hosting: upload it again');
    }
    $total = $zip->numFiles;
    $skipped = (int)($state['extract'][$version]['links'] ?? 0);
    for ($i = $start; $i < $total; $i++) {
        if (!ra_left()) {
            $zip->close();
            $state['extract'][$version] = ['links' => $skipped];
            return ['next' => $i, 'total' => $total, 'done' => false];
        }
        $entry = str_replace('\\', '/', (string)$zip->getNameIndex($i));
        if ($strip !== '' && strncmp($entry, $strip, strlen($strip)) === 0) {
            $entry = substr($entry, strlen($strip));
        }
        if (trim($entry, '/') === '') {
            continue;
        }
        $target = $dest . '/' . ra_rel($entry);
        if (substr($entry, -1) === '/') {
            @mkdir($target, 0755, true);
            continue;
        }
        $mode = 0;
        if ($zip->getExternalAttributesIndex($i, $opsys, $attributes) && $opsys === ZipArchive::OPSYS_UNIX) {
            $mode = ($attributes >> 16) & 0xFFFF;
        }
        if (($mode & 0xF000) === 0xA000) {
            $skipped++;                                              // links are not created on a hosting
            continue;
        }
        if (!is_dir(dirname($target))) {
            @mkdir(dirname($target), 0755, true);
        }
        $in = $zip->getStream((string)$zip->getNameIndex($i));
        $out = @fopen($target, 'wb');
        if ($in === false || $out === false) {
            $zip->close();
            throw new RaError(500, 'extract', 'cannot extract ' . $entry);
        }
        stream_copy_to_stream($in, $out);
        fclose($in);
        fclose($out);
        @chmod($target, ($mode & 0777) ? (($mode & 0111) ? 0755 : 0644) : 0644);
    }
    $zip->close();
    $final = ra_data() . '/releases/' . $version;
    if (ra_exists($final) && !ra_delete($final)) {
        return ['next' => $total, 'total' => $total, 'done' => false];
    }
    if (!@rename($dest, $final)) {
        throw new RaError(500, 'extract', 'cannot move the release into place');
    }
    @unlink($file);
    $state['releases'][$version] = ['extracted_at' => gmdate('c'), 'sha256' => $state['uploads'][$name]['sha256'] ?? null];
    unset($state['uploads'][$name], $state['extract'][$version]);
    return ['next' => $total, 'total' => $total, 'done' => true, 'links_skipped' => $skipped];
}

// ------------------------------------------------------------------ switching

/** Panel lines of .htaccess (cPanel's PHP version handler) are kept when a release brings its own .htaccess. */
function ra_panel_lines(string $file): string
{
    $text = (string)@file_get_contents($file);
    return preg_match_all('/^# (?:php -- )?BEGIN cPanel-generated.*?^# (?:php -- )?END cPanel-generated[^\n]*\n?/ms', $text, $m) ? implode('', $m[0]) : '';
}

function ra_activate(array &$state, array $p): array
{
    $version = ra_version((string)($p['version'] ?? ''));
    $sub = ra_sub((string)($p['docroot_target'] ?? '.'));
    $shared = ra_rel_list($p['shared'] ?? []);
    $protect = ra_protected($p['protect'] ?? []);
    $releases = ra_dir('releases');
    $current = $state['current'] ?? null;
    if ($current === $version) {
        throw new RaError(409, 'current', $version . ' is already live');
    }
    $root = $version === RA_PRE ? $releases . '/' . RA_PRE : $releases . '/' . $version . ($sub === '' ? '' : '/' . $sub);
    if (!is_dir($root)) {
        throw new RaError(404, 'no_release', is_dir($releases . '/' . $version) ? 'the folder ' . $sub . ' is not in release ' . $version
            : 'release ' . $version . ' is not on the hosting');
    }
    $live = ra_live($protect, $shared, $root);
    $from = $current !== null ? $current : (count($live) ? RA_PRE : null);
    $fromRoot = $from === null ? null : ($from === RA_PRE ? $releases . '/' . RA_PRE : $releases . '/' . $from . ($sub === '' ? '' : '/' . $sub));
    $moves = [];
    $storage = ra_data() . '/shared/storage';
    if (!empty($p['laravel']) && !is_dir($storage) && is_dir(__DIR__ . '/storage') && !is_link(__DIR__ . '/storage')) {
        $moves[] = ['move', __DIR__ . '/storage', $storage];          // the data of the adopted Laravel website stays with it
    }
    $incoming = ra_list($root);                                     // top-level names that go live
    foreach ($shared as $path) {                                    // data that stays: into the new tree before the swap
        if (ra_exists(__DIR__ . '/' . $path)) {
            if (ra_exists($root . '/' . $path)) {
                $moves[] = ['drop', $root . '/' . $path];
            }
            $moves[] = ['move', __DIR__ . '/' . $path, $root . '/' . $path];
            $top = explode('/', $path)[0];
            if (!in_array($top, $incoming, true)) {
                $incoming[] = $top;
            }
        }
    }
    foreach ($live as $name) {
        if ($fromRoot !== null && ra_exists($fromRoot . '/' . $name)) {
            throw new RaError(409, 'conflict', $fromRoot . '/' . $name . ' exists already');
        }
        $moves[] = ['move', __DIR__ . '/' . $name, $fromRoot . '/' . $name];
    }
    foreach ($incoming as $name) {
        if (in_array($name, $protect, true) || strncmp($name, '.rollout-', 9) === 0) {
            $moves[] = ['drop', $root . '/' . $name];                // never replaces this file, its data or panel files
            continue;
        }
        $moves[] = ['move', $root . '/' . $name, __DIR__ . '/' . $name];
    }
    $state['journal'] = ['from' => $from, 'to' => $version, 'moves' => $moves, 'done' => 0,
                         'panel' => is_file($root . '/.htaccess') ? ra_panel_lines(__DIR__ . '/.htaccess') : ''];
    ra_save($state);
    ra_journal($state);
    return ['current' => $state['current'] ?? null, 'previous' => $from, 'moved' => count($moves)];
}

/** Carry out (or finish, after an interrupted request) the moves of a switch. */
function ra_journal(array &$state): void
{
    $journal = $state['journal'] ?? null;
    if (!is_array($journal)) {
        return;
    }
    $moves = $journal['moves'];
    for ($i = (int)$journal['done']; $i < count($moves); $i++) {
        $move = $moves[$i];
        if ($move[0] === 'drop') {
            if (!ra_delete($move[1])) {
                $state['journal']['done'] = $i;
                ra_save($state);
                throw new RaError(503, 'busy', 'still switching: try again');
            }
        } elseif (ra_exists($move[1])) {
            if (!is_dir(dirname($move[2]))) {
                @mkdir(dirname($move[2]), 0755, true);
            }
            if (!@rename($move[1], $move[2])) {
                throw new RaError(500, 'switch', 'cannot move ' . $move[1]);
            }
        }
        $state['journal']['done'] = $i + 1;
        ra_save($state);
    }
    if ($journal['panel'] !== '' && is_file(__DIR__ . '/.htaccess')) {
        $text = (string)file_get_contents(__DIR__ . '/.htaccess');
        if (strpos($text, trim($journal['panel'])) === false) {
            file_put_contents(__DIR__ . '/.htaccess', $journal['panel'] . $text);
        }
    }
    $to = $journal['to'];
    $state['current'] = $to === RA_PRE ? null : $to;
    $state['previous'] = $journal['from'];
    if ($to !== RA_PRE) {
        $state['releases'][$to]['activated_at'] = gmdate('c');
    }
    unset($state['journal']);
    ra_save($state);
}

function ra_delete_release(array &$state, array $p): array
{
    $version = ra_version((string)($p['version'] ?? ''));
    if ($version === ($state['current'] ?? null)) {
        throw new RaError(409, 'current', 'the live release cannot be deleted');
    }
    $done = ra_delete(ra_dir('releases') . '/' . $version);
    if ($done) {
        unset($state['releases'][$version]);
        if (($state['previous'] ?? null) === $version) {
            $state['previous'] = null;
        }
    }
    return ['done' => $done];
}

// ------------------------------------------------------------------ database

function ra_env(string $file): array
{
    $values = [];
    $text = (string)@file_get_contents($file);
    if (substr($file, -4) === '.php') {                            // wp-config.php and similar: define('DB_NAME', '...')
        preg_match_all('/define\(\s*[\'"](DB_NAME|DB_USER|DB_PASSWORD|DB_HOST)[\'"]\s*,\s*[\'"]((?:[^\'"\\\\]|\\\\.)*)[\'"]\s*\)/', $text, $m, PREG_SET_ORDER);
        $map = ['DB_NAME' => 'DB_DATABASE', 'DB_USER' => 'DB_USERNAME', 'DB_PASSWORD' => 'DB_PASSWORD', 'DB_HOST' => 'DB_HOST'];
        foreach ($m as $match) {
            $values[$map[$match[1]]] = stripslashes($match[2]);
        }
        return $values;
    }
    foreach (preg_split('/\r\n|\r|\n/', $text) as $line) {
        $line = trim($line);
        if ($line === '' || $line[0] === '#' || strpos($line, '=') === false) {
            continue;
        }
        if (strncmp($line, 'export ', 7) === 0) {
            $line = ltrim(substr($line, 7));
        }
        list($key, $value) = explode('=', $line, 2);
        $key = trim($key);
        $value = trim($value);
        if (!preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $key)) {
            continue;
        }
        if (strlen($value) >= 2 && ($value[0] === '"' || $value[0] === "'") && substr($value, -1) === $value[0]) {
            $value = $value[0] === '"' ? str_replace(['\\n', '\\"', '\\\\'], ["\n", '"', '\\'], substr($value, 1, -1)) : substr($value, 1, -1);
        } elseif (($hash = strpos($value, ' #')) !== false) {
            $value = rtrim(substr($value, 0, $hash));
        }
        $values[$key] = $value;
    }
    return $values;
}

function ra_first(array $env, array $keys): string
{
    foreach ($keys as $key) {
        if (isset($env[$key]) && $env[$key] !== '') {
            return (string)$env[$key];
        }
    }
    return '';
}

/** -> [driver mysql|pgsql|sqlite, PDO dsn, user, password, name] or null (no database) */
function ra_db(string $envFile, string $engine): ?array
{
    if ($engine === 'none' || $envFile === '') {
        return null;
    }
    $path = ra_existing_path($envFile);
    if (!is_file($path)) {
        throw new RaError(422, 'no_env', 'the file ' . $envFile . ' with the database settings is not on the hosting');
    }
    $env = ra_env($path);
    $url = (string)($env['DATABASE_URL'] ?? '');
    if (strpos($url, '://') !== false) {
        $u = parse_url($url);
        $scheme = strtolower(explode('+', (string)($u['scheme'] ?? ''))[0]);
        $driver = in_array($scheme, ['mysql', 'mysql2', 'mariadb'], true) ? 'mysql' : (in_array($scheme, ['postgres', 'postgresql', 'pgsql'], true) ? 'pgsql' : ($scheme === 'sqlite' ? 'sqlite' : ''));
        $name = rawurldecode(ltrim((string)($u['path'] ?? ''), '/'));
        $settings = [$driver, (string)($u['host'] ?? 'localhost'), (string)($u['port'] ?? ''), $name, rawurldecode((string)($u['user'] ?? '')), rawurldecode((string)($u['pass'] ?? ''))];
    } else {
        $connection = strtolower((string)($env['DB_CONNECTION'] ?? ''));
        $name = ra_first($env, ['DB_DATABASE', 'DB_NAME', 'MYSQL_DATABASE', 'MARIADB_DATABASE', 'POSTGRES_DB']);
        $driver = in_array($connection, ['mysql', 'mariadb'], true) ? 'mysql' : (in_array($connection, ['pgsql', 'postgres', 'postgresql'], true) ? 'pgsql'
            : ($connection === 'sqlite' ? 'sqlite' : ($connection === '' ? 'mysql' : '')));
        $settings = [$driver, ra_first($env, ['DB_HOST']) ?: 'localhost', ra_first($env, ['DB_PORT']), $name,
                     ra_first($env, ['DB_USERNAME', 'DB_USER', 'MYSQL_USER', 'MARIADB_USER', 'POSTGRES_USER']),
                     ra_first($env, ['DB_PASSWORD', 'DB_PASS', 'MYSQL_PASSWORD', 'MARIADB_PASSWORD', 'POSTGRES_PASSWORD'])];
    }
    list($driver, $host, $port, $name, $user, $password) = $settings;
    if ($engine === 'mysql' || $engine === 'postgres') {
        $driver = $engine === 'mysql' ? 'mysql' : 'pgsql';
    }
    if ($name === '' || $driver === '') {
        if ($engine === 'auto') {
            return null;
        }
        throw new RaError(422, 'no_database', 'no database settings in ' . $envFile);
    }
    if ($driver === 'sqlite') {
        $file = $name[0] === '/' || preg_match('#^[A-Za-z]:[\/]#', $name) ? $name : __DIR__ . '/' . $name;
        return ['sqlite', 'sqlite:' . $file, null, null, $name];
    }
    if ($driver === 'mysql') {
        $socket = '';
        if (strpos($host, ':') !== false) {
            list($host, $rest) = explode(':', $host, 2);
            if (ctype_digit($rest)) {
                $port = $rest;
            } else {
                $socket = $rest;
            }
        }
        $dsn = 'mysql:' . ($socket !== '' ? 'unix_socket=' . $socket : 'host=' . $host . ($port !== '' ? ';port=' . $port : '')) . ';dbname=' . $name . ';charset=utf8mb4';
        return ['mysql', $dsn, $user, $password, $name];
    }
    return ['pgsql', 'pgsql:host=' . $host . ($port !== '' ? ';port=' . $port : '') . ';dbname=' . $name, $user, $password, $name];
}

function ra_pdo(array $db): PDO
{
    if (!in_array($db[0], PDO::getAvailableDrivers(), true)) {
        throw new RaError(500, 'no_driver', 'the PHP extension pdo_' . $db[0] . ' is not enabled on this hosting');
    }
    try {
        return new PDO($db[1], $db[2], $db[3], [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_TIMEOUT => 10]);
    } catch (PDOException $e) {
        throw new RaError(422, 'db_connect', 'the database cannot be reached: ' . substr($e->getMessage(), 0, 200));
    }
}

/** Statements of a migration file: one statement ends with ";" at the end of a line; "--" lines are comments. */
function ra_statements(string $sql): array
{
    $list = [];
    foreach (preg_split('/;\s*(?:\r?\n|$)/', $sql) as $chunk) {
        $chunk = trim((string)preg_replace('/^\s*--.*$/m', '', $chunk));
        if ($chunk !== '') {
            $list[] = $chunk;
        }
    }
    return $list;
}

function ra_migration_files(string $version, string $dir): array
{
    $folder = ra_data() . '/releases/' . ra_version($version) . '/' . ra_rel($dir, 'migrations folder');
    $files = [];
    foreach (ra_list($folder) as $name) {
        if (substr($name, -4) === '.sql' && is_file($folder . '/' . $name)) {
            $files[$name] = $folder . '/' . $name;
        }
    }
    uksort($files, 'strnatcmp');
    return $files;
}

function ra_migrate(array &$state, array $p): array
{
    $mode = (string)($p['mode'] ?? 'auto');
    $files = ra_migration_files((string)($p['version'] ?? ''), (string)($p['dir'] ?? 'database/migrations'));
    $applied = $state['migrations'] ?? [];
    $pending = array_diff_key($files, $applied);
    $done = [];
    $pdo = null;
    foreach ($pending as $name => $path) {
        if (!ra_left()) {
            return ['done' => false, 'applied' => $done];
        }
        $sql = (string)file_get_contents($path);
        if ($mode === 'auto') {
            if ($pdo === null) {
                $db = ra_db((string)($p['env_file'] ?? ''), (string)($p['engine'] ?? 'auto'));
                if ($db === null) {
                    throw new RaError(422, 'no_database', 'the release has database changes, but the site has no database settings (environment file)');
                }
                $pdo = ra_pdo($db);
            }
            $transaction = $pdo->getAttribute(PDO::ATTR_DRIVER_NAME) !== 'mysql';   // MySQL commits every DDL statement anyway
            try {
                if ($transaction) {
                    $pdo->beginTransaction();
                }
                foreach (ra_statements($sql) as $statement) {
                    $pdo->exec($statement);
                }
                if ($transaction) {
                    $pdo->commit();
                }
            } catch (PDOException $e) {
                if ($transaction && $pdo->inTransaction()) {
                    $pdo->rollBack();
                }
                throw new RaError(422, 'migration_failed', $name . ': ' . substr($e->getMessage(), 0, 300));
            }
        }
        $state['migrations'][$name] = ['sha256' => hash('sha256', $sql), 'at' => gmdate('c'), 'mode' => $mode === 'auto' ? 'auto' : 'manual'];
        ra_save($state);
        $done[] = $name;
    }
    return ['done' => true, 'applied' => $done];
}

// ------------------------------------------------------------------ backups (database dump and files, in steps)

function ra_tables(PDO $pdo, string $driver): array
{
    if ($driver === 'sqlite') {
        return $pdo->query("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")->fetchAll(PDO::FETCH_COLUMN);
    }
    return $pdo->query("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")->fetchAll(PDO::FETCH_COLUMN);
}

function ra_quote_name(string $name, string $driver): string
{
    return $driver === 'mysql' ? '`' . str_replace('`', '``', $name) . '`' : '"' . str_replace('"', '""', $name) . '"';
}

/** One statement of a dump: "-- len N" and N bytes, so values with line breaks or semicolons restore safely. */
function ra_dump_write($gz, string $statement): void
{
    gzwrite($gz, '-- len ' . strlen($statement) . "\n" . $statement . "\n");
}

function ra_dump_step(array &$job, array $db): bool
{
    $pdo = ra_pdo($db);
    $driver = $db[0];
    $gz = gzopen($job['dir'] . '/database.sql.gz', 'ab6');
    if (!isset($job['tables'])) {
        $job['tables'] = ra_tables($pdo, $driver);
        $job['t'] = 0;
        $job['offset'] = -1;
        gzwrite($gz, "-- Arsika Rollout Manager hosting agent: dump of " . $db[4] . ' (' . $driver . ', ' . gmdate('c') . ")\n");
        if ($driver === 'mysql') {
            ra_dump_write($gz, 'SET NAMES utf8mb4;');
            ra_dump_write($gz, 'SET FOREIGN_KEY_CHECKS=0;');
        }
    }
    while ($job['t'] < count($job['tables'])) {
        $table = $job['tables'][$job['t']];
        $quoted = ra_quote_name($table, $driver);
        if ($job['offset'] < 0) {
            if ($driver === 'sqlite') {
                $create = (string)$pdo->query("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = " . $pdo->quote($table))->fetchColumn();
            } else {
                $create = (string)$pdo->query('SHOW CREATE TABLE ' . $quoted)->fetch(PDO::FETCH_NUM)[1];
            }
            ra_dump_write($gz, 'DROP TABLE IF EXISTS ' . $quoted . ';');
            ra_dump_write($gz, $create . ';');
            $job['offset'] = 0;
        }
        while (true) {
            if (!ra_left()) {
                gzclose($gz);
                return false;
            }
            $rows = $pdo->query('SELECT * FROM ' . $quoted . ' LIMIT 200 OFFSET ' . (int)$job['offset'])->fetchAll(PDO::FETCH_NUM);
            if (!$rows) {
                break;
            }
            $values = [];
            foreach ($rows as $row) {
                $values[] = '(' . implode(',', array_map(function ($v) use ($pdo) { return $v === null ? 'NULL' : $pdo->quote((string)$v); }, $row)) . ')';
            }
            ra_dump_write($gz, 'INSERT INTO ' . $quoted . ' VALUES ' . implode(',', $values) . ';');
            $job['offset'] += count($rows);
        }
        $job['t']++;
        $job['offset'] = -1;
    }
    if ($driver === 'mysql') {
        ra_dump_write($gz, 'SET FOREIGN_KEY_CHECKS=1;');
    }
    gzclose($gz);
    return true;
}

function ra_files_list(array $paths, array $exclude): array
{
    $files = [];
    foreach ($paths as $path) {
        $full = ra_existing_path($path);
        if (is_file($full)) {
            $files[] = $path;
        } elseif (is_dir($full)) {
            $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($full, FilesystemIterator::SKIP_DOTS));
            foreach ($iterator as $item) {
                if ($item->isFile()) {
                    $files[] = $path . '/' . substr(str_replace('\\', '/', $item->getPathname()), strlen(str_replace('\\', '/', $full)) + 1);
                }
            }
        }
    }
    return array_values(array_filter($files, function ($file) use ($exclude) {
        foreach ($exclude as $e) {
            if ($file === $e || strncmp($file, rtrim($e, '/') . '/', strlen(rtrim($e, '/')) + 1) === 0) {
                return false;
            }
        }
        return true;
    }));
}

function ra_backup(array &$state, array $p): array
{
    $id = (string)($p['id'] ?? '');
    if (!preg_match('/^[0-9A-Za-z][0-9A-Za-z._+-]{0,100}$/', $id)) {
        throw new RaError(422, 'bad_id', 'invalid backup id');
    }
    $job = $state['backup'] ?? null;
    if (!is_array($job) || $job['id'] !== $id) {
        $dir = ra_dir('backups') . '/' . $id . '-' . bin2hex(random_bytes(8));      // not guessable inside a public folder
        @mkdir($dir, 0700, true);
        $db = ra_db((string)($p['env_file'] ?? ''), (string)($p['engine'] ?? 'auto'));
        $paths = array_values(array_filter(ra_rel_list($p['paths'] ?? []), function ($path) { return ra_exists(ra_existing_path($path)); }));
        $job = ['id' => $id, 'dir' => $dir, 'db' => $db !== null && $db[0] !== 'pgsql', 'db_skipped' => $db !== null && $db[0] === 'pgsql',
                'files' => ra_files_list($paths, ra_rel_list($p['exclude'] ?? [])), 'f' => 0, 'paths' => $paths, 'stage' => 'db'];
        $state['backup'] = $job;
        ra_save($state);
    }
    if ($job['stage'] === 'db') {
        if ($job['db']) {
            $finished = ra_dump_step($job, ra_db((string)($p['env_file'] ?? ''), (string)($p['engine'] ?? 'auto')));
            $state['backup'] = $job;
            if (!$finished) {
                return ['done' => false, 'stage' => 'database'];
            }
        }
        $job['stage'] = 'files';
    }
    if ($job['stage'] === 'files' && $job['files']) {
        $zip = new ZipArchive();
        if ($zip->open($job['dir'] . '/files.zip', ZipArchive::CREATE) !== true) {
            throw new RaError(500, 'zip', 'cannot write the files archive');
        }
        while ($job['f'] < count($job['files'])) {
            if (!ra_left()) {
                break;
            }
            $file = $job['files'][$job['f']];
            $zip->addFile(ra_existing_path($file), $file);
            $job['f']++;
        }
        $zip->close();
        $state['backup'] = $job;
        if ($job['f'] < count($job['files'])) {
            return ['done' => false, 'stage' => 'files', 'files' => $job['f'] . '/' . count($job['files'])];
        }
    }
    $parts = [];
    foreach (['database.sql.gz' => 'database', 'files.zip' => 'files'] as $file => $kind) {
        if (is_file($job['dir'] . '/' . $file)) {
            $parts[] = ['kind' => $kind, 'file' => $file, 'path' => substr($job['dir'], strlen(ra_data()) + 1) . '/' . $file,
                        'size' => filesize($job['dir'] . '/' . $file), 'sha256' => hash_file('sha256', $job['dir'] . '/' . $file)];
        }
    }
    unset($state['backup']);
    return ['done' => true, 'parts' => $parts, 'paths' => $job['paths'], 'db_skipped' => $job['db_skipped'],
            'folder' => substr($job['dir'], strlen(ra_data()) + 1)];
}

function ra_download(array &$state, array $p): RaRaw
{
    $path = ra_rel((string)($p['path'] ?? ''));
    if (strncmp($path, 'backups/', 8) !== 0) {
        throw new RaError(403, 'forbidden', 'only backups can be downloaded');
    }
    $file = ra_data() . '/' . $path;
    if (!is_file($file)) {
        throw new RaError(404, 'not_found', 'not found');
    }
    $handle = fopen($file, 'rb');
    fseek($handle, max(0, (int)($p['offset'] ?? 0)));
    $bytes = (string)fread($handle, min(8 << 20, max(1, (int)($p['length'] ?? (4 << 20)))));
    fclose($handle);
    return new RaRaw($bytes);
}

function ra_clear(array &$state, array $p): array
{
    $path = ra_rel((string)($p['path'] ?? ''));
    if (strncmp($path, 'backups/', 8) !== 0 && strncmp($path, 'replaced/', 9) !== 0 && strncmp($path, 'restore/', 8) !== 0) {
        throw new RaError(403, 'forbidden', 'only backups and restore folders can be removed');
    }
    return ['done' => ra_delete(ra_data() . '/' . $path)];
}

function ra_restore_files(array &$state, array $p): array
{
    $name = ra_upload_name((string)($p['name'] ?? ''));
    $file = ra_dir('uploads') . '/' . $name;
    $staging = ra_dir('restore') . '/' . pathinfo($name, PATHINFO_FILENAME);
    $start = max(0, (int)($p['start'] ?? 0));
    $zip = new ZipArchive();
    if (!is_file($file) || $zip->open($file) !== true) {
        throw new RaError(404, 'no_upload', 'the files of the backup are not on the hosting');
    }
    for ($i = $start; $i < $zip->numFiles; $i++) {
        if (!ra_left()) {
            $zip->close();
            return ['done' => false, 'next' => $i];
        }
        $raw = (string)$zip->getNameIndex($i);
        $entry = ra_rel($raw);
        if (substr($raw, -1) === '/') {
            @mkdir($staging . '/' . $entry, 0755, true);
            continue;
        }
        @mkdir(dirname($staging . '/' . $entry), 0755, true);
        $in = $zip->getStream($raw);
        $out = @fopen($staging . '/' . $entry, 'wb');
        if ($in === false || $out === false) {
            $zip->close();
            throw new RaError(500, 'restore', 'cannot extract ' . $entry);
        }
        stream_copy_to_stream($in, $out);
        fclose($in);
        fclose($out);
    }
    $zip->close();
    $replaced = 'replaced/' . gmdate('Ymd-His');
    foreach (ra_rel_list($p['paths'] ?? []) as $path) {
        $live = ra_path($path);
        if (ra_exists($live)) {
            @mkdir(dirname(ra_data() . '/' . $replaced . '/' . $path), 0700, true);
            rename($live, ra_data() . '/' . $replaced . '/' . $path);
        }
        if (ra_exists($staging . '/' . $path)) {
            @mkdir(dirname($live), 0755, true);
            rename($staging . '/' . $path, $live);
        }
    }
    ra_delete($staging);
    @unlink($file);
    return ['done' => true, 'replaced' => $replaced];
}

function ra_restore_db(array &$state, array $p): array
{
    $name = ra_upload_name((string)($p['name'] ?? ''));
    $file = ra_dir('uploads') . '/' . $name;
    $db = ra_db((string)($p['env_file'] ?? ''), (string)($p['engine'] ?? 'auto'));
    if ($db === null) {
        throw new RaError(422, 'no_database', 'the site has no database settings to restore the database into');
    }
    $pdo = ra_pdo($db);
    $gz = gzopen($file, 'rb');
    if ($gz === false) {
        throw new RaError(404, 'no_upload', 'the database dump is not on the hosting');
    }
    $offset = max(0, (int)($p['offset'] ?? 0));
    if ($offset > 0) {
        gzseek($gz, $offset);
    }
    try {
        while (($line = gzgets($gz)) !== false) {
            if (strncmp($line, '-- len ', 7) === 0) {
                $length = (int)substr($line, 7);
                $statement = '';
                while (strlen($statement) < $length && !gzeof($gz)) {
                    $statement .= (string)gzread($gz, $length - strlen($statement));
                }
                gzgets($gz);                                            // the line break after the statement
                $pdo->exec(rtrim($statement, "; \n"));
                if (!ra_left()) {
                    $position = gztell($gz);
                    gzclose($gz);
                    return ['done' => false, 'offset' => $position];
                }
            }
        }
    } catch (PDOException $e) {
        gzclose($gz);
        throw new RaError(422, 'restore_failed', 'database restore failed: ' . substr($e->getMessage(), 0, 300));
    }
    gzclose($gz);
    @unlink($file);
    return ['done' => true];
}

// ------------------------------------------------------------------ the agent itself and the .env of the site

/** The runner replaces this file with the agent of its own version (same site, same name; its key is marked as used). */
function ra_self_update(array &$state, array $p): array
{
    $code = (string)($p['code'] ?? '');
    if (strncmp($code, '<?php', 5) !== 0 || strpos($code, "const RA_SITE = '" . RA_SITE . "';") === false
        || !preg_match("/const RA_PAIRING_KEY = '([0-9a-f]{64})';/", $code, $key) || !preg_match("/const RA_VERSION = '([0-9A-Za-z.+-]{1,32})';/", $code, $version)) {
        throw new RaError(422, 'bad_agent', 'this is not an agent file of the site ' . RA_SITE);
    }
    $tmp = __DIR__ . '/.rollout-update-' . bin2hex(random_bytes(4)) . '.php';
    if (@file_put_contents($tmp, $code, LOCK_EX) === false) {
        throw new RaError(500, 'self_update', 'cannot write the new agent next to ' . ra_self());
    }
    @chmod($tmp, @fileperms(__FILE__) & 0777 ?: 0644);
    if (!@rename($tmp, __FILE__)) {
        // Windows keeps a running script open: write it in place instead (the same size check as an upload)
        $written = @file_put_contents(__FILE__, $code, LOCK_EX);
        @unlink($tmp);
        clearstatcache(true, __FILE__);
        if ($written !== strlen($code) || filesize(__FILE__) !== strlen($code)) {
            throw new RaError(500, 'self_update', 'cannot replace ' . ra_self());
        }
    }
    $state['pairing_hash'] = hash('sha256', $key[1]);               // the key of the new file never connects anyone
    if (function_exists('opcache_invalidate')) {
        @opcache_invalidate(__FILE__, true);
    }
    return ['agent' => $version[1]];
}

function ra_env_read(array &$state, array $p): array
{
    $path = ra_path((string)($p['file'] ?? '.env'));
    if (!is_file($path)) {
        return ['exists' => false, 'content' => ''];
    }
    return ['exists' => true, 'content' => (string)file_get_contents($path, false, null, 0, 262144), 'size' => filesize($path)];
}

/** Write the .env; the previous one is kept in the data folder (the last five). */
function ra_env_write(array &$state, array $p): array
{
    $path = ra_path((string)($p['file'] ?? '.env'));
    $content = (string)($p['content'] ?? '');
    if (strlen($content) > 262144 || strpos($content, "\0") !== false) {
        throw new RaError(422, 'bad_env', 'not a valid .env file');
    }
    if (is_file($path)) {
        $keep = ra_dir('env-previous');
        @copy($path, $keep . '/' . basename($path) . '.' . gmdate('Ymd-His'));
        $old = glob($keep . '/*') ?: [];
        sort($old);
        foreach (array_slice($old, 0, max(0, count($old) - 5)) as $file) {
            @unlink($file);
        }
    }
    ra_write_file($path, $content, 0600);
    return ['written' => true, 'size' => strlen($content)];
}

// ------------------------------------------------------------------ Laravel (only public/ in the website folder)

/** -> [release folder (the application), shared folder (.env), storage folder] */
function ra_laravel_paths(string $version): array
{
    $release = ra_data() . '/releases/' . ra_version($version);
    $shared = ra_dir('shared');
    if (is_dir($shared . '/storage')) {
        $storage = $shared . '/storage';
    } elseif (is_dir(__DIR__ . '/storage') && !is_link(__DIR__ . '/storage') && is_file(__DIR__ . '/artisan')) {
        $storage = __DIR__ . '/storage';                             // an adopted website: its storage/ moves when it switches
    } else {
        $storage = $release . '/storage';
    }
    return [$release, $shared, $storage];
}

function ra_laravel_skeleton(string $storage): void
{
    foreach (['app/public', 'framework/cache/data', 'framework/sessions', 'framework/views', 'logs'] as $sub) {
        if (!is_dir($storage . '/' . $sub)) {
            @mkdir($storage . '/' . $sub, 0755, true);
        }
    }
}

/** public/index.php of a release: the application, .env and storage/ are outside the website folder. */
function ra_laravel_front(string $release, string $shared, string $version): string
{
    $template = <<<'PHP'
<?php
// Written by Arsika Rollout Manager for release %VERSION%: the application, its .env and storage/ live outside the
// website folder (in the data folder of the site's agent).
define('LARAVEL_START', microtime(true));
$rolloutApp = %APP%;
$rolloutShared = %SHARED%;
$rolloutStorage = is_dir($rolloutShared . '/storage') ? $rolloutShared . '/storage' : $rolloutApp . '/storage';
if (file_exists($maintenance = $rolloutStorage . '/framework/maintenance.php')) {
    require $maintenance;
}
require $rolloutApp . '/vendor/autoload.php';
$app = require_once $rolloutApp . '/bootstrap/app.php';
$app->useEnvironmentPath($rolloutShared);
$app->useStoragePath($rolloutStorage);
if (method_exists($app, 'usePublicPath')) {
    $app->usePublicPath(__DIR__);
} else {
    $app->instance('path.public', __DIR__);
}
if (method_exists($app, 'handleRequest')) {
    $app->handleRequest(Illuminate\Http\Request::capture());
} else {
    $kernel = $app->make(Illuminate\Contracts\Http\Kernel::class);
    $response = $kernel->handle($request = Illuminate\Http\Request::capture())->send();
    $kernel->terminate($request, $response);
}

PHP;
    return strtr($template, ['%VERSION%' => preg_replace('/[^0-9A-Za-z._+-]/', '', $version), '%APP%' => var_export($release, true),
                             '%SHARED%' => var_export($shared, true)]);
}

/** Before the database changes and the switch: .env (APP_KEY), storage/ and public/index.php of the release. */
function ra_laravel_prepare(array &$state, array $p): array
{
    $version = ra_version((string)($p['version'] ?? ''));
    list($release, $shared) = ra_laravel_paths($version);
    if (!is_file($release . '/artisan') || !is_file($release . '/bootstrap/app.php')) {
        throw new RaError(422, 'not_laravel', 'release ' . $version . ' is not a Laravel application (artisan and bootstrap/app.php are missing)');
    }
    if (!is_file($release . '/vendor/autoload.php')) {
        throw new RaError(422, 'no_vendor', 'release ' . $version . ' has no vendor/ folder');
    }
    // the live release has its public/ in the website folder (deploy again); any other release has it in its folder
    $front = $version === ($state['current'] ?? null) ? __DIR__ . '/index.php' : $release . '/public/index.php';
    $original = is_file($front) ? (string)file_get_contents($front) : '';
    if (strpos($original, 'bootstrap/app.php') === false) {
        throw new RaError(422, 'no_front', 'public/index.php of release ' . $version . ' is not the front controller of Laravel');
    }
    $result = ['data_outside' => strpos(ra_data(), __DIR__ . '/') !== 0];
    $env = $shared . '/.env';
    if (!is_file($env)) {
        if (!empty($p['adopt']) && is_file(__DIR__ . '/.env') && is_file(__DIR__ . '/artisan') && @copy(__DIR__ . '/.env', $env)) {
            @chmod($env, 0600);
            $result['env'] = 'copied';
        } else {
            throw new RaError(422, 'no_env', 'the .env of the site is not set yet: fill it in the dashboard (site page, .env) and deploy again');
        }
    }
    $values = ra_env($env);
    $text = (string)file_get_contents($env);
    if (trim((string)($values['APP_KEY'] ?? '')) === '') {
        $key = 'APP_KEY=base64:' . base64_encode(random_bytes(32));
        $text = preg_match('/^APP_KEY=.*$/m', $text) ? (string)preg_replace('/^APP_KEY=.*$/m', $key, $text, 1) : $key . "\n" . $text;
        ra_write_file($env, $text, 0600);
        $result['app_key'] = 'generated';
    }
    if (strtolower((string)($values['DB_CONNECTION'] ?? '')) === 'sqlite' && trim((string)($values['DB_DATABASE'] ?? '')) === '') {
        $file = $shared . '/database.sqlite';                       // not inside a release: it would be replaced
        if (!is_file($file)) {
            @touch($file);
        }
        $line = 'DB_DATABASE=' . $file;
        $text = preg_match('/^#?\s*DB_DATABASE=.*$/m', $text) ? (string)preg_replace('/^#?\s*DB_DATABASE=.*$/m', $line, $text, 1) : $text . "\n" . $line . "\n";
        ra_write_file($env, $text, 0600);
        $result['sqlite'] = $file;
    }
    if (is_dir($shared . '/storage')) {
        $result['storage'] = 'shared';
    } elseif (!empty($p['adopt']) && is_dir(__DIR__ . '/storage') && !is_link(__DIR__ . '/storage') && is_file(__DIR__ . '/artisan')) {
        $result['storage'] = 'website';
    } elseif (is_dir($release . '/storage') && @rename($release . '/storage', $shared . '/storage')) {
        $result['storage'] = 'seeded';
    } else {
        $result['storage'] = 'created';
    }
    if ($result['storage'] !== 'website') {
        ra_laravel_skeleton($shared . '/storage');
    }
    if (!is_dir($release . '/bootstrap/cache')) {
        @mkdir($release . '/bootstrap/cache', 0755, true);
    }
    ra_write_file($front, ra_laravel_front($release, $shared, $version));
    return $result;
}

/** After a switch: storage/ complete, the public storage link (when links are allowed), compiled views cleared. */
function ra_laravel_finish(array &$state, array $p): array
{
    $shared = ra_dir('shared');
    $result = ['storage_link' => 'exists', 'views_cleared' => 0];
    ra_laravel_skeleton($shared . '/storage');
    $link = __DIR__ . '/storage';
    if (!ra_exists($link) && ($p['link'] ?? true)) {
        $target = $shared . '/storage/app/public';
        if (ra_disabled('symlink')) {
            $result['storage_link'] = 'symlink() is disabled';
        } elseif (@symlink($target, $link)) {
            foreach ([ra_data(), $shared] as $dir) {
                @chmod($dir, 0711);                                  // the web server follows the link
            }
            $result['storage_link'] = 'created';
        } else {
            $result['storage_link'] = 'symlink() failed';
        }
    }
    foreach (glob($shared . '/storage/framework/views/*.php') ?: [] as $file) {
        if (@unlink($file)) {
            $result['views_cleared']++;
        }
    }
    return $result;
}

/** Migrations that the database has run already (Laravel's migrations table). */
function ra_laravel_migrations(array &$state, array $p): array
{
    $db = ra_db((string)($p['env_file'] ?? '@shared/.env'), 'auto');
    if ($db === null) {
        throw new RaError(422, 'no_database', 'no database settings in the .env');
    }
    $pdo = ra_pdo($db);
    try {
        $rows = $pdo->query('SELECT migration FROM migrations')->fetchAll(PDO::FETCH_COLUMN);
    } catch (PDOException $e) {
        return ['applied' => [], 'table' => false];
    }
    return ['applied' => array_values(array_map('strval', $rows)), 'table' => true];
}

/** A fatal error or an exit() inside the application still answers with JSON (and the output so far). */
function ra_artisan_guard(): void
{
    if (!empty($GLOBALS['ra_artisan_done'])) {
        return;
    }
    $error = error_get_last();
    $fatal = $error && in_array($error['type'], [E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR, E_USER_ERROR], true);
    $output = '';
    while (ob_get_level() > 0) {
        $output = (string)ob_get_clean() . $output;
    }
    if (!headers_sent()) {
        ra_send(500, ['ok' => false, 'error' => [
            'code' => $fatal ? 'php_fatal' : 'exited',
            'message' => $fatal ? 'PHP error in the application: ' . $error['message'] . ' (' . basename((string)$error['file']) . ':' . $error['line'] . ')'
                : 'the command ended the PHP request early (exit)',
            'output' => ra_clip($output)]]);
    }
}

/** Run an artisan command of a release inside this PHP request: no terminal, no shell. */
function ra_artisan(array &$state, array $p): array
{
    $version = ra_version((string)($p['version'] ?? ($state['current'] ?? '')));
    $command = trim((string)($p['command'] ?? ''));
    if ($command === '' || strlen($command) > 1000 || preg_match('/[\r\n\0]/', $command)) {
        throw new RaError(422, 'bad_command', 'invalid artisan command');
    }
    list($release, $shared, $storage) = ra_laravel_paths($version);
    if (!is_file($release . '/artisan') || !is_file($release . '/vendor/autoload.php')) {
        throw new RaError(422, 'not_laravel', 'release ' . $version . ' is not a Laravel application with vendor/ on the hosting');
    }
    if (!is_file($shared . '/.env')) {
        throw new RaError(422, 'no_env', 'the .env of the site is not set yet: fill it in the dashboard');
    }
    if (!ra_disabled('set_time_limit')) {
        @set_time_limit(max(30, min(3600, (int)($p['timeout'] ?? 300))));
    }
    ra_save($state);                                                 // the application may end the request itself
    // hostings (aaPanel by default) disable putenv(), chdir() and others: Laravel reads $_ENV and $_SERVER as well
    $_ENV['APP_RUNNING_IN_CONSOLE'] = $_SERVER['APP_RUNNING_IN_CONSOLE'] = 'true';
    if (!ra_disabled('putenv')) {
        @putenv('APP_RUNNING_IN_CONSOLE=true');
    }
    if (strtok($command, ' ') === 'tinker') {
        // PsySH (tinker) looks for its folders in the user's home and in /etc, /usr/share: outside open_basedir (aaPanel
        // allows only the site); with PHP-FPM it reads them from $_SERVER, so they go into storage/framework/psysh
        foreach (['HOME' => '', 'XDG_CONFIG_HOME' => '/config', 'XDG_CONFIG_DIRS' => '/config', 'XDG_DATA_HOME' => '/data',
                  'XDG_DATA_DIRS' => '/data', 'XDG_RUNTIME_DIR' => '/run', 'APPDATA' => '/config'] as $key => $sub) {
            $dir = $storage . '/framework/psysh' . $sub;
            if (!is_dir($dir)) {
                @mkdir($dir, 0700, true);
            }
            $_SERVER[$key] = $_ENV[$key] = $dir;
            if (!ra_disabled('putenv')) {
                @putenv($key . '=' . $dir);
            }
        }
    }
    $cwd = ra_disabled('getcwd') ? false : getcwd();
    if ($cwd !== false && !ra_disabled('chdir')) {
        @chdir($release);
    }
    $GLOBALS['ra_artisan_done'] = false;
    register_shutdown_function('ra_artisan_guard');
    ob_start();
    try {
        require_once $release . '/vendor/autoload.php';
        $app = require $release . '/bootstrap/app.php';
        $app->useEnvironmentPath($shared);
        $app->useStoragePath($storage);
        if (method_exists($app, 'usePublicPath')) {
            $app->usePublicPath(__DIR__);
        } else {
            $app->instance('path.public', __DIR__);
        }
        $kernel = $app->make('Illuminate\Contracts\Console\Kernel');
        $buffer = new \Symfony\Component\Console\Output\BufferedOutput();
        $line = $command . (preg_match('/(^|\s)(--no-interaction|-n)(\s|$)/', $command) ? '' : ' --no-interaction');
        $status = (int)$kernel->call($line, [], $buffer);
        $text = $buffer->fetch();
    } catch (Throwable $error) {
        $GLOBALS['ra_artisan_done'] = true;
        $stray = (string)ob_get_clean();
        throw new RaError(422, 'artisan_failed', 'php artisan ' . strtok($command, ' ') . ': ' . get_class($error) . ': ' . substr($error->getMessage(), 0, 500)
            . ($stray !== '' ? "\n" . ra_clip($stray, 4000) : ''));
    } finally {
        if ($cwd !== false && !ra_disabled('chdir')) {
            @chdir($cwd);
        }
    }
    $GLOBALS['ra_artisan_done'] = true;
    $stray = (string)ob_get_clean();
    restore_error_handler();
    restore_exception_handler();
    return ['status' => $status, 'output' => ra_clip($stray . $text)];
}

// ------------------------------------------------------------------ the page in the browser

function ra_page(): void
{
    header('Content-Type: text/html; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Frame-Options: DENY');
    header('X-Robots-Tag: noindex, nofollow');
    $e = function ($text) { return htmlspecialchars((string)$text, ENT_QUOTES, 'UTF-8'); };
    $body = '';
    if (ra_is_template()) {
        $body = '<p class="bad">Ini file contoh. Unduh file agen dari dasbor (Situs → Hosting → Unduh file agen).<br><small>This is the template: download the agent file from the dashboard.</small></p>';
    } else {
        try {
            $state = ra_load();
            $info = ra_info($state);
        } catch (Throwable $error) {
            $state = [];
            $info = null;
        }
        if ($info !== null && $info['paired']) {
            $body = '<p class="ok">Terhubung. Situs ini di-update oleh Arsika Rollout Manager.<br><small>Connected: this website is updated by Arsika Rollout Manager.</small></p>';
        } else {
            $https = strncmp(ra_url(), 'https://', 8) === 0;
            $lan = !$https && ra_local_host((string)parse_url(ra_url(), PHP_URL_HOST));
            $checks = [                                                // [what, ok | warn | bad, note]
                ['PHP ' . PHP_VERSION, version_compare(PHP_VERSION, '7.4', '>=') ? 'ok' : 'bad', 'PHP 7.4+'],
                ['ZipArchive', class_exists('ZipArchive') ? 'ok' : 'bad', 'ekstensi zip / zip extension'],
                ['zlib', function_exists('gzopen') ? 'ok' : 'bad', 'backup database'],
                ['PDO: ' . (class_exists('PDO') ? implode(', ', PDO::getAvailableDrivers()) : '–'), class_exists('PDO') ? 'ok' : 'bad', 'database'],
                ['Folder bisa ditulis / writable', $info !== null && $info['writable'] ? 'ok' : 'bad', ''],
                ['HTTPS', $https ? 'ok' : ($lan ? 'warn' : 'bad'), $https ? '' : ($lan ? 'http di jaringan lokal: boleh / plain http on a local network: allowed'
                    : 'buka lewat https:// / open it over https://')],
            ];
            $ready = $checks[0][1] === 'ok' && $checks[1][1] === 'ok' && $checks[4][1] === 'ok';
            $rows = '';
            foreach ($checks as $check) {
                $rows .= '<tr><td>' . $e($check[0]) . '</td><td class="' . $check[1] . '">' . ['ok' => '✓', 'warn' => '!', 'bad' => '✗'][$check[1]]
                    . '</td><td><small>' . $e($check[2]) . '</small></td></tr>';
            }
            // a query (not a #fragment) survives a sign-in in front of the dashboard, e.g. Cloudflare Access; only the
            // dashboard that knows this file's key can connect it, so the address may be changed safely
            $link = rtrim(RA_DASHBOARD, '/') . '/?connect=' . rawurlencode(RA_SITE) . '&agent=' . rawurlencode(ra_url());
            $json = JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT | JSON_UNESCAPED_SLASHES;
            $form = '<form id="ra-connect"><label for="ra-dash">Alamat cicd (dasbor) / Address of the dashboard</label>'
                . '<input id="ra-dash" type="text" inputmode="url" required value="' . $e(RA_DASHBOARD) . '" placeholder="https://cicd.example.com">'
                . '<p><button class="button" type="submit">Hubungkan ke dasbor / Connect to the dashboard</button></p>'
                . '<p><small>Sudah terisi dari dasbor; ubah hanya kalau dasbor dibuka lewat alamat lain (mis. alamat jaringan lokal).<br>'
                . 'Filled in by the dashboard; change it only when you open the dashboard at another address.</small></p></form>'
                . '<script>document.getElementById("ra-connect").addEventListener("submit",function(e){e.preventDefault();'
                . 'var d=document.getElementById("ra-dash").value.trim().replace(/\/+$/,"");if(!/^https?:\/\//i.test(d)){d="https://"+d;}'
                . 'location.href=d+"/?connect="+encodeURIComponent(' . json_encode(RA_SITE, $json) . ')+"&agent="+encodeURIComponent('
                . json_encode(ra_url(), $json) . ');});</script>'
                . '<noscript><p><a class="button" href="' . $e($link) . '">Hubungkan ke dasbor / Connect to the dashboard</a></p></noscript>';
            $body = '<p>File agen untuk situs <b>' . $e(RA_SITE) . '</b>. Periksa alamat dasbor, klik Hubungkan, lalu konfirmasi di dasbor.<br><small>Agent file of the site '
                . $e(RA_SITE) . ': check the address of the dashboard, click Connect, then confirm in the dashboard.</small></p><table>' . $rows . '</table>'
                . ($ready ? $form : '<p class="bad">Tempat ini belum memenuhi syarat di atas. / This place does not meet the requirements above.</p>');
        }
    }
    echo '<!doctype html><html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        . '<title>Arsika Rollout Manager</title><style>body{font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#1f2937}'
        . 'h1{font-size:1.3rem}table{border-collapse:collapse;width:100%;margin-bottom:18px}td{border-bottom:1px solid #e5e7eb;padding:8px 6px}'
        . '.ok{color:#047857}.warn{color:#b45309}.bad{color:#b91c1c}label{display:block;font-weight:600;margin-bottom:6px}'
        . 'input{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;font:inherit}'
        . '.button{display:inline-block;background:#4f46e5;color:#fff;padding:10px 18px;border:0;border-radius:999px;text-decoration:none;font:inherit;font-weight:600;cursor:pointer}</style></head>'
        . '<body><h1>Arsika Rollout Manager · agent ' . RA_VERSION . '</h1>' . $body . '</body></html>';
}

// ------------------------------------------------------------------ dispatch

if (PHP_SAPI === 'cli') {
    return;
}
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'POST') {
    ra_page();
    return;
}
$ra_lock = null;
try {
    if (ra_is_template()) {
        throw new RaError(503, 'template', 'this is the template of the agent: download the agent file from the dashboard');
    }
    $action = (string)($_GET['action'] ?? '');
    $body = (string)file_get_contents('php://input');
    $ra_lock = fopen(ra_data() . '/lock', 'c');
    if ($ra_lock !== false) {
        flock($ra_lock, LOCK_EX);
    }
    $state = ra_load();
    $used = ra_auth($state, $action, $body);
    ra_save($state);
    if ($used === 'pairing' && !in_array($action, ['hello', 'pair'], true)) {
        throw new RaError(401, 'not_paired', 'connect the site first');
    }
    ra_journal($state);                                              // finish a switch that an earlier request left open
    $params = $action === 'upload' ? $_GET : (json_decode($body === '' ? '{}' : $body, true) ?: []);
    $handlers = [
        'hello' => function (&$state, $p) { return ra_info($state); },
        'pair' => 'ra_pair', 'forget' => 'ra_forget', 'status' => 'ra_status',
        'upload' => 'ra_upload', 'finish-upload' => 'ra_finish_upload', 'extract' => 'ra_extract',
        'activate' => 'ra_activate', 'delete-release' => 'ra_delete_release', 'migrate' => 'ra_migrate',
        'backup' => 'ra_backup', 'download' => 'ra_download', 'clear' => 'ra_clear',
        'restore-files' => 'ra_restore_files', 'restore-db' => 'ra_restore_db',
        'self-update' => 'ra_self_update', 'env-read' => 'ra_env_read', 'env-write' => 'ra_env_write',
        'laravel-prepare' => 'ra_laravel_prepare', 'laravel-finish' => 'ra_laravel_finish',
        'laravel-migrations' => 'ra_laravel_migrations', 'artisan' => 'ra_artisan',
    ];
    if (!isset($handlers[$action])) {
        throw new RaError(404, 'unknown_action', 'unknown action: ' . $action);
    }
    $result = call_user_func_array($handlers[$action], [&$state, $params, $body]);
    ra_save($state);
    if ($result instanceof RaRaw) {
        header('Content-Type: application/octet-stream');
        header('Cache-Control: no-store');
        header('Content-Length: ' . strlen($result->bytes));
        echo $result->bytes;
    } else {
        ra_send(200, ['ok' => true, 'data' => $result]);
    }
} catch (RaError $error) {
    $GLOBALS['ra_artisan_done'] = true;                              // answered here: the artisan guard stays quiet
    ra_send($error->status, ['ok' => false, 'error' => ['code' => $error->name, 'message' => $error->getMessage()]]);
} catch (Throwable $error) {
    $GLOBALS['ra_artisan_done'] = true;
    ra_send(500, ['ok' => false, 'error' => ['code' => 'agent_error', 'message' => $error->getMessage()]]);
} finally {
    if (is_resource($ra_lock)) {
        flock($ra_lock, LOCK_UN);
        fclose($ra_lock);
    }
}

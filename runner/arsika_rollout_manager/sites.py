"""Site settings: validation, defaults and presets.

A site is a web application on this server that is installed from a GitHub release: a zip asset of the release
(source "asset") or the release's source code archive (source "source"), or from an archive uploaded through the
dashboard (source "upload", see uploads.py):

    <path>/releases/<version>/    one folder per installed release
    <path>/shared/                files kept between releases (.env, storage/ ...), linked into every release
    <path>/current -> releases/<version>
    <path>/<docroot_link> -> current/<docroot_target>   the web server's document root (aaPanel: Running directory)

Deploy mode "files" serves the release folder through the web server (PHP, static files, an app run by a systemd
unit); deploy mode "compose" runs the release's Docker Compose files (see compose.py).

Hooks ("build" before the switch, "activate" after it) are shell commands run as root, or "preset:<name>" for the
scripts in runner/presets/.
"""
import os
import re

from .util import REPO, SERVICE, SITE_ID, USER

SOURCE_ARCHIVE = ":source"                 # release pattern meaning: the release's source code archive (zipball)

# what an uploaded archive must not bring live: the local environment, version control, editors, logs and caches
UPLOAD_EXCLUDE = [".env", ".env.backup", ".env.production", ".git/", ".svn/", ".hg/", "node_modules/", ".idea/", ".vscode/",
                  ".fleet/", ".phpunit.result.cache", ".phpunit.cache/", "**/.DS_Store", "**/Thumbs.db", "**/desktop.ini",
                  "__MACOSX/", "storage/logs/*", "storage/framework/views/*", "storage/framework/sessions/*",
                  "storage/framework/cache/data/*", "storage/framework/testing/*", "storage/pail/", "bootstrap/cache/*.php",
                  "public/hot", "public/storage"]

DEFAULTS = {
    "name": "",
    "domain": "",
    "path": "",
    "repo": "",
    "asset_pattern": "*.zip",
    "web_user": "www",
    "keep_releases": 5,
    "shared": [],
    "docroot_link": "public",
    "docroot_target": "public",
    "build": "",
    "activate": "",
    "services": [],
    "reload_php_fpm": True,
    "health_url": "",
    "health_contains": "",
    "health_local": False,
    "backup_before_deploy": True,
    "backup_env_file": "",
    "backup_paths": [],
    "backup_exclude": [],
    "backup_keep": 5,
    "adopt_keep": [".user.ini", ".well-known", ".htaccess"],
    "deploy_mode": "files",
    "source": "asset",
    "compose_files": [],
    "compose_project": "",
    "compose_build": True,
    "backup_db": "auto",
    "backup_db_service": "",
    "backup_volumes": [],
    "agent_url": "",
    "migrations_dir": "database/migrations",
    "hosting_app": "",
    "upload_exclude": UPLOAD_EXCLUDE,
    "upload_keep": 5,
}

HOSTING = {
    "deploy_mode": "hosting",
    "web_user": "",
    "docroot_link": "",
    "docroot_target": ".",
    "reload_php_fpm": False,
    "keep_releases": 2,
    "health_url": "{site_url}/",
    "adopt_keep": [".well-known", "cgi-bin", ".user.ini"],
}

COMPOSE = {
    "deploy_mode": "compose",
    "source": "source",
    "web_user": "",
    "shared": [".env"],
    "docroot_link": "",
    "docroot_target": ".",
    "reload_php_fpm": False,
    "health_url": "https://{domain}/",
    "backup_env_file": "",
    "backup_paths": [".env"],
}

PRESETS = {
    "anemia-web": {
        "label": "Anemia Web 3.x",
        "repo": "RRBillanivo/Anemia_Web",
        "asset_pattern": "anemia_web_v*.zip",
        "shared": [".env", "storage/"],
        "docroot_link": "public",
        "docroot_target": "public",
        "build": "preset:anemia-web",
        "activate": "preset:anemia-web",
        "services": ["anemia-engine"],
        "reload_php_fpm": True,
        "health_url": "https://{domain}/api/v1/app",
        "health_contains": '"version":"{version}"',
        "backup_env_file": ".env",
        "backup_paths": [".env", "storage/"],
        "backup_exclude": ["storage/framework/sessions", "storage/framework/tmp", "storage/logs"],
    },
    "php-app": {
        "label": "PHP application (public/ + .env + storage/)",
        "shared": [".env", "storage/"],
        "docroot_link": "public",
        "docroot_target": "public",
        "reload_php_fpm": True,
        "health_url": "https://{domain}/",
        "backup_env_file": ".env",
        "backup_paths": [".env", "storage/"],
        "backup_exclude": ["storage/framework/sessions", "storage/logs"],
    },
    "static": {
        "label": "Static site (HTML/CSS/JS)",
        "shared": [],
        "docroot_link": "public",
        "docroot_target": ".",
        "reload_php_fpm": False,
        "health_url": "https://{domain}/",
        "backup_paths": [],
    },
    "compose-build": dict(COMPOSE, label="Docker Compose: build the images from the release", compose_build=True),
    "compose-images": dict(COMPOSE, label="Docker Compose: pull ready-made images", compose_build=False),
    "hosting-static": dict(HOSTING, label="Hosting: static site or built files (HTML, CSS, JS, dist/)", shared=[], backup_paths=[],
                           backup_db="none"),
    "hosting-php": dict(HOSTING, label="Hosting: PHP website (.env and database)", shared=[".env"], backup_env_file=".env",
                        backup_paths=[".env"]),
    # the website folder gets only public/; the application, its .env and storage/ live in the agent's data folder
    "hosting-laravel": dict(HOSTING, label="Hosting: Laravel (php artisan migrate without a terminal, composer on this server)",
                            hosting_app="laravel", docroot_target="public", shared=[], backup_env_file="@shared/.env",
                            backup_paths=["@shared/.env", "@shared/storage/app"], backup_exclude=[]),
    "rollout-manager": {
        "label": "Arsika Rollout Manager (this dashboard: updates itself and the runner of this server)",
        "repo": "RRBillanivo/arsika-rollout-manager",
        "asset_pattern": "arsika-rollout-manager-v*.zip",
        "shared": [".env", "storage/"],
        "docroot_link": "public",
        "docroot_target": "public",
        "build": "preset:rollout-manager",
        "activate": "preset:rollout-manager",
        "reload_php_fpm": True,
        "health_url": "http://{domain}/api/v1/app",
        "health_contains": '"version":"{version}"',
        "health_local": True,
        "backup_env_file": ".env",
        "backup_paths": [".env"],
    },
}

RELATIVE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9_.@+-][A-Za-z0-9_./@+-]{0,199}$")
DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
IPV4 = re.compile(r"^((25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(25[0-5]|2[0-4]\d|1?\d?\d)$")
PATTERN = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9_.@+*?!\[\] /-]{1,200}$")
LIST_FIELDS = ("shared", "services", "backup_paths", "backup_exclude", "adopt_keep", "compose_files", "backup_volumes",
               "upload_exclude")
BOOL_FIELDS = ("reload_php_fpm", "backup_before_deploy", "compose_build", "health_local")
HOOK_FIELDS = ("build", "activate")
CHOICES = {"deploy_mode": ("files", "compose", "hosting"), "source": ("asset", "source", "upload"),
           "backup_db": ("auto", "mysql", "postgres", "none")}
PROJECT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def preset(name, web_user=None):
    """Default settings of a preset (without its label). ``web_user`` = this server's web server user, used by the
    presets that serve files (Docker Compose sites have none)."""
    values = dict(DEFAULTS)
    values.update({k: v for k, v in PRESETS.get(name, {}).items() if k != "label"})
    if web_user is not None and values["deploy_mode"] == "files":
        values["web_user"] = web_user
    return values


def presets_info(web_user=None):
    return [{"id": key, "label": value["label"], "settings": preset(key, web_user)} for key, value in PRESETS.items()]


def release_pattern(site):
    """What to take from a release: the asset pattern, or SOURCE_ARCHIVE for the source code archive."""
    return SOURCE_ARCHIVE if site.get("source") == "source" else site.get("asset_pattern") or "*.zip"


def _as_list(value):
    if isinstance(value, str):
        value = [line.strip() for line in value.replace(",", "\n").splitlines()]
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def validate(data, site_roots, existing=None):
    """Check settings sent by the dashboard. -> (site dict, errors dict field -> message)."""
    site = dict(DEFAULTS)
    site.update(existing or {})
    errors = {}
    for key in DEFAULTS:
        if key in data:
            site[key] = data[key]
    if existing is None:
        site["id"] = str(data.get("id", "")).strip().lower()
        if not SITE_ID.match(site["id"]):
            errors["id"] = "Use 1-40 lowercase letters, digits and hyphens (e.g. example)."
    site["name"] = str(site["name"]).strip()[:80] or site.get("id", "")
    site["domain"] = str(site["domain"]).strip().lower()
    if site["domain"] and not DOMAIN.match(site["domain"]) and not IPV4.match(site["domain"]):
        errors["domain"] = "Not a valid domain name (or IPv4 address)."

    hosting = str(site.get("deploy_mode") or "") == "hosting"
    path = os.path.normpath(str(site["path"]).strip()) if str(site["path"]).strip() else ""
    site["path"] = path.replace("\\", "/") if os.name != "posix" else path
    roots = [os.path.normpath(r) for r in site_roots]
    inside = any(os.path.normcase(path).startswith(os.path.normcase(r) + os.sep) for r in roots)
    if hosting:
        site["path"] = str(site["path"])[:300]                       # the folder on the hosting, reported by its agent
    elif not path or not os.path.isabs(path):
        errors["path"] = "Absolute path of the site folder, e.g. /www/wwwroot/example.com."
    elif not inside:
        errors["path"] = "The site folder must be inside " + ", ".join(site_roots) + "."

    site["repo"] = str(site["repo"]).strip()
    if site["repo"].startswith("https://github.com/"):
        site["repo"] = site["repo"][len("https://github.com/"):].strip("/")
    if site["repo"].endswith(".git"):
        site["repo"] = site["repo"][:-4]
    uploaded = str(site.get("source") or "").strip().lower() == "upload"
    if not REPO.match(site["repo"]) and not (uploaded and not site["repo"]):
        errors["repo"] = "GitHub repository as owner/name, e.g. RRBillanivo/Anemia_Web."
    site["asset_pattern"] = str(site["asset_pattern"]).strip() or "*.zip"
    if len(site["asset_pattern"]) > 100 or "/" in site["asset_pattern"]:
        errors["asset_pattern"] = "File name pattern of the release asset, e.g. *.zip."
    site["web_user"] = str(site["web_user"]).strip()
    if site["web_user"] and not USER.match(site["web_user"]):
        errors["web_user"] = "Linux user name, e.g. www."

    for key in LIST_FIELDS:
        site[key] = _as_list(site[key])
    for key in ("shared", "backup_paths", "backup_exclude", "adopt_keep"):
        bad = [p for p in site[key] if not RELATIVE.match(p)]
        if bad:
            errors[key] = "Relative paths only (no leading / and no ..): " + ", ".join(bad[:3])
    bad = [p for p in site["upload_exclude"] if not PATTERN.match(p)]
    if bad:
        errors["upload_exclude"] = "Relative paths or patterns (*, ?, **/name) without a leading / and without ..: " + ", ".join(bad[:3])
    bad = [s for s in site["services"] if not SERVICE.match(s)]
    if bad:
        errors["services"] = "systemd unit names only: " + ", ".join(bad[:3])
    for key in ("docroot_link", "docroot_target"):
        site[key] = str(site[key]).strip().strip("/") or ("." if key == "docroot_target" else "")
    if site["docroot_link"] and (not RELATIVE.match(site["docroot_link"]) or "/" in site["docroot_link"]
                                 or site["docroot_link"] in ("releases", "shared", "current")):
        errors["docroot_link"] = "Name of a link in the site folder, e.g. public (not releases, shared or current)."
    if site["docroot_target"] != "." and not RELATIVE.match(site["docroot_target"]):
        errors["docroot_target"] = "Folder inside the release, e.g. public (or . for the release itself)."

    for key in HOOK_FIELDS:
        site[key] = str(site[key] or "").strip()
        if len(site[key]) > 2000 or "\x00" in site[key]:
            errors[key] = "Command too long."
        elif site[key].startswith("preset:") and site[key][7:] not in PRESETS:
            errors[key] = "Unknown preset " + site[key][7:] + "."
    for key in BOOL_FIELDS:
        site[key] = site[key] in (True, 1, "1", "true", "on", "yes")
    for key, low, high in (("keep_releases", 2, 20), ("backup_keep", 1, 50), ("upload_keep", 1, 50)):
        try:
            site[key] = int(site[key])
            if not low <= site[key] <= high:
                raise ValueError
        except (TypeError, ValueError):
            errors[key] = "Number between %d and %d." % (low, high)

    site["health_url"] = str(site["health_url"]).strip()
    if site["health_url"] and not re.match(r"^(https?://|\{site_url\})[^\s]*$", site["health_url"]):
        errors["health_url"] = "Address starting with https:// or {site_url} (may contain {domain})."
    site["health_contains"] = str(site["health_contains"])[:200]
    site["backup_env_file"] = str(site["backup_env_file"]).strip()
    if site["backup_env_file"] and not RELATIVE.match(site["backup_env_file"]):
        errors["backup_env_file"] = "Path inside shared/, e.g. .env."

    for key, allowed in CHOICES.items():
        site[key] = str(site[key] or DEFAULTS[key]).strip().lower()
        if site[key] not in allowed:
            errors[key] = "One of: " + ", ".join(allowed) + "."
    bad = [p for p in site["compose_files"] if not RELATIVE.match(p)]
    if bad:
        errors["compose_files"] = "Relative paths only (no leading / and no ..): " + ", ".join(bad[:3])
    site["compose_project"] = str(site["compose_project"] or "").strip()
    if site["compose_project"] and not PROJECT.match(site["compose_project"]):
        errors["compose_project"] = "Lowercase letters, digits, - and _ (empty: the name of the site folder)."
    site["backup_db_service"] = str(site["backup_db_service"] or "").strip()
    if site["backup_db_service"] and not CONTAINER.match(site["backup_db_service"]):
        errors["backup_db_service"] = "Name of a compose service or container, e.g. db."
    bad = [v for v in site["backup_volumes"] if not CONTAINER.match(v)]
    if bad:
        errors["backup_volumes"] = "Volume names only: " + ", ".join(bad[:3])
    if site["deploy_mode"] == "compose" and site["reload_php_fpm"]:
        site["reload_php_fpm"] = False                               # the containers serve the site
    site["migrations_dir"] = str(site["migrations_dir"] or "database/migrations").strip().strip("/")
    if not RELATIVE.match(site["migrations_dir"]):
        errors["migrations_dir"] = "Folder inside the release, e.g. database/migrations."
    site["agent_url"] = str(site["agent_url"] or "").strip()
    site["hosting_app"] = str(site.get("hosting_app") or "").strip().lower()
    if site["hosting_app"] not in ("", "laravel"):
        errors["hosting_app"] = "Empty (plain files) or laravel."
    if site["deploy_mode"] == "hosting":
        site["reload_php_fpm"], site["services"], site["build"], site["activate"] = False, [], "", ""
        if site["hosting_app"] == "laravel" and site["docroot_target"] == ".":
            site["docroot_target"] = "public"                        # only public/ goes into the website folder
    else:
        site["hosting_app"] = ""
    return site, errors


def site_url(site):
    """Address of the website folder of a hosting site: the address of its agent file without the file name."""
    url = str(site.get("agent_url") or "")
    return url.rsplit("/", 1)[0] if "/" in url else ""


def expand(site, text, version=""):
    """Replace {domain}, {version}, {site} and {site_url} in health check settings."""
    return (str(text).replace("{site_url}", site_url(site)).replace("{domain}", site.get("domain", ""))
            .replace("{version}", version).replace("{site}", site.get("id", "")))


def layout(site):
    path = site["path"]
    return {
        "releases": os.path.join(path, "releases"),
        "shared": os.path.join(path, "shared"),
        "current": os.path.join(path, "current"),
        "docroot": os.path.join(path, site["docroot_link"]) if site.get("docroot_link") else None,
        "pre_rollout": os.path.join(path, "_pre_rollout"),
    }

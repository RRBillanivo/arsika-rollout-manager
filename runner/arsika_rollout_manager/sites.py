"""Site settings: validation, defaults and presets.

A site is a web application on this server that is installed from the zip asset of a GitHub release:

    <path>/releases/<version>/    one folder per installed release
    <path>/shared/                files kept between releases (.env, storage/ ...), linked into every release
    <path>/current -> releases/<version>
    <path>/<docroot_link> -> current/<docroot_target>   the web server's document root (aaPanel: Running directory)

Hooks ("build" before the switch, "activate" after it) are shell commands run as root, or "preset:<name>" for the
scripts in runner/presets/.
"""
import os
import re

from .util import REPO, SERVICE, SITE_ID, USER

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
    "backup_before_deploy": True,
    "backup_env_file": "",
    "backup_paths": [],
    "backup_exclude": [],
    "backup_keep": 5,
    "adopt_keep": [".user.ini", ".well-known", ".htaccess"],
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
}

RELATIVE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9_.@+-][A-Za-z0-9_./@+-]{0,199}$")
DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
LIST_FIELDS = ("shared", "services", "backup_paths", "backup_exclude", "adopt_keep")
BOOL_FIELDS = ("reload_php_fpm", "backup_before_deploy")
HOOK_FIELDS = ("build", "activate")


def preset(name):
    """Default settings of a preset (without its label)."""
    values = dict(DEFAULTS)
    values.update({k: v for k, v in PRESETS.get(name, {}).items() if k != "label"})
    return values


def presets_info():
    return [{"id": key, "label": value["label"], "settings": preset(key)} for key, value in PRESETS.items()]


def _as_list(value):
    if isinstance(value, str):
        value = [line.strip() for line in value.replace(",", "\n").splitlines()]
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def validate(data, site_roots, existing=None):
    """Check settings sent by the dashboard. -> (site dict, errors dict field -> message)."""
    site = dict(existing or DEFAULTS)
    errors = {}
    for key in DEFAULTS:
        if key in data:
            site[key] = data[key]
    if existing is None:
        site["id"] = str(data.get("id", "")).strip().lower()
        if not SITE_ID.match(site["id"]):
            errors["id"] = "Use 1-40 lowercase letters, digits and hyphens (e.g. anemia)."
    site["name"] = str(site["name"]).strip()[:80] or site.get("id", "")
    site["domain"] = str(site["domain"]).strip().lower()
    if site["domain"] and not DOMAIN.match(site["domain"]):
        errors["domain"] = "Not a valid domain name."

    path = os.path.normpath(str(site["path"]).strip()) if str(site["path"]).strip() else ""
    site["path"] = path.replace("\\", "/") if os.name != "posix" else path
    roots = [os.path.normpath(r) for r in site_roots]
    inside = any(os.path.normcase(path).startswith(os.path.normcase(r) + os.sep) for r in roots)
    if not path or not os.path.isabs(path):
        errors["path"] = "Absolute path of the site folder, e.g. /www/wwwroot/example.com."
    elif not inside:
        errors["path"] = "The site folder must be inside " + ", ".join(site_roots) + "."

    site["repo"] = str(site["repo"]).strip()
    if site["repo"].startswith("https://github.com/"):
        site["repo"] = site["repo"][len("https://github.com/"):].strip("/")
    if site["repo"].endswith(".git"):
        site["repo"] = site["repo"][:-4]
    if not REPO.match(site["repo"]):
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
    for key, low, high in (("keep_releases", 2, 20), ("backup_keep", 1, 50)):
        try:
            site[key] = int(site[key])
            if not low <= site[key] <= high:
                raise ValueError
        except (TypeError, ValueError):
            errors[key] = "Number between %d and %d." % (low, high)

    site["health_url"] = str(site["health_url"]).strip()
    if site["health_url"] and not re.match(r"^https?://[^\s]+$", site["health_url"]):
        errors["health_url"] = "Address starting with https:// (may contain {domain})."
    site["health_contains"] = str(site["health_contains"])[:200]
    site["backup_env_file"] = str(site["backup_env_file"]).strip()
    if site["backup_env_file"] and not RELATIVE.match(site["backup_env_file"]):
        errors["backup_env_file"] = "Path inside shared/, e.g. .env."
    return site, errors


def expand(site, text, version=""):
    """Replace {domain}, {version} and {site} in health check settings."""
    return str(text).replace("{domain}", site.get("domain", "")).replace("{version}", version).replace("{site}", site.get("id", ""))


def layout(site):
    path = site["path"]
    return {
        "releases": os.path.join(path, "releases"),
        "shared": os.path.join(path, "shared"),
        "current": os.path.join(path, "current"),
        "docroot": os.path.join(path, site["docroot_link"]) if site.get("docroot_link") else None,
        "pre_rollout": os.path.join(path, "_pre_rollout"),
    }

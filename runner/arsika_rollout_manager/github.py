"""GitHub REST API: releases of a repository and download of their assets (private repositories need a token)."""
import fnmatch
import json
import time
import urllib.error
import urllib.request

from . import __version__
from .util import version_of


class GitHubError(Exception):
    def __init__(self, message, status=0):
        Exception.__init__(self, message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub(object):
    CACHE_SECONDS = 60

    def __init__(self, api="https://api.github.com", token=""):
        self.api = api.rstrip("/")
        self.token = token
        self.cache = {}

    def _headers(self, accept="application/vnd.github+json"):
        headers = {"Accept": accept, "User-Agent": "arsika-rollout-manager/" + __version__,
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    def _get_json(self, path):
        request = urllib.request.Request(self.api + path, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read(2000).decode("utf-8", "replace")
            try:
                message = json.loads(body).get("message", body)
            except ValueError:
                message = body
            if e.code == 404:
                message = "repository or release not found (private repository without a valid token?)"
            elif e.code in (401, 403):
                message = "GitHub refused the request (%s): check the token" % message
            raise GitHubError(message, e.code)
        except (urllib.error.URLError, OSError) as e:
            raise GitHubError("cannot reach GitHub: %s" % getattr(e, "reason", e))

    @staticmethod
    def _release(raw, pattern):
        assets = [{"id": a["id"], "name": a["name"], "size": a.get("size", 0), "url": a["url"],
                   "digest": a.get("digest") or "", "download_count": a.get("download_count", 0)} for a in raw.get("assets", [])]
        match = [a for a in assets if fnmatch.fnmatch(a["name"], pattern)]
        return {
            "tag": raw["tag_name"], "version": version_of(raw["tag_name"]), "name": raw.get("name") or raw["tag_name"],
            "published_at": raw.get("published_at"), "prerelease": bool(raw.get("prerelease")), "draft": bool(raw.get("draft")),
            "notes": (raw.get("body") or "")[:4000], "html_url": raw.get("html_url", ""),
            "asset": match[0] if match else None, "assets": [a["name"] for a in assets],
        }

    def releases(self, repo, pattern="*.zip", refresh=False):
        """Published releases, newest first (cached for a minute)."""
        key = (repo, pattern)
        cached = self.cache.get(key)
        if cached and not refresh and time.time() - cached[0] < self.CACHE_SECONDS:
            return cached[1]
        raw = self._get_json("/repos/%s/releases?per_page=30" % repo)
        releases = [self._release(r, pattern) for r in raw if not r.get("draft")]
        self.cache[key] = (time.time(), releases)
        return releases

    def release(self, repo, version, pattern="*.zip"):
        for tag in ("v" + version, version):
            try:
                return self._release(self._get_json("/repos/%s/releases/tags/%s" % (repo, tag)), pattern)
            except GitHubError as e:
                if e.status != 404:
                    raise
        raise GitHubError("release %s not found in %s" % (version, repo), 404)

    def download(self, asset, target, progress=None):
        """Download an asset to ``target``. The API answers with a redirect to a signed storage URL, which must be
        requested WITHOUT the GitHub token."""
        opener = urllib.request.build_opener(_NoRedirect)
        request = urllib.request.Request(asset["url"], headers=self._headers("application/octet-stream"))
        url = asset["url"]
        try:
            response = opener.open(request, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                raise GitHubError("download refused (HTTP %d)" % e.code, e.code)
            url = e.headers.get("Location")
            response = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "arsika-rollout-manager/" + __version__}), timeout=60)
        total = int(response.headers.get("Content-Length") or asset.get("size") or 0)
        done = 0
        last = time.time()
        with response, open(target, "wb") as out:
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                out.write(block)
                done += len(block)
                if progress and time.time() - last >= 2:
                    progress(done, total)
                    last = time.time()
        if progress:
            progress(done, total)
        return done

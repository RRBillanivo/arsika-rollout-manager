"""Arsika Rollout Manager runner: deploys GitHub releases of the registered web sites on this server (release
folders + a "current" link), rolls them back, and backs up / restores their data. Runs as root, listens on 127.0.0.1
only and accepts requests signed by the dashboard (HMAC-SHA256). Standard library only, Python 3.6+."""

__version__ = "1.1.0"

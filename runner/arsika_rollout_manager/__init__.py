"""Arsika Rollout Manager runner: deploys GitHub releases of the registered web sites on this server (release
folders + a "current" link; files served by the web server, or Docker Compose), rolls them back, and backs up /
restores their data. Runs as root and accepts requests signed by the dashboard (HMAC-SHA256): on 127.0.0.1 for a
dashboard on the same server, on HTTPS with a pinned certificate for a dashboard on another server. Standard library
only, Python 3.6+."""

__version__ = "1.6.1"

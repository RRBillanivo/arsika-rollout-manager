"""Agents that call the dashboard themselves (agent_mode "pull"): a hosting in another network that this runner cannot
open (an office server without a public address, behind a router). The agent asks the dashboard for the next request,
runs it and sends the answer with its next question; the dashboard passes both on to this runner.

The requests keep the signatures of a direct connection (the agent checks them as before), and every question of an
agent is signed as well, with the site's secret (or with the one-time key of its file while it connects):

    hex HMAC-SHA256(secret, "POLL" \\n SITE \\n TIMESTAMP \\n NONCE \\n AGENT VERSION \\n AGENT ADDRESS \\n
                            ANSWERED NONCE \\n ANSWER STATUS \\n ANSWER CONTENT TYPE \\n hex SHA-256(answer))

A request is identified by its nonce, which its signature covers, so an answer cannot be given to another request.
Everything waits in memory: when the runner restarts, a request in progress fails and the agent asks again.
"""
import collections
import hashlib
import hmac
import re
import threading
import time

WINDOW = 300                     # seconds a signed question stays valid (the clock of a hosting may drift a little)
WAIT = 20                        # longest wait for a request within one question (PHP and proxies time out later)
OFFLINE = 120                    # an agent that has not asked for this long is offline: requests fail at once
LOCKOUT = (30, 600)              # this many invalid questions of a site within so many seconds lock it for a while
NONCE = re.compile(r"^[0-9a-f]{16,64}$")


class RelayError(Exception):
    def __init__(self, status, code, message):
        Exception.__init__(self, message)
        self.status, self.code = status, code


def poll_message(site, ts, nonce, agent, url, result, status, kind, body):
    """What the agent signs for one question."""
    return "\n".join(["POLL", site, str(ts), nonce, agent, url, result, str(status), kind, hashlib.sha256(body).hexdigest()])


def ago(seconds):
    seconds = int(max(0, seconds))
    if seconds < 120:
        return "%d seconds" % seconds
    if seconds < 7200:
        return "%d minutes" % (seconds // 60)
    return "%d hours" % (seconds // 3600)


class Relay(object):
    def __init__(self, wait=WAIT, offline=OFFLINE):
        self.wait, self.offline = wait, offline
        self.cond = threading.Condition()
        self.queues = collections.defaultdict(collections.deque)   # site -> requests not taken yet
        self.open = {}                                              # nonce -> site, while its caller waits
        self.answers = {}                                           # nonce -> (status, content type, bytes)
        self.busy = {}                                              # site -> (nonce, deadline) of the request it runs
        self.lost = set()                                           # nonces of requests the agent never answered
        self.calls = {}                                             # site -> number of its latest question
        self.seen = {}                                              # site -> what the agent said last, and when
        self.nonces = {}                                            # site -> {nonce: time} of its questions
        self.failures = collections.defaultdict(list)

    # ------------------------------------------------------------------ the agent's side
    def check(self, site, secret, key, ts, nonce, signature, message):
        """Which key signed the question: "secret" (connected) or "pairing" (the one-time key of a file that connects)."""
        now = time.time()
        with self.cond:
            failures = [t for t in self.failures[site] if t > now - LOCKOUT[1]]
            self.failures[site] = failures
            if len(failures) >= LOCKOUT[0]:
                raise RelayError(429, "locked", "too many invalid requests: try again in 10 minutes")
            used = None
            if abs(now - ts) <= WINDOW and NONCE.match(nonce):
                for name, value in (("secret", secret), ("pairing", key)):
                    if value and hmac.compare_digest(hmac.new(value.encode(), message.encode(), hashlib.sha256).hexdigest(), signature):
                        used = name
                        break
            if used is None:
                failures.append(now)
                raise RelayError(401, "bad_signature", "request expired or clock mismatch" if abs(now - ts) > WINDOW else "invalid signature")
            nonces = {n: t for n, t in self.nonces.get(site, {}).items() if t > now - 2 * WINDOW}
            if nonce in nonces:
                raise RelayError(401, "replayed", "request already used")
            nonces[nonce] = now
            self.nonces[site] = nonces
            return used

    def question(self, site, used, agent, url, result, status, kind, body, wait):
        """An agent whose question was checked: its answer to an earlier request, then the next request for it (it waits
        up to ``wait`` seconds for one). -> request dict or None"""
        with self.cond:
            self.seen[site] = {"at": time.time(), "agent": agent, "url": url, "key": used}
            number = self.calls[site] = self.calls.get(site, 0) + 1
            busy = self.busy.pop(site, None)                          # an agent runs one request at a time
            if busy and busy[0] != result and self.open.get(busy[0]) == site:
                # it asks again without answering: the request never reached it (the earlier call's connection broke,
                # e.g. the hosting restarted) or PHP ended while it ran: its caller learns it at once
                self.lost.add(busy[0])
            if result and self.open.get(result) == site:
                self.answers[result] = (status, kind, body)
            self.cond.notify_all()
            deadline = time.time() + max(0, min(wait, self.wait))
            while True:
                if self.calls.get(site) != number:
                    return None                                       # a newer call of the agent: this one is gone
                queue = self.queues.get(site)
                for request in list(queue or ()):
                    if request["auth"] == used:                       # one-time key: only the requests that connect
                        queue.remove(request)
                        self.busy[site] = (request["nonce"], time.time() + request.get("timeout", 120))
                        self.seen[site]["at"] = time.time()
                        return request
                left = deadline - time.time()
                if left <= 0:
                    self.seen[site]["at"] = time.time()
                    return None
                self.cond.wait(left)

    # ------------------------------------------------------------------ the runner's side
    def last_seen(self, site, key=None):
        """Seconds since the agent of the site asked (None: not since this runner started)."""
        seen = self.seen.get(site)
        if not seen or (key and seen["key"] != key):
            return None
        return time.time() - seen["at"]

    def online(self, site, key=None):
        """The agent asked recently, or it is running a request (it asks again when that is done)."""
        busy = self.busy.get(site)
        since = self.last_seen(site, key)
        return (since is not None and since <= self.offline) or bool(busy and time.time() < busy[1])

    def is_busy(self, site):
        busy = self.busy.get(site)
        return bool(busy and time.time() < busy[1])

    def send(self, site, request, timeout):
        """Queue a signed request for the agent of the site and wait for its answer. -> (status, content type, bytes)"""
        nonce = request["nonce"]
        request = dict(request, timeout=timeout)
        with self.cond:
            if not self.online(site):
                since = self.last_seen(site)
                raise RelayError(503, "offline", "the hosting has not called the dashboard %s: open its agent file in a browser on "
                                 "the network of the hosting (it calls the dashboard while it is open and keeps calling), and check "
                                 "that the hosting can open the dashboard" % ("since this runner started" if since is None
                                                                             else "for " + ago(since)))
            self.open[nonce] = site
            self.queues[site].append(request)
            self.cond.notify_all()
            deadline = time.time() + timeout
            try:
                while nonce not in self.answers:
                    if nonce in self.lost:
                        raise RelayError(502, "lost", "the hosting did not get this request (it was restarted, or the connection "
                                         "broke): try again")
                    left = deadline - time.time()
                    if left <= 0:
                        taken = request not in self.queues[site]
                        if not taken:
                            self.queues[site].remove(request)
                        raise RelayError(504, "no_answer", "the hosting did not answer within %d seconds%s" % (
                            timeout, "" if taken else " (it did not ask for the request: is it still calling the dashboard?)"))
                    self.cond.wait(min(left, 5))
                return self.answers.pop(nonce)
            finally:
                self.open.pop(nonce, None)
                self.answers.pop(nonce, None)
                self.lost.discard(nonce)

    def forget(self, site):
        """The site was removed: requests waiting for its agent are dropped."""
        with self.cond:
            self.queues.pop(site, None)
            self.busy.pop(site, None)
            self.seen.pop(site, None)
            self.cond.notify_all()

"""Job log: one text file per job (state_dir/logs/<id>.log) that the dashboard reads while the job runs.
Line prefixes: "==" a step, "  " command output, "!!" an error, "--" other information."""
import os
import threading
from datetime import datetime


class JobLog(object):
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.file = open(path, "a", encoding="utf-8")
        self.steps = 0

    def _write(self, prefix, text):
        stamp = datetime.now().strftime("%H:%M:%S")
        with self.lock:
            for line in str(text).splitlines() or [""]:
                self.file.write("%s %s %s\n" % (stamp, prefix, line))
            self.file.flush()

    def step(self, text):
        self.steps += 1
        self._write("==", text)

    def line(self, text):
        self._write("--", text)

    def output(self, text):
        self._write("  ", text)

    def error(self, text):
        self._write("!!", text)

    def close(self):
        with self.lock:
            self.file.close()


def read_log(path, offset=0, limit=262144):
    """Text from byte ``offset`` (at most ``limit`` bytes). -> (text, next offset, size)"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", 0, 0
    offset = max(0, min(int(offset or 0), size))
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(limit)
    cut = data.rfind(b"\n") + 1 if len(data) == limit else len(data)      # whole lines only
    return data[:cut].decode("utf-8", "replace"), offset + cut, size

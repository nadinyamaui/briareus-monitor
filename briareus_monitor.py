"""Small outbound-only Linux telemetry service; no AI or third-party packages."""
import argparse
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from collector import collect

DAY_MS = 86_400_000
MAX_QUEUE_BYTES = 16 * 1024 * 1024
LOG = logging.getLogger("briareus-monitor")


def load_config(filename):
    with open(filename, encoding="utf-8") as stream:
        config = json.load(stream)
    url = urllib.parse.urlsplit(config.get("endpoint", ""))
    token = config.get("token", "")
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or not url.path.startswith("/api/v1/telemetry/ssh/")):
        raise ValueError("endpoint must be the HTTPS telemetry URL issued by Briareus")
    import re
    if not re.fullmatch(r"brm_[A-Za-z0-9_-]{43}", token):
        raise ValueError("invalid monitor token")
    if config.get("intervalSeconds", 60) != 60:
        raise ValueError("intervalSeconds must be 60")
    return config


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the machine credential to a redirect destination.
        return None


def send(config, samples):
    request = urllib.request.Request(config["endpoint"],
        data=json.dumps({"samples": samples}, separators=(",", ":")).encode(),
        headers={"Authorization": "Bearer " + config["token"],
                 "Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=10) as response:
        result = json.loads(response.read(4096))
    if result.get("received") != len(samples):
        raise ValueError("Briareus did not acknowledge the whole batch")


class Queue:
    """Durable, bounded spool for core/network outages; oldest first on recovery."""
    def __init__(self, directory):
        Path(directory).mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(Path(directory) / "samples.sqlite3"))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS samples (at INTEGER PRIMARY KEY, payload TEXT NOT NULL)")

    def append(self, at, raw):
        payload = json.dumps({"at": at, "raw": raw}, separators=(",", ":"))
        if len(payload.encode()) > 64 * 1024:
            raise ValueError("snapshot exceeds 64 KiB")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO samples VALUES (?, ?)", (at, payload))
            self.db.execute("DELETE FROM samples WHERE at < ?", (at - DAY_MS,))
            self.db.execute("DELETE FROM samples WHERE at NOT IN (SELECT at FROM samples ORDER BY at DESC LIMIT 1440)")
            while self.db.execute("SELECT COALESCE(SUM(length(payload)), 0) FROM samples").fetchone()[0] > MAX_QUEUE_BYTES:
                self.db.execute("DELETE FROM samples WHERE at = (SELECT MIN(at) FROM samples)")

    def flush(self, config, sender=send):
        # Bound recovery traffic; another minute continues draining the backlog.
        for _ in range(6):
            rows = self.db.execute("SELECT at, payload FROM samples ORDER BY at LIMIT 10").fetchall()
            if not rows:
                return
            sender(config, [json.loads(row[1]) for row in rows])
            with self.db:
                self.db.executemany("DELETE FROM samples WHERE at = ?", [(row[0],) for row in rows])

    def close(self):
        self.db.close()


def run(config, state_directory, stop, once=False):
    queue = Queue(state_directory)
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                queue.append(int(time.time() * 1000), collect())
            except (OSError, ValueError, KeyError):
                LOG.warning("Could not collect Linux resources")
            try:
                queue.flush(config)
            except (OSError, ValueError, urllib.error.URLError):
                LOG.warning("Upload failed; samples remain queued for retry")
            if once:
                return
            stop.wait(max(0, 60 - (time.monotonic() - started)))
    finally:
        queue.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-dir", default=os.environ.get("STATE_DIRECTORY", "/var/lib/briareus-monitor"))
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    os.umask(0o077)
    config = load_config(args.config)
    if args.check_config:
        return
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    run(config, args.state_dir, stop, args.once)


if __name__ == "__main__":
    main()

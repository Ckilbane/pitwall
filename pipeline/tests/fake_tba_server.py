#!/usr/bin/env python3
"""A local stand-in for the TBA v3 API, for tests (no network).

Serves one season dump (the loader's raw-dump shape, e.g. tba_2025.json.gz; parsed with json only,
treated as untrusted data) at /api/v3/...:

    events/{y}                       dump["events"]
    event/{k}/matches|rankings|alliances   dump["event_data"][k][...]   (404 when absent)
    event/{k}/teams/keys             dump["event_data"][k]["teams"] as ["frcN"]   (404 when absent)
    teams/{y}/{page}                 dump["teams"] bucketed number // 500 (an empty list past the end)
    districts/{y}, district/{d}/teams/keys   dump["districts"] (404 when absent)

Every 200 carries a strong ETag (sha1 of the body) and honours If-None-Match with 304.  Requires the
header X-TBA-Auth-Key to equal `key`, else 401.  Test controls: `mutate(fn)` edits the dump in place,
`fail(prefix, status, times)` injects errors, `log` lists every request, `max_inflight` records the peak
number of simultaneous requests, `starts` the monotonic arrival times.  Responses are gzip-encoded when
the client sends Accept-Encoding: gzip.

Standalone: python3 -I fake_tba_server.py DUMP.json.gz [--port N] [--key K]
"""
import gzip
import hashlib
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple


class FakeTBA:
    def __init__(self, dump: Dict[str, Any], key: str = "test-key-0000", delay: float = 0.0):
        self.dump = dump
        self.key = key
        self.delay = delay
        self.log: List[Dict[str, Any]] = []
        self.starts: List[float] = []
        self.max_inflight = 0
        self._inflight = 0
        self._lock = threading.Lock()
        self._fail: List[Dict[str, Any]] = []
        self.bad_key_seen = 0
        self.keys_received: List[str] = []
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    # -- controls -----------------------------------------------------------------------------
    def mutate(self, fn) -> None:
        with self._lock:
            fn(self.dump)

    def fail(self, prefix: str, status: int, times: int = 1_000_000) -> None:
        with self._lock:
            self._fail.append({"prefix": prefix, "status": status, "left": times})

    def clear_failures(self) -> None:
        with self._lock:
            self._fail.clear()

    def reset_log(self) -> None:
        with self._lock:
            self.log.clear()
            self.starts.clear()
            self.max_inflight = 0

    def requests_for(self, prefix: str) -> List[Dict[str, Any]]:
        return [r for r in self.log if r["path"].startswith(prefix)]

    # -- routing ------------------------------------------------------------------------------
    def _route(self, path: str) -> Tuple[int, Any]:
        d = self.dump
        parts = path.split("/")
        if parts[0] == "events" and len(parts) == 2:
            return 200, d["events"]
        if parts[0] == "event" and len(parts) >= 3:
            ed = d["event_data"].get(parts[1])
            tail = "/".join(parts[2:])
            if ed is None:
                return 404, None
            if tail in ("matches", "rankings", "alliances"):
                return (200, ed[tail]) if tail in ed else (404, None)
            if tail == "teams/keys":
                if "teams" not in ed:
                    return 404, None
                return 200, ["frc" + str(t).replace("frc", "") for t in ed["teams"]]
        if parts[0] == "teams" and len(parts) == 3:
            page = int(parts[2])
            return 200, [t for t in d.get("teams", []) if int(t["key"][3:]) // 500 == page]
        if parts[0] == "districts" and len(parts) == 2 and "districts" in d:
            return 200, [{"key": f"{parts[1]}{a}", "abbreviation": a} for a in d["districts"]]
        if parts[0] == "district" and len(parts) == 4 and "districts" in d:
            abbr = parts[1][4:]
            if abbr in d["districts"]:
                return 200, ["frc" + str(t).replace("frc", "") for t in d["districts"][abbr]]
        return 404, None

    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        t0 = time.monotonic()
        with self._lock:
            self._inflight += 1
            self.max_inflight = max(self.max_inflight, self._inflight)
            self.starts.append(t0)
        try:
            if self.delay:
                time.sleep(self.delay)
            raw_path = handler.path.split("?", 1)[0]
            m = re.match(r"^/api/v3/(.+)$", raw_path)
            path = m.group(1) if m else raw_path.lstrip("/")
            sent_key = handler.headers.get("X-TBA-Auth-Key")
            rec = {"path": path, "inm": handler.headers.get("If-None-Match"), "status": None,
                   "ua": handler.headers.get("User-Agent"), "key_ok": sent_key == self.key,
                   "query": "?" in handler.path}
            with self._lock:
                self.log.append(rec)
                if sent_key is not None:
                    self.keys_received.append(sent_key)
            if sent_key != self.key:
                self.bad_key_seen += 1
                rec["status"] = 401
                return self._send(handler, 401, b'{"Error": "X-TBA-Auth-Key is invalid."}')
            inj = None
            with self._lock:
                for f in self._fail:
                    if path.startswith(f["prefix"]) and f["left"] > 0:
                        f["left"] -= 1
                        inj = f["status"]
                        break
            if inj:
                rec["status"] = inj
                return self._send(handler, inj, b'{"Error": "injected"}')
            status, obj = self._route(path)
            if status == 404:
                rec["status"] = 404
                return self._send(handler, 404, b'{"Error": "not found"}')
            body = json.dumps(obj, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            etag = '"' + hashlib.sha1(body).hexdigest() + '"'
            if handler.headers.get("If-None-Match") == etag:
                rec["status"] = 304
                handler.send_response(304)
                handler.send_header("ETag", etag)
                handler.end_headers()
                return
            rec["status"] = 200
            return self._send(handler, 200, body, etag)
        finally:
            with self._lock:
                self._inflight -= 1

    @staticmethod
    def _send(handler, status: int, body: bytes, etag: Optional[str] = None) -> None:
        gz = "gzip" in (handler.headers.get("Accept-Encoding") or "")
        payload = gzip.compress(body) if gz and status == 200 else body
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        if gz and status == 200:
            handler.send_header("Content-Encoding", "gzip")
        if etag:
            handler.send_header("ETag", etag)
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    # -- lifecycle ----------------------------------------------------------------------------
    def start(self, port: int = 0) -> str:
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802
                outer.handle(self)

            def log_message(self, *a, **k):  # silence
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/api/v3"

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


def load_dump(path: str) -> Dict[str, Any]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:  # type: ignore
        return json.load(f)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--key", default="test-key-0000")
    a = ap.parse_args()
    srv = FakeTBA(load_dump(a.dump), a.key)
    print(srv.start(a.port), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()

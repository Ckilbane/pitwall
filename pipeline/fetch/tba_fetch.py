#!/usr/bin/env python3
"""Incremental, polite TBA fetcher with an ETag cache (SPEC 2.1, 2.4).

    TBA_KEY=... python pipeline/fetch/tba_fetch.py --cache cache/raw --config pipeline/config.json \
        --mode live|daily|full [--seasons 2025 2026] --report fetch_report.json

Every endpoint is requested with `If-None-Match` when a cached body and ETag exist; 304 keeps the
cached body, 200 stores the new body (verbatim bytes, gzip 9 mtime 0) and ETag, 404 stores the body
`null`.  Other errors are retried 3 times with 2/4/8 s backoff, then the cached body is kept and the
URL goes into the report's `errors`.  At most 4 requests are in flight and request starts are at
least 50 ms apart.  List order is never touched: bodies are stored exactly as TBA sent them.

The API key comes only from the TBA_KEY environment variable and is sent only as the
`X-TBA-Auth-Key` header.  It is never printed, put in a URL, written to a file or included in an
exception message (see `Fetcher.redact`).  HTTP 401/403 stop the run at once with a clear message.

Exit codes: 0 done (errors, if any, are in the report); 2 usage or missing key; 3 key rejected.
"""
import argparse
import datetime as dt
import gzip
import http.client
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fetch import cachelib  # noqa: E402
from fetch.eventfilter import wanted_events  # noqa: E402

DEFAULT_BASE_URL = "https://www.thebluealliance.com/api/v3"
MAX_BODY_BYTES = 128 * 1024 * 1024
MODES = ("live", "daily", "full")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_TEAM_PAGES = 60


class AuthError(Exception):
    """TBA rejected the key (401/403)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: urllib would forward the auth header to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


class RateGate:
    """Guarantees at least `min_interval` seconds between request starts, across threads."""

    def __init__(self, min_interval: float, clock=time.monotonic, sleep=time.sleep):
        self.min_interval = float(min_interval)
        self._lock = threading.Lock()
        self._next = 0.0
        self._clock, self._sleep = clock, sleep

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + self.min_interval
        if start > now:
            self._sleep(start - now)


class Result:
    __slots__ = ("api", "outcome", "status", "message", "rel")

    def __init__(self, api: str, rel: str, outcome: str, status: Optional[int] = None, message: str = ""):
        self.api, self.rel, self.outcome, self.status, self.message = api, rel, outcome, status, message

    @property
    def changed(self) -> bool:
        return self.outcome in ("new", "changed")


def utc_now_iso() -> str:
    return os.environ.get("PITWALL_NOW") or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Fetcher:
    def __init__(
        self,
        raw_dir: str,
        base_url: str,
        key: str,
        user_agent: str,
        max_inflight: int = 4,
        min_interval: float = 0.05,
        retries: int = 3,
        backoff_base: float = 2.0,
        timeout: float = 30.0,
        now_iso: Optional[str] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.raw_dir = raw_dir
        self.base_url = base_url.rstrip("/")
        self._key = key
        self.user_agent = user_agent
        self.max_inflight = max(1, int(max_inflight))
        self.retries = int(retries)
        self.backoff_base = float(backoff_base)
        self.timeout = float(timeout)
        self.now_iso = now_iso or utc_now_iso()
        self._sleep = sleep
        self.gate = RateGate(min_interval, sleep=sleep)
        self.etags = cachelib.load_etags(raw_dir)
        self._lock = threading.Lock()
        self.abort = threading.Event()
        self.auth_status: Optional[int] = None
        self.http_attempts = 0
        self._inflight = 0
        self.max_inflight_seen = 0
        self.results: List[Result] = []
        host = urllib.parse.urlsplit(self.base_url).hostname or ""
        handlers: List[Any] = [_NoRedirect()]
        if host in LOOPBACK_HOSTS:
            handlers.insert(0, urllib.request.ProxyHandler({}))
        self._opener = urllib.request.build_opener(*handlers)

    # -- safety -------------------------------------------------------------------------------
    def redact(self, text: Any) -> str:
        s = str(text)
        if self._key:
            s = s.replace(self._key, "***")
        return s

    # -- one HTTP attempt ---------------------------------------------------------------------
    def _http(self, api: str, etag: Optional[str]) -> Tuple[int, Optional[bytes], Optional[str]]:
        """One request.  Returns (status, body bytes or None, etag).  Raises AuthError on 401/403 and
        `_Retryable` for errors worth retrying."""
        url = self.base_url + "/" + api
        headers = {
            "X-TBA-Auth-Key": self._key,
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }
        if etag:
            headers["If-None-Match"] = etag
        req = urllib.request.Request(url, headers=headers, method="GET")
        self.gate.wait()
        with self._lock:
            self.http_attempts += 1
            self._inflight += 1
            self.max_inflight_seen = max(self.max_inflight_seen, self._inflight)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                status = resp.status
                body = _read_body(resp)
                return status, body, resp.headers.get("ETag")
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                body = _read_body(e) if status == 404 else None
            except Exception:  # noqa: BLE001
                body = None
            if status == 304:
                return 304, None, e.headers.get("ETag") if e.headers else None
            if status == 404:
                return 404, body, None
            if status in (401, 403):
                raise AuthError(status)
            retry_after = None
            if e.headers and e.headers.get("Retry-After"):
                try:
                    retry_after = min(float(e.headers["Retry-After"]), 60.0)
                except ValueError:
                    retry_after = None
            if status == 429 or status >= 500:
                raise _Retryable(f"HTTP {status}", retry_after)
            raise _Fatal(f"HTTP {status}")
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
                http.client.HTTPException, OSError, zlib.error, EOFError) as e:
            raise _Retryable(f"{type(e).__name__}: {self.redact(e)}"[:200], None)
        finally:
            with self._lock:
                self._inflight -= 1

    # -- one endpoint, with cache, retries, bookkeeping ---------------------------------------
    def get(self, api: str, rel: str) -> Result:
        if self.abort.is_set():
            return Result(api, rel, "skipped", message="run aborted")
        path = cachelib.abs_path(self.raw_dir, rel)
        have_file = os.path.isfile(path)
        with self._lock:
            entry = dict(self.etags.get(api) or {})
        etag = entry.get("etag") if have_file else None

        last_msg = ""
        for attempt in range(self.retries + 1):
            if self.abort.is_set():
                return Result(api, rel, "skipped", message="run aborted")
            try:
                status, body, new_etag = self._http(api, etag)
            except AuthError as e:
                self.auth_status = int(e.args[0])
                self.abort.set()
                return Result(api, rel, "error", self.auth_status, "key rejected")
            except _Fatal as e:
                return self._error(api, rel, None, str(e))
            except _Retryable as e:
                last_msg = str(e)
                if attempt < self.retries:
                    wait = e.retry_after if e.retry_after is not None else self.backoff_base * (2 ** attempt)
                    self._sleep(wait)
                    continue
                return self._error(api, rel, None, f"{last_msg} (after {self.retries} retries)")
            if status == 304:
                if not have_file:  # cache lost: ask again unconditionally
                    etag = None
                    have_file = False
                    continue
                return self._record(Result(api, rel, "not_modified", 304))
            if status == 404:
                return self._store(api, rel, path, entry, b"null", None, 404, "not_found")
            # 200
            assert body is not None
            try:
                json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                last_msg = "response is not valid JSON"
                if attempt < self.retries:
                    self._sleep(self.backoff_base * (2 ** attempt))
                    continue
                return self._error(api, rel, status, last_msg)
            return self._store(api, rel, path, entry, body, new_etag, 200, "ok")
        return self._error(api, rel, None, last_msg or "failed")

    def _store(self, api, rel, path, old, body: bytes, etag, status, kind) -> Result:
        sha = cachelib.sha256_hex(body)
        same = bool(old) and old.get("sha256") == sha and os.path.isfile(path)
        if same:
            outcome = "not_found" if kind == "not_found" else "same_body"
            fetched_at = old.get("fetched_at") or self.now_iso
        else:
            outcome = "not_found" if kind == "not_found" else ("changed" if old else "new")
            fetched_at = self.now_iso
            cachelib.write_if_changed(path, cachelib.gz_bytes(body))
        new_entry = {"etag": etag, "status": status, "fetched_at": fetched_at, "sha256": sha}
        with self._lock:
            self.etags[api] = new_entry
        r = Result(api, rel, outcome, status)
        r.message = "changed" if (not same) else ""
        if kind == "not_found" and not same:
            r.outcome = "not_found_changed"
        return self._record(r)

    def _error(self, api, rel, status, msg) -> Result:
        return self._record(Result(api, rel, "error", status, self.redact(msg)))

    def _record(self, r: Result) -> Result:
        with self._lock:
            self.results.append(r)
        return r

    def save(self) -> None:
        with self._lock:
            cachelib.save_etags(self.raw_dir, self.etags)

    def pool(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=self.max_inflight, thread_name_prefix="tba")


class _Retryable(Exception):
    def __init__(self, msg: str, retry_after: Optional[float]):
        super().__init__(msg)
        self.retry_after = retry_after


class _Fatal(Exception):
    pass


def _read_body(resp) -> bytes:
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    raw = resp.read(MAX_BODY_BYTES + 1)
    if len(raw) > MAX_BODY_BYTES:
        raise _Retryable("response too large", None)
    if enc == "gzip":
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = d.decompress(raw, MAX_BODY_BYTES + 1)
        if len(out) > MAX_BODY_BYTES:
            raise _Retryable("decompressed response too large", None)
        return out
    return raw


# ---------------------------------------------------------------------------------------------
# What to fetch
# ---------------------------------------------------------------------------------------------
def _parse_date(s: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def published_event_status(published_dir: Optional[str], year: int) -> Dict[str, str]:
    """{event_key: status} from the last published season.json.gz (events table), or {}."""
    if not published_dir:
        return {}
    path = os.path.join(published_dir, str(year), "season.json.gz")
    try:
        with gzip.open(path, "rb") as f:
            d = json.loads(f.read().decode("utf-8"))
        cols = d["events"]["cols"]
        return {k: s for k, s in zip(cols["key"], cols["status"]) if isinstance(k, str)}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def select_live_events(raw_dir: str, year: int, wanted: List[Dict[str, Any]], today: dt.date,
                       built_status: Dict[str, str]) -> List[str]:
    """Events a `live` run refreshes: active window, built-as-not-Completed after the end date,
    or never fetched (no cached matches body)."""
    out: List[str] = []
    for ev in wanted:
        key = ev["key"]
        start, end = _parse_date(ev.get("start_date")), _parse_date(ev.get("end_date"))
        never = not os.path.isfile(cachelib.abs_path(raw_dir, f"{year}/event/{key}/matches.json.gz"))
        if start is None or end is None:
            out.append(key)
        elif start - dt.timedelta(days=1) <= today <= end + dt.timedelta(days=2):
            out.append(key)
        elif end < today and key in built_status and built_status[key] != "Completed":
            out.append(key)
        elif never:
            out.append(key)
    return out


class SeasonStats:
    def __init__(self, year: int):
        self.year = year
        self.requested = 0
        self.changed: List[str] = []
        self.not_modified = 0
        self.not_found = 0
        self.errors: List[Dict[str, Any]] = []
        self.changed_events: List[str] = []
        self.events_refreshed = 0
        self.skipped_event_keys: List[str] = []
        self.fingerprint: Optional[Dict[str, Any]] = None

    def add(self, r: Result) -> None:
        if r.outcome == "skipped":
            return
        self.requested += 1
        if r.outcome in ("new", "changed", "not_found_changed"):
            self.changed.append(r.rel)
            parts = r.rel.split("/")
            if len(parts) >= 4 and parts[1] == "event":
                self.changed_events.append(parts[2])
        elif r.outcome == "not_modified":
            self.not_modified += 1
        if r.outcome in ("not_found", "not_found_changed"):
            self.not_found += 1
        if r.outcome == "error":
            self.errors.append({"url": r.api, "status": r.status, "message": r.message})

    def as_dict(self) -> Dict[str, Any]:
        return {
            "requested": self.requested,
            "changed": len(self.changed),
            "not_modified": self.not_modified,
            "not_found": self.not_found,
            "errors": len(self.errors),
            "events_refreshed": self.events_refreshed,
            "changed_events": sorted(set(self.changed_events)),
            "changed_files": sorted(self.changed),
            "skipped_event_keys": self.skipped_event_keys,
            "inputs_hash": (self.fingerprint or {}).get("inputs_hash"),
            "last_change_at": (self.fingerprint or {}).get("last_change_at"),
        }


def _cached_json(raw_dir: str, rel: str) -> Any:
    ok, body = cachelib.read_body(cachelib.abs_path(raw_dir, rel))
    return body if ok else None


def fetch_season(F: Fetcher, year: int, mode: str, current: int, today: dt.date,
                 published_dir: Optional[str]) -> SeasonStats:
    st = SeasonStats(year)
    raw = F.raw_dir
    is_current = year == current

    def run_all(items: List[Tuple[str, str]]) -> List[Result]:
        if not items:
            return []
        with F.pool() as ex:
            res = list(ex.map(lambda it: F.get(*it), items))
        for r in res:
            st.add(r)
        return res

    # 1. the event list
    api, rel = cachelib.endpoint_events(year)
    r = F.get(api, rel)
    st.add(r)
    events = _cached_json(raw, rel)
    wanted, bad = wanted_events(year, events)
    st.skipped_event_keys = bad
    if not wanted and not isinstance(events, list):
        return st  # nothing usable (error and no cache): the report carries the error

    # 2. per-event endpoints
    if mode == "live":
        keys = select_live_events(raw, year, wanted, today, published_event_status(published_dir, year))
    else:
        keys = [e["key"] for e in wanted]
    st.events_refreshed = len(keys)
    items: List[Tuple[str, str]] = []
    for k in keys:
        for what in cachelib.EVENT_ENDPOINTS:
            if what == "teams" and not is_current:
                continue
            items.append(cachelib.endpoint_event(year, k, what))
    run_all(items)

    # 3. districts and their team lists (current season only)
    if is_current:
        d_api, d_rel = cachelib.endpoint_districts(year)
        have = os.path.isfile(cachelib.abs_path(raw, d_rel))
        if mode != "live" or not have:
            st.add(F.get(d_api, d_rel))
        districts = _cached_json(raw, d_rel)
        d_items: List[Tuple[str, str]] = []
        if isinstance(districts, list):
            for d in districts:
                dkey = d.get("key") if isinstance(d, dict) else None
                if not cachelib.valid_district_key(dkey, year):
                    continue
                a, rr = cachelib.endpoint_district_teams(year, dkey)
                if mode != "live" or not os.path.isfile(cachelib.abs_path(raw, rr)):
                    d_items.append((a, rr))
        run_all(d_items)

    # 4. team pages: 0.. until an empty page (live: only when nothing is cached yet)
    p0 = cachelib.endpoint_teams_page(year, 0)[1]
    if mode != "live" or not os.path.isfile(cachelib.abs_path(raw, p0)):
        for page in range(MAX_TEAM_PAGES):
            a, rr = cachelib.endpoint_teams_page(year, page)
            r = F.get(a, rr)
            st.add(r)
            body = _cached_json(raw, rr)
            if r.outcome == "error" or not isinstance(body, list) or len(body) == 0:
                break
    return st


def select_seasons(config: Dict[str, Any], mode: str, requested: Optional[List[int]]) -> List[int]:
    configured = [int(s) for s in config["seasons"]]
    if requested:
        bad = [s for s in requested if s not in configured]
        if bad:
            raise ValueError(f"seasons not in config: {bad}")
        return sorted(set(requested))
    if mode == "full":
        return sorted(configured)
    return [int(config["current_season"])]


def check_base_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    if p.username or p.password or not p.hostname:
        raise ValueError("base URL must not contain credentials and must have a host")
    if p.scheme == "https" or (p.scheme == "http" and p.hostname in LOOPBACK_HOSTS):
        return url.rstrip("/")
    raise ValueError("base URL must be https (http is allowed only for localhost tests)")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", required=True, help="raw cache directory (cache/raw)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--mode", required=True, choices=MODES)
    ap.add_argument("--seasons", nargs="*", type=int, default=None)
    ap.add_argument("--report", required=True)
    ap.add_argument("--published", default=None,
                    help="published data tree (default: sibling 'published' of --cache)")
    ap.add_argument("--base-url", default=os.environ.get("TBA_BASE_URL") or DEFAULT_BASE_URL,
                    help="TBA API v3 base URL (env TBA_BASE_URL); tests point this at a local fake server")
    ap.add_argument("--today", default=None, help="UTC date YYYY-MM-DD (tests)")
    ap.add_argument("--max-inflight", type=int, default=4)
    ap.add_argument("--min-interval", type=float, default=0.05, help="seconds between request starts")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--backoff-base", type=float, default=2.0, help="first retry wait; then x2, x4")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args(argv)

    key = (os.environ.get("TBA_KEY") or "").strip()
    if not key:
        print("ERROR: TBA_KEY is not set. In GitHub, add it under Settings > Secrets and variables > "
              "Actions > Repository secrets, named exactly TBA_KEY.", file=sys.stderr)
        return 2
    try:
        base_url = check_base_url(args.base_url)
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
        seasons = select_seasons(config, args.mode, args.seasons)
        today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.timezone.utc).date()
    except (ValueError, OSError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    ua = "PitWall-fetcher/1.0 (FRC ratings site; GitHub Actions" + (f"; {repo}" if repo else "") + ")"
    raw_dir = args.cache
    os.makedirs(raw_dir, exist_ok=True)
    published = args.published or os.path.join(os.path.dirname(os.path.abspath(raw_dir)), "published")
    started = utc_now_iso()
    F = Fetcher(raw_dir, base_url, key, ua, args.max_inflight, args.min_interval, args.retries,
                args.backoff_base, args.timeout, now_iso=started)
    host = urllib.parse.urlsplit(base_url).netloc
    print(f"fetch: mode={args.mode} seasons={seasons} host={host}", flush=True)

    stats: Dict[int, SeasonStats] = {}
    t0 = time.monotonic()
    for y in seasons:
        if F.abort.is_set():
            break
        st = fetch_season(F, y, args.mode, int(config["current_season"]), today, published)
        stats[y] = st
        if not F.abort.is_set():
            st.fingerprint = cachelib.refresh_fingerprint(raw_dir, y, started, bool(st.changed))
        print(f"  [{y}] requested {st.requested}  changed {len(st.changed)}  304 {st.not_modified}  "
              f"404 {st.not_found}  errors {len(st.errors)}", flush=True)
    F.save()

    requested = sum(s.requested for s in stats.values())
    errors = sum(len(s.errors) for s in stats.values())
    report = {
        "schema": 1,
        "mode": args.mode,
        "seasons": seasons,
        "started_at": started,
        "finished_at": utc_now_iso(),
        "elapsed_s": round(time.monotonic() - t0, 2),
        "http_attempts": F.http_attempts,
        "max_inflight": F.max_inflight_seen,
        "requested_endpoints": requested,
        "changed_endpoints": sum(len(s.changed) for s in stats.values()),
        "errors_total": errors,
        "error_ratio": round(errors / requested, 4) if requested else 0.0,
        "aborted": "auth" if F.auth_status else None,
        "errors": [dict(e, season=y) for y, s in stats.items() for e in s.errors],
        "per_season": {str(y): s.as_dict() for y, s in stats.items()},
    }
    cachelib.dump_small_json(report, args.report, indent=1)

    if F.auth_status:
        print(f"ERROR: The Blue Alliance rejected the API key (HTTP {F.auth_status}). Check that the "
              "repository secret TBA_KEY exists and holds a valid TBA read key (https://www.thebluealliance.com/account). "
              "Stopped before fetching anything further.", file=sys.stderr)
        return 3
    print(f"fetch: done, {requested} endpoints, {report['changed_endpoints']} changed, {errors} errors, "
          f"{report['elapsed_s']}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Raw TBA cache on disk (SPEC 2.4): paths, gzip blobs, etags.json, fingerprints, inputs hash.

Layout under the cache root `raw/` (this module takes that directory as `raw_dir`):

    etags.json                                  {url: {etag, status, fetched_at, sha256}}
    {y}/events.json.gz                          events/{y}
    {y}/event/{key}/matches.json.gz             event/{key}/matches
    {y}/event/{key}/rankings.json.gz            event/{key}/rankings
    {y}/event/{key}/alliances.json.gz           event/{key}/alliances
    {y}/event/{key}/teams.json.gz               event/{key}/teams/keys      (current season only)
    {y}/districts.json.gz                       districts/{y}               (current season only)
    {y}/district/{dkey}/teams.json.gz           district/{dkey}/teams/keys  (current season only)
    {y}/teams/{page}.json.gz                    teams/{y}/{page}
    {y}/fingerprint.json                        {inputs_hash, endpoint_count, last_change_at}

`url` in etags.json is the API path without host and without the /api/v3 prefix, e.g.
"event/2025abc/matches".  Bodies are the verbatim bytes TBA returned (after transport
decompression), gzip level 9 with mtime 0, so an unchanged body gives an identical blob.
A 404 is stored as the body `null`.  Nothing here ever sees the API key.

Everything read from a cache file is untrusted data: json only, and keys that become path
components are validated with EVENT_KEY_RE / DISTRICT_KEY_RE before use.
"""
import gzip
import hashlib
import json
import os
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

EVENT_KEY_RE = re.compile(r"^[0-9]{4}[a-z0-9]+$")
DISTRICT_KEY_RE = re.compile(r"^[0-9]{4}[a-z0-9]+$")
TEAM_KEY_RE = re.compile(r"^frc[0-9]+$")

NULL_BODY = b"null"

EVENT_ENDPOINTS = ("matches", "rankings", "alliances", "teams")  # teams: current season only


# ---------------------------------------------------------------------------------------------
# gzip and file helpers
# ---------------------------------------------------------------------------------------------
def gz_bytes(data: bytes) -> bytes:
    """Deterministic gzip: level 9, header mtime 0."""
    return gzip.compress(data, compresslevel=9, mtime=0)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_if_changed(path: str, data: bytes) -> bool:
    """Write bytes unless the file already holds exactly them.  Returns True if written."""
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return True


def read_gz_bytes(path: str) -> Optional[bytes]:
    try:
        with gzip.open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def read_body(path: str) -> Tuple[bool, Any]:
    """(exists, parsed JSON) of a cached body.  `null` bodies parse to None."""
    raw = read_gz_bytes(path)
    if raw is None:
        return False, None
    return True, json.loads(raw.decode("utf-8"))


def dump_small_json(obj: Any, path: str, indent: Optional[int] = None) -> bool:
    text = json.dumps(obj, sort_keys=True, indent=indent, separators=None if indent else (",", ":"))
    return write_if_changed(path, (text + "\n").encode("utf-8"))


def load_json_file(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


# ---------------------------------------------------------------------------------------------
# key validation and path mapping
# ---------------------------------------------------------------------------------------------
def valid_event_key(key: Any, year: int) -> bool:
    return isinstance(key, str) and bool(EVENT_KEY_RE.match(key)) and key.startswith(str(year))


def valid_district_key(key: Any, year: int) -> bool:
    return isinstance(key, str) and bool(DISTRICT_KEY_RE.match(key)) and key.startswith(str(year))


def season_dir(raw_dir: str, year: int) -> str:
    return os.path.join(raw_dir, str(int(year)))


def endpoint_events(year: int) -> Tuple[str, str]:
    y = int(year)
    return f"events/{y}", f"{y}/events.json.gz"


def endpoint_event(year: int, key: str, what: str) -> Tuple[str, str]:
    if not valid_event_key(key, year) or what not in EVENT_ENDPOINTS:
        raise ValueError(f"bad event endpoint {key!r} {what!r}")
    y = int(year)
    api = f"event/{key}/teams/keys" if what == "teams" else f"event/{key}/{what}"
    return api, f"{y}/event/{key}/{what}.json.gz"


def endpoint_districts(year: int) -> Tuple[str, str]:
    y = int(year)
    return f"districts/{y}", f"{y}/districts.json.gz"


def endpoint_district_teams(year: int, dkey: str) -> Tuple[str, str]:
    if not valid_district_key(dkey, year):
        raise ValueError(f"bad district key {dkey!r}")
    return f"district/{dkey}/teams/keys", f"{int(year)}/district/{dkey}/teams.json.gz"


def endpoint_teams_page(year: int, page: int) -> Tuple[str, str]:
    y, p = int(year), int(page)
    if p < 0:
        raise ValueError("bad page")
    return f"teams/{y}/{p}", f"{y}/teams/{p}.json.gz"


def abs_path(raw_dir: str, rel: str) -> str:
    return os.path.join(raw_dir, *rel.split("/"))


# ---------------------------------------------------------------------------------------------
# etags.json
# ---------------------------------------------------------------------------------------------
def etags_path(raw_dir: str) -> str:
    return os.path.join(raw_dir, "etags.json")


def load_etags(raw_dir: str) -> Dict[str, Dict[str, Any]]:
    d = load_json_file(etags_path(raw_dir), {})
    return d if isinstance(d, dict) else {}


def save_etags(raw_dir: str, etags: Dict[str, Dict[str, Any]]) -> bool:
    return dump_small_json(etags, etags_path(raw_dir), indent=1)


# ---------------------------------------------------------------------------------------------
# season file inventory, inputs hash, fingerprint
# ---------------------------------------------------------------------------------------------
def season_body_files(raw_dir: str, year: int) -> List[str]:
    """Relative paths (to raw_dir, '/'-separated, sorted) of every cached endpoint body of the season."""
    base = season_dir(raw_dir, year)
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for fn in sorted(filenames):
            if not fn.endswith(".json.gz"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), raw_dir).replace(os.sep, "/")
            out.append(rel)
    return sorted(out)


def season_options(raw_dir: str, year: int) -> Dict[str, bool]:
    """Loader inclusion flags, derived from what the season's cache holds (see assemble.py).

    The fetcher only ever caches districts and event team lists for the current season, so for
    past seasons both are False and their builds stay identical to the validated runs.
    """
    y = int(year)
    districts = _has_real_body(raw_dir, endpoint_districts(y)[1])
    event_teams = False
    ev = os.path.join(season_dir(raw_dir, y), "event")
    if os.path.isdir(ev):
        for key in sorted(os.listdir(ev)):
            if _has_real_body(raw_dir, f"{y}/event/{key}/teams.json.gz"):
                event_teams = True
                break
    return {"districts": districts, "event_teams": event_teams}


def _has_real_body(raw_dir: str, rel: str) -> bool:
    ok, body = read_body(abs_path(raw_dir, rel))
    return ok and body is not None


def compute_inputs_hash(raw_dir: str, year: int) -> Tuple[str, int]:
    """(inputs_hash, endpoint_count) per SPEC 2.4: sha256 of the sorted lines
    "{relative_path} {sha256(body)}" over every cached endpoint of the season, plus the loader
    option flags.  Hashes the decompressed body, so gzip implementation differences never
    change it."""
    lines: List[str] = []
    for rel in season_body_files(raw_dir, year):
        raw = read_gz_bytes(abs_path(raw_dir, rel))
        lines.append(f"{rel} {sha256_hex(raw if raw is not None else b'')}")
    opts = season_options(raw_dir, year)
    lines.append(f"option districts={int(opts['districts'])}")
    lines.append(f"option event_teams={int(opts['event_teams'])}")
    lines.sort()
    digest = sha256_hex(("\n".join(lines) + "\n").encode("utf-8"))
    return digest, len(lines) - 2


def fingerprint_path(raw_dir: str, year: int) -> str:
    return os.path.join(season_dir(raw_dir, year), "fingerprint.json")


def load_fingerprint(raw_dir: str, year: int) -> Optional[Dict[str, Any]]:
    d = load_json_file(fingerprint_path(raw_dir, year))
    return d if isinstance(d, dict) and "inputs_hash" in d else None


def refresh_fingerprint(raw_dir: str, year: int, now_iso: str, changed: bool) -> Dict[str, Any]:
    """Recompute the season fingerprint.  `last_change_at` moves to `now_iso` only if the inputs
    hash differs from the stored one (or `changed` and none stored); otherwise it is kept."""
    h, n = compute_inputs_hash(raw_dir, year)
    old = load_fingerprint(raw_dir, year)
    if old and old.get("inputs_hash") == h:
        last = old.get("last_change_at") or now_iso
    else:
        last = now_iso
    fp = {"inputs_hash": h, "endpoint_count": n, "last_change_at": last}
    if old != fp:
        dump_small_json(fp, fingerprint_path(raw_dir, year), indent=1)
    return fp


def seasons_in_cache(raw_dir: str) -> List[int]:
    out = []
    if os.path.isdir(raw_dir):
        for name in os.listdir(raw_dir):
            if re.fullmatch(r"[0-9]{4}", name) and os.path.isfile(
                abs_path(raw_dir, endpoint_events(int(name))[1])
            ):
                out.append(int(name))
    return sorted(out)


def iter_event_keys(raw_dir: str, year: int) -> Iterator[str]:
    """Event keys that have any cached endpoint file, in sorted order (validated)."""
    ev = os.path.join(season_dir(raw_dir, year), "event")
    if not os.path.isdir(ev):
        return
    for key in sorted(os.listdir(ev)):
        if valid_event_key(key, year):
            yield key

#!/usr/bin/env python3
"""Raw TBA cache <-> the loader's raw-dump shape (SPEC 2.4, sb/loader.py docstring).

    from fetch.assemble import season_dump
    dump = season_dump("cache/raw", 2026)   # {"year", "fetched", "events", "event_data", "teams", ["districts"]}

CLI:
    assemble.py --split tba_2023.json.gz --cache DIR     seed a cache from an old dump (tests, bootstrapping)
    assemble.py --dump 2023 --cache DIR --out FILE.gz     write the season's dump file (feed it to run.py --raw)

`--cache` may be the raw directory itself or its parent (a directory containing `raw/`).

Order is never changed: events keep `/events/{y}` order, matches keep `/event/{key}/matches` order.
`oprs`, present in the old dumps, is not part of the cache (nothing reads it) and is dropped by --split.
"""
import argparse
import gzip
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fetch import cachelib  # noqa: E402

TEAM_PAGE_SIZE = 500  # sb.shim buckets dump teams as number // 500, like TBA's /teams/{page}
_ABBR_RE = re.compile(r"^[a-z0-9]+$")


def resolve_raw_dir(cache: str) -> str:
    sub = os.path.join(cache, "raw")
    if os.path.isdir(sub):
        return sub
    return cache


def _body(raw_dir: str, rel: str) -> Any:
    ok, body = cachelib.read_body(cachelib.abs_path(raw_dir, rel))
    return body if ok else None


def _team_pages(raw_dir: str, year: int) -> List[int]:
    d = os.path.join(cachelib.season_dir(raw_dir, year), "teams")
    pages = []
    if os.path.isdir(d):
        for fn in os.listdir(d):
            m = re.fullmatch(r"([0-9]+)\.json\.gz", fn)
            if m:
                pages.append(int(m.group(1)))
    return sorted(pages)


def season_dump(cache: str, year: int) -> Dict[str, Any]:
    """Build the loader's raw dump for `year` from the cache.  Deterministic for a given cache."""
    raw_dir = resolve_raw_dir(cache)
    year = int(year)
    ev_rel = cachelib.endpoint_events(year)[1]
    events = _body(raw_dir, ev_rel)
    if not isinstance(events, list):
        raise FileNotFoundError(f"no cached /events/{year} body under {raw_dir}")
    fp = cachelib.load_fingerprint(raw_dir, year)
    if not fp or not fp.get("last_change_at"):
        raise FileNotFoundError(f"no fingerprint for {year}: run the fetcher (or --split) first")

    event_data: Dict[str, Dict[str, Any]] = {}
    for key in cachelib.iter_event_keys(raw_dir, year):
        ed: Dict[str, Any] = {}
        for what in ("matches", "rankings", "alliances"):
            body = _body(raw_dir, f"{year}/event/{key}/{what}.json.gz")
            if body is not None:
                ed[what] = body
        teams = _body(raw_dir, f"{year}/event/{key}/teams.json.gz")
        if teams is not None:
            ed["teams"] = teams
        event_data[key] = ed

    teams_all: List[Any] = []
    for page in _team_pages(raw_dir, year):
        body = _body(raw_dir, cachelib.endpoint_teams_page(year, page)[1])
        if isinstance(body, list):
            teams_all.extend(body)

    dump: Dict[str, Any] = {
        "year": year,
        "fetched": fp["last_change_at"],
        "events": events,
        "event_data": event_data,
        "teams": teams_all,
    }
    districts = _body(raw_dir, cachelib.endpoint_districts(year)[1])
    if isinstance(districts, list):
        out: Dict[str, List[Any]] = {}
        for d in districts:
            dkey = d.get("key") if isinstance(d, dict) else None
            if not cachelib.valid_district_key(dkey, year):
                continue
            tl = _body(raw_dir, cachelib.endpoint_district_teams(year, dkey)[1])
            out[dkey[4:]] = tl if isinstance(tl, list) else []
        dump["districts"] = out
    return dump


# ---------------------------------------------------------------------------------------------
# dump -> cache
# ---------------------------------------------------------------------------------------------
def _dumps(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def _team_key(t: Any) -> str:
    s = str(t)
    return s if s.startswith("frc") else "frc" + s


def _put(raw_dir: str, rel: str, obj: Any) -> None:
    cachelib.write_if_changed(cachelib.abs_path(raw_dir, rel), cachelib.gz_bytes(_dumps(obj)))


def split_dump(dump: Dict[str, Any], cache: str) -> Dict[str, Any]:
    """Write one season dump into the cache layout and a fingerprint (last_change_at = dump["fetched"]).
    Returns counts.  Existing files of that season are kept unless overwritten (use a fresh dir)."""
    raw_dir = cache if not os.path.isdir(os.path.join(cache, "raw")) else os.path.join(cache, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    year = int(dump["year"])
    counts = {"events": 0, "event_data": 0, "teams": 0, "pages": 0, "districts": 0, "skipped_keys": 0}

    _put(raw_dir, cachelib.endpoint_events(year)[1], dump["events"])
    counts["events"] = len(dump["events"])

    for key, ed in dump["event_data"].items():
        if not cachelib.valid_event_key(key, year):
            counts["skipped_keys"] += 1
            continue
        for what in ("matches", "rankings", "alliances"):
            if what in ed and ed[what] is not None:
                _put(raw_dir, f"{year}/event/{key}/{what}.json.gz", ed[what])
        if ed.get("teams") is not None:
            _put(raw_dir, f"{year}/event/{key}/teams.json.gz", [_team_key(t) for t in ed["teams"]])
        # an event that exists in event_data but had no endpoint files still gets a marker body
        if not any(k in ed and ed[k] is not None for k in ("matches", "rankings", "alliances", "teams")):
            _put(raw_dir, f"{year}/event/{key}/matches.json.gz", None)
        counts["event_data"] += 1

    teams = dump.get("teams")
    if isinstance(teams, list):
        buckets: Dict[int, List[Any]] = {}
        for t in teams:
            num = int(str(t["key"])[3:])
            buckets.setdefault(num // TEAM_PAGE_SIZE, []).append(t)
        for page in sorted(buckets):
            _put(raw_dir, cachelib.endpoint_teams_page(year, page)[1], buckets[page])
        counts["teams"], counts["pages"] = len(teams), len(buckets)

    dist = dump.get("districts")
    if isinstance(dist, dict):
        listing = []
        for abbr, tl in dist.items():
            if not _ABBR_RE.match(str(abbr)):
                counts["skipped_keys"] += 1
                continue
            dkey = f"{year}{abbr}"
            listing.append({"key": dkey, "abbreviation": abbr})
            _put(raw_dir, cachelib.endpoint_district_teams(year, dkey)[1], [_team_key(t) for t in tl])
        _put(raw_dir, cachelib.endpoint_districts(year)[1], listing)
        counts["districts"] = len(listing)

    cachelib.refresh_fingerprint(raw_dir, year, str(dump.get("fetched") or "1970-01-01T00:00:00Z"), True)
    # refresh_fingerprint keeps an older last_change_at when the hash is unchanged; for a split
    # we want last_change_at to be the dump's fetch time exactly.
    fp = cachelib.load_fingerprint(raw_dir, year) or {}
    want = str(dump.get("fetched") or "1970-01-01T00:00:00Z")
    if fp.get("last_change_at") != want:
        fp["last_change_at"] = want
        cachelib.dump_small_json(fp, cachelib.fingerprint_path(raw_dir, year), indent=1)
    return counts


def read_dump_file(path: str) -> Dict[str, Any]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:  # type: ignore
        d = json.load(f)
    if not isinstance(d, dict) or "events" not in d or "event_data" not in d or "year" not in d:
        raise ValueError(f"{path}: not a season dump")
    return d


def write_dump_file(dump: Dict[str, Any], out: str) -> None:
    data = json.dumps(dump, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(cachelib.gz_bytes(data) if out.endswith(".gz") else data)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--split", metavar="DUMP", help="seed the cache from a tba_YYYY.json.gz dump")
    g.add_argument("--dump", metavar="YEAR", type=int, help="write the season dump built from the cache")
    ap.add_argument("--out", help="output file for --dump")
    args = ap.parse_args(argv)
    if args.split:
        counts = split_dump(read_dump_file(args.split), args.cache)
        print(f"split {args.split}: {counts}")
        return 0
    if not args.out:
        ap.error("--dump needs --out")
    write_dump_file(season_dump(args.cache, args.dump), args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

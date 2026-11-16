#!/usr/bin/env python3
"""Cut the WP1 CI fixture from a full season dump (run once; the output is committed).

    python3 -I pipeline/tests/make_wp1_fixtures.py /path/tba_2023.json.gz pipeline/tests/fixtures/tba_2023_mini.json.gz

Keeps one week-1 district event (2023inmis), one champs division (2023arc), two events the loader
drops (one type-99 offseason, one non-champs event with no week), in the original /events order, and
the team objects of every team that played in the kept events.  `oprs` is dropped.  The dump is
untrusted data: parsed with json only.
"""
import gzip
import json
import sys

KEEP = ("2023inmis", "2023arc")


def main(src: str, dst: str) -> None:
    with gzip.open(src, "rt", encoding="utf-8") as f:
        raw = json.load(f)
    by_key = {e["key"]: e for e in raw["events"]}
    drop_types = [e["key"] for e in raw["events"] if e["event_type"] == 99 and e["key"] not in raw["event_data"]][:1]
    drop_week = [e["key"] for e in raw["events"]
                 if e["event_type"] in (0, 1, 2) and e.get("week") is None and e["key"] not in raw["event_data"]][:1]
    keys = set(KEEP) | set(drop_types) | set(drop_week)
    events = [e for e in raw["events"] if e["key"] in keys]
    event_data = {}
    teams_needed = set()
    for k in KEEP:
        d = raw["event_data"][k]
        event_data[k] = {a: d[a] for a in ("matches", "rankings", "alliances")}
        for m in d["matches"]:
            for side in ("red", "blue"):
                teams_needed.update(m["alliances"][side]["team_keys"])
    teams = [t for t in raw["teams"] if t["key"] in teams_needed]
    out = {"year": raw["year"], "fetched": raw["fetched"], "events": events, "event_data": event_data, "teams": teams}
    data = json.dumps(out, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    with open(dst, "wb") as f:
        f.write(gzip.compress(data, compresslevel=9, mtime=0))
    print(f"{dst}: {len(events)} events ({sorted(keys)}), {len(teams)} teams, {len(data)} bytes json")
    assert all(k in by_key for k in KEEP)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

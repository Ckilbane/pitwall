"""Which events of `/events/{year}` the pipeline reads, mirroring the vendored upstream
`src.tba.read_tba.get_events` filter so the fetcher never pulls event endpoints the loader would
drop, and never skips one it keeps.

Upstream drops: keys containing "tempclone", keys in EVENT_BLACKLIST, event types 99 and 100
(unless overridden in EVENT_TYPE_OVERRIDES), and events whose `week` is None after champs events
(types 3, 4, 6) have been given week 8.  The two constants come from the vendored module so there
is one source of truth; they are imported, never retyped.
"""
import os
import sys
from typing import Any, Dict, Iterable, List, Set, Tuple

from fetch import cachelib

_VENDOR_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor", "backend")
_CHAMPS_TYPES = (3, 4, 6)  # CHAMPS_DIV, EINSTEIN, festival of champions -> EINSTEIN


def _constants() -> Tuple[Set[str], Set[str]]:
    if _VENDOR_BACKEND not in sys.path:
        sys.path.insert(0, _VENDOR_BACKEND)
    from src.tba.constants import EVENT_BLACKLIST, EVENT_TYPE_OVERRIDES  # noqa: WPS433

    return set(EVENT_BLACKLIST), set(EVENT_TYPE_OVERRIDES.keys())


def wanted_events(year: int, events: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(events the loader keeps, in `/events/{year}` order; keys skipped as malformed).

    `events` is the parsed `/events/{year}` body (untrusted).  Items that are not dicts, whose key
    fails validation, or that lack the fields the filter reads are skipped and reported."""
    blacklist, overrides = _constants()
    kept: List[Dict[str, Any]] = []
    bad: List[str] = []
    if not isinstance(events, list):
        return kept, bad
    for ev in events:
        if not isinstance(ev, dict):
            bad.append("<non-object>")
            continue
        key = ev.get("key")
        if not cachelib.valid_event_key(key, year):
            bad.append(str(key)[:40])
            continue
        if "tempclone" in key or key in blacklist:
            continue
        try:
            etype = int(ev.get("event_type"))
        except (TypeError, ValueError):
            bad.append(key)
            continue
        if etype in (99, 100) and key not in overrides:
            continue
        week = ev.get("week")
        if etype in _CHAMPS_TYPES:
            week = 8
        if week is None:
            continue
        kept.append(ev)
    return kept, bad


def event_keys(year: int, events: Any) -> List[str]:
    return [e["key"] for e in wanted_events(year, events)[0]]

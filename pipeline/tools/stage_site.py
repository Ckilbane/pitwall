#!/usr/bin/env python3
"""Assemble the Pages artifact (SPEC 2.3 step 8): site/ + data/ -> _site/.

    python pipeline/tools/stage_site.py --site site --data staged_data --out _site

* copies `site/` (without tests/, tools/, node_modules/, caches, dotfiles) to --out;
* copies the staged data tree to --out/data after checking every file against an allowlist
  (manifest.json, models.json, history.json.gz, {season}/season|asof|traj.json.gz,
  {season}/events/{key}.json.gz), so raw TBA bodies, caches or anything else can never be published;
* runs `site/tools/build_precache.py <out>` (WP6) to write sw-precache.json.  If the site has an
  sw.js the script must exist and succeed; a site without a service worker (before WP6) skips it.

Refuses symlinks.  Exit 0 ok, 1 refused or failed.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from typing import List, Optional

SITE_SKIP_DIRS = {"tests", "tools", "node_modules", "__pycache__", ".git", ".github"}
DATA_ALLOW = [
    re.compile(r"^manifest\.json$"),
    re.compile(r"^models\.json$"),
    re.compile(r"^history\.json\.gz$"),
    re.compile(r"^[0-9]{4}/(season|asof|traj)\.json\.gz$"),
    re.compile(r"^[0-9]{4}/events/[0-9]{4}[a-z0-9]+\.json\.gz$"),
]


def check_data_tree(data: str) -> List[str]:
    problems: List[str] = []
    if not os.path.isfile(os.path.join(data, "manifest.json")):
        problems.append("manifest.json missing")
    for dirpath, dirnames, filenames in os.walk(data):
        for name in dirnames + filenames:
            if os.path.islink(os.path.join(dirpath, name)):
                problems.append(f"symlink: {os.path.relpath(os.path.join(dirpath, name), data)}")
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), data).replace(os.sep, "/")
            if not any(p.match(rel) for p in DATA_ALLOW):
                problems.append(f"not publishable: {rel}")
    return problems


def copy_site(site: str, out: str) -> None:
    for dirpath, dirnames, filenames in os.walk(site):
        dirnames[:] = sorted(d for d in dirnames if d not in SITE_SKIP_DIRS and not d.startswith("."))
        rel = os.path.relpath(dirpath, site)
        dest = out if rel == "." else os.path.join(out, rel)
        os.makedirs(dest, exist_ok=True)
        for fn in sorted(filenames):
            if fn.startswith(".") or fn.endswith((".pyc", ".map")):
                continue
            src = os.path.join(dirpath, fn)
            if os.path.islink(src):
                raise ValueError(f"symlink in site: {os.path.relpath(src, site)}")
            shutil.copy2(src, os.path.join(dest, fn))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    if not os.path.isfile(os.path.join(a.site, "index.html")):
        print(f"ERROR: {a.site}/index.html not found: nothing to publish", file=sys.stderr)
        return 1
    problems = check_data_tree(a.data)
    if problems:
        print("ERROR: data tree refused:\n  " + "\n  ".join(problems[:20]), file=sys.stderr)
        return 1
    if os.path.exists(a.out):
        shutil.rmtree(a.out)
    os.makedirs(a.out)
    try:
        copy_site(a.site, a.out)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    shutil.copytree(a.data, os.path.join(a.out, "data"))

    pre = os.path.join(a.site, "tools", "build_precache.py")
    has_sw = os.path.isfile(os.path.join(a.site, "sw.js"))
    if os.path.isfile(pre):
        r = subprocess.run([sys.executable, pre, a.out])
        if r.returncode != 0:
            print("ERROR: build_precache.py failed", file=sys.stderr)
            return 1
    elif has_sw:
        print("ERROR: site/sw.js exists but site/tools/build_precache.py does not", file=sys.stderr)
        return 1
    else:
        print("note: no service worker in site/: sw-precache.json not written")
    n = sum(len(f) for _, _, f in os.walk(a.out))
    print(f"staged {n} files in {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

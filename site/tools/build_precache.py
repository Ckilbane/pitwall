#!/usr/bin/env python3
"""Write the service-worker precache list for a staged site (SPEC 6, WP6).

    python site/tools/build_precache.py _site

Run by pipeline/tools/stage_site.py after it copies site/ and data/ into the Pages artifact.

* Lists every app-shell file under the staged root: index.html, manifest.webmanifest, css/, js/,
  assets/ (fonts), brand/ (icons), vendor/. Never data/ (shards are cached at run time, keyed by
  their manifest hashes), never sw.js or sw-precache.json themselves, never tests/, tools/ or dotfiles.
* Writes <root>/sw-precache.json: {"version": v, "files": [{"url": "css/pitwall.css", "hash": h}, ...]}
  with urls relative to the site root (the worker's scope), sorted, and hash = first 12 hex chars
  of SHA-256 of the bytes on disk (the same hash12 as the data shards).
* version = hash12 of the list plus the source of sw.js, so it changes whenever any shell file or
  the worker's code changes, and only then (deterministic: same files give the same version).
* Rewrites the line `const VERSION = '...';` in <root>/sw.js to that version. A changed sw.js is
  what makes browsers install the new worker, which then precaches the new shell.

Exit 0 ok; 1 on a missing root, missing sw.js or a sw.js without exactly one VERSION line.
"""
import hashlib
import json
import os
import re
import sys
from typing import Dict, List, Optional

SHELL_TOP_FILES = {"index.html", "manifest.webmanifest"}
SHELL_DIRS = {"css", "js", "assets", "brand", "vendor"}
SKIP_DIRS = {"tests", "tools", "node_modules", "__pycache__", "data"}
SKIP_SUFFIXES = (".pyc", ".map", ".md", ".py")
VERSION_RE = re.compile(r"^const VERSION = '[^'\n]*';$", re.M)


def hash12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def shell_files(root: str) -> List[str]:
    """Relative POSIX paths of every shell file under root, sorted."""
    out: List[str] = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if name in SHELL_TOP_FILES and os.path.isfile(p):
            out.append(name)
    for top in sorted(SHELL_DIRS):
        base = os.path.join(root, top)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            for fn in sorted(filenames):
                if fn.startswith(".") or fn.endswith(SKIP_SUFFIXES):
                    continue
                full = os.path.join(dirpath, fn)
                if os.path.islink(full):
                    continue
                out.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return sorted(out)


def build(root: str, sw_source: Optional[str] = None) -> Dict:
    """The precache object for root. sw_source: the worker's code (default: root/sw.js)."""
    files = []
    for rel in shell_files(root):
        with open(os.path.join(root, rel), "rb") as f:
            files.append({"url": rel, "hash": hash12(f.read())})
    if sw_source is None:
        with open(os.path.join(root, "sw.js"), encoding="utf-8") as f:
            sw_source = f.read()
    neutral = VERSION_RE.sub("const VERSION = '';", sw_source)  # version must not depend on itself
    body = "\n".join(f"{x['url']} {x['hash']}" for x in files) + "\n--sw--\n" + neutral
    return {"version": hash12(body.encode("utf-8")), "files": files}


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: build_precache.py <staged site root>", file=sys.stderr)
        return 1
    root = argv[0]
    sw_path = os.path.join(root, "sw.js")
    if not os.path.isfile(os.path.join(root, "index.html")) or not os.path.isfile(sw_path):
        print(f"ERROR: {root} needs index.html and sw.js", file=sys.stderr)
        return 1
    with open(sw_path, encoding="utf-8") as f:
        sw = f.read()
    if len(VERSION_RE.findall(sw)) != 1:
        print("ERROR: sw.js must contain exactly one line `const VERSION = '...';`", file=sys.stderr)
        return 1
    pre = build(root, sw)
    with open(os.path.join(root, "sw-precache.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(pre, f, separators=(",", ":"))
        f.write("\n")
    with open(sw_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(VERSION_RE.sub(f"const VERSION = '{pre['version']}';", sw))
    total = sum(os.path.getsize(os.path.join(root, x["url"])) for x in pre["files"])
    print(f"sw-precache.json: version {pre['version']}, {len(pre['files'])} files, {total / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

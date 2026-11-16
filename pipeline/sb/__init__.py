"""Offline flat-file port of the Statbotics EPA pipeline (loader stage).

Importing `sb` makes the vendored upstream package (`src`) importable by adding
<root>/vendor/backend to sys.path.  Nothing under vendor/ is modified.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_BACKEND = os.path.join(ROOT, "vendor", "backend")

# keep bytecode out of vendor/ (treated as read-only)
if sys.pycache_prefix is None:
    sys.pycache_prefix = os.path.join(ROOT, ".pycache")

if VENDOR_BACKEND not in sys.path:
    sys.path.insert(0, VENDOR_BACKEND)

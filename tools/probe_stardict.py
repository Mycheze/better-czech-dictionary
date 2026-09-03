#!/usr/bin/env python3
"""Assert that specific lookups in a BUILT StarDict index return the right senses.

The existing coverage test (tools/test_dictionary.py) queries the database, so it
reports 99.99% while the exported dictionary is still hiding senses -- every bug
this file guards against was invisible to it. These probes read the shipped .idx
and .dict.dz instead, i.e. exactly what KOReader sees.

Usage:
    python3 tools/probe_stardict.py [--dir output/stardict] [--name Czech-English]
"""

import argparse
import gzip
import struct
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# (lookup key, substring that must appear, why it matters)
PROBES = [
    ("houbičku",  "sponge",      "inflected form must show every sense, not just senses[0]"),
    ("houbička",  "sponge",      "diminutive cross-reference resolves to the full target entry"),
    ("let",       "summer",      "a lemma that is also an inflected form keeps both readings"),
    ("plot",      "hedge",       "phrase back-link surfaces živý plot"),
    ("plotu",     "hedge",       "phrase back-link survives inflection"),
    ("živý plot",  "hedge",      "regression: the phrase entry itself"),
    ("bát",       "bát se",      "reflexive verb entry is reachable from the bare verb"),
    ("táhnout",   "haul",        "merged Svobodné senses"),
    ("zbrusu",    "zbrusu nový", "regression: set phrase already worked, must not break"),
    # MorfFlex paradigms for entries added after the last full build
    ("vopruzu",   "vopruz",      "show-subs colloquialism declines (MorfFlex re-run)"),
    ("saunové",   "sauna",       "post-build adjective declines (MorfFlex re-run)"),
]

# Keys that must NOT be in the index at all.
ABSENT_PROBES = [
    ("vložení omezení funkčnosti do programu (drm). hardwarové zařízení, pro které "
     "byl program výrobcem vyvinut, přestane fungovat pokud uživatel program pozmění "
     "resp. uživateli je zamezeno přizpůsobit program svým potřebám. (porušení "
     "svobody 1 z definice svobodného software)",
     "a Svobodne definition stored as a headword"),
    ("24 hours a day, 7 days a week", "English text stored as a Czech headword"),
    ("= subprime mortgage", "a Svobodne cross-reference note as a headword"),
]

# (lookup key, substring that must NOT come before the first real sense)
ORDER_PROBES = [
    ("ostrý", "surname", "name-only entry must render after the real senses"),
    ("nový",  "surname", "name-only entry must render after the real senses"),
]


def load_index(directory, name):
    idx_path = Path(directory) / f"{name}.idx"
    dz_path = Path(directory) / f"{name}.dict.dz"
    raw_path = Path(directory) / f"{name}.dict"
    if not idx_path.exists():
        sys.exit(f"missing {idx_path}")
    if dz_path.exists():
        blob = gzip.open(dz_path, "rb").read()
    elif raw_path.exists():
        blob = raw_path.read_bytes()
    else:
        sys.exit(f"missing {dz_path} and {raw_path}")

    data = idx_path.read_bytes()
    index = {}
    prev = b""
    order_violations = 0
    oversized = 0
    pos = 0
    count = 0
    while pos < len(data):
        end = data.index(b"\x00", pos)
        key_bytes = data[pos:end]
        offset, size = struct.unpack(">II", data[end + 1:end + 9])
        index.setdefault(key_bytes.decode("utf-8"), []).append((offset, size))
        if key_bytes < prev:
            order_violations += 1
        if len(key_bytes) > 255:
            oversized += 1
        prev = key_bytes
        pos = end + 9
        count += 1
    return index, blob, count, order_violations, oversized


def render(index, blob, key):
    out = []
    for offset, size in index.get(key, []):
        out.append(blob[offset:offset + size].rstrip(b"\x00").decode("utf-8", "replace"))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(PROJECT_ROOT / "output" / "stardict"))
    ap.add_argument("--name", default="Czech-English")
    args = ap.parse_args()

    index, blob, count, order_violations, oversized = load_index(args.dir, args.name)
    print(f"{count:,} index records, {len(index):,} distinct keys")
    print(f"byte-order violations: {order_violations}   keys over 255 bytes: {oversized}")
    print()

    failures = 0
    for key, needle, why in PROBES:
        text = render(index, blob, key)
        if not text:
            print(f"  MISS  {key:<12} NOT IN INDEX  ({why})")
            failures += 1
        elif needle.lower() in text.lower():
            print(f"  ok    {key:<12} contains {needle!r}")
        else:
            print(f"  FAIL  {key:<12} missing {needle!r}  ({why})")
            print(f"        got: {text[:160]}")
            failures += 1

    for key, needle, why in ORDER_PROBES:
        text = render(index, blob, key)
        if not text:
            print(f"  MISS  {key:<12} NOT IN INDEX  ({why})")
            failures += 1
            continue
        where = text.lower().find(needle.lower())
        if where == -1:
            print(f"  ok    {key:<12} no {needle!r} at all")
        elif where < len(text) // 2:
            print(f"  FAIL  {key:<12} {needle!r} appears early ({where}/{len(text)})  ({why})")
            failures += 1
        else:
            print(f"  ok    {key:<12} {needle!r} demoted to position {where}/{len(text)}")

    for key, why in ABSENT_PROBES:
        if key in index:
            print(f"  FAIL  {key[:40]!r}... still indexed  ({why})")
            failures += 1
        else:
            print(f"  ok    absent: {key[:40]!r}...")

    if order_violations:
        print(f"\n  FAIL  index is not in UTF-8 byte order "
              f"({order_violations} violations) -- StarDict binary search will miss keys")
        failures += 1
    if oversized:
        # Pre-existing: the shipped Aug-2026 build has the same single long key.
        print(f"\n  warn  {oversized} key(s) exceed the 255-byte StarDict limit")

    print(f"\n{'PASS' if not failures else str(failures) + ' FAILING PROBE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

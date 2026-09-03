#!/usr/bin/env python3
"""Shard the tools/audit_entries.py candidates for agent workers, then merge back.

Same file-based, resumable pattern as processing/yt_swarm.py, but for re-auditing
entries that already exist rather than generating brand-new ones. The difference
matters downstream: these entries must be imported with --refresh, or
INSERT OR IGNORE drops every one of them.

    python3 tools/audit_swarm.py shard --size 100
    # ... agents write <shard>.out.json ...
    python3 tools/audit_swarm.py merge
    python3 processing/process_show_subs.py --import-entries \
        show_subs_cache/audit_entries.json --refresh --source claude
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "processing"))
from yt_swarm import input_shards, load_json, save_json, install_brief, write_shards  # noqa: E402

CACHE_DIR = PROJECT_ROOT / "show_subs_cache"
CANDIDATES = CACHE_DIR / "thin_candidates.json"
SHARDS = CACHE_DIR / "shards" / "audit"
MERGED = CACHE_DIR / "audit_entries.json"


def cmd_shard(args):
    cands = load_json(CANDIDATES, [])
    if not cands:
        sys.exit(f"no candidates at {CANDIDATES} -- run tools/audit_entries.py first")
    done = load_json(MERGED, {})
    todo = [c for c in cands if c["headword"] not in done]
    print(f"candidates: {len(cands):,}   already generated: {len(done):,}   "
          f"to shard: {len(todo):,}")
    paths = write_shards(todo, SHARDS, args.size)
    brief = install_brief(SHARDS, "swarm_gen_entries.md")
    print(f"wrote {len(paths)} shards of <= {args.size} into {SHARDS}")
    print(f"worker brief: {brief}")


def cmd_merge(args):
    merged = load_json(MERGED, {})
    before = len(merged)
    added = bad = 0
    missing = []
    for shard in input_shards(SHARDS):
        out = shard.with_suffix(".out.json")
        if not out.exists():
            missing.append(shard.name)
            continue
        entries = load_json(out, None)
        if isinstance(entries, dict):
            entries = entries.get("entries") or entries.get("results")
        if not isinstance(entries, list):
            missing.append(shard.name)
            continue
        for e in entries:
            if not isinstance(e, dict):
                bad += 1
                continue
            lemma = str(e.get("lemma", "")).strip().lower()
            if not lemma or not e.get("senses") or not e.get("pos"):
                bad += 1
                continue
            merged[lemma] = e
            added += 1
    save_json(MERGED, merged)
    print(f"merged {added:,} entries ({bad} malformed skipped)")
    print(f"cache: {before:,} -> {len(merged):,}   file: {MERGED}")
    if missing:
        print(f"MISSING output for {len(missing)} shards: {missing[:12]}")
    counts = Counter(len(e.get("senses", [])) for e in merged.values())
    print("senses per entry:", sorted(counts.items()))
    extra = sum(1 for k in merged if " " in k)
    print(f"multi-word entries proposed (reflexives/phrases): {extra:,}")


def cmd_status(args):
    shards = input_shards(SHARDS)
    done = [s for s in shards if s.with_suffix(".out.json").exists()]
    print(f"shards: {len(shards)}   with output: {len(done)}   "
          f"pending: {len(shards) - len(done)}")
    for s in shards:
        if not s.with_suffix(".out.json").exists():
            print(f"  pending: {s.name}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("shard"); p.add_argument("--size", type=int, default=100)
    p.set_defaults(func=cmd_shard)
    p = sub.add_parser("merge"); p.set_defaults(func=cmd_merge)
    p = sub.add_parser("status"); p.set_defaults(func=cmd_status)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

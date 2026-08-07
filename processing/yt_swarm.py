#!/usr/bin/env python3
"""
Shard/merge helper for screening and generating with a swarm of agents.

The API-loop version of this work (process_yt_corpus.py --screen --generate)
paid per reasoning token and became the bottleneck. This splits the same work
into files that worker agents can pick up independently, then folds their
output back into the same caches process_yt_corpus.py already understands, so
the two approaches stay interchangeable.

Usage:
    # screening
    python3 yt_swarm.py shard-screen --size 300
    python3 yt_swarm.py merge-screen

    # generation
    python3 yt_swarm.py shard-gen --size 40
    python3 yt_swarm.py merge-gen

    python3 yt_swarm.py status
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

CORPUS_DIR = PROJECT_ROOT / "youtube_corpus"
CANDIDATES = CORPUS_DIR / "candidates.json"
SCREEN_CACHE = CORPUS_DIR / "screen_results.json"
GEN_CACHE = CORPUS_DIR / "generated_entries.json"
SCREEN_SHARDS = CORPUS_DIR / "shards" / "screen"
GEN_SHARDS = CORPUS_DIR / "shards" / "gen"

VALID_CATS = {"word", "proper_noun", "foreign", "asr_error"}


def input_shards(d):
    """Shard inputs only -- glob("shard_*.json") also matches our own outputs."""
    return sorted(p for p in Path(d).glob("shard_*.json")
                  if not p.name.endswith(".out.json"))


def load_json(p, default):
    if Path(p).exists():
        try:
            return json.loads(Path(p).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"WARNING: {p} unreadable")
    return default


def save_json(p, data):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def write_shards(records, outdir, size, prefix="shard"):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob(f"{prefix}_*.json"):
        old.unlink()
    paths = []
    for i in range(0, len(records), size):
        p = outdir / f"{prefix}_{i//size:03d}.json"
        p.write_text(json.dumps(records[i:i + size], ensure_ascii=False, indent=1),
                     encoding="utf-8")
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------

def cmd_shard_screen(args):
    cands = load_json(CANDIDATES, [])
    screened = load_json(SCREEN_CACHE, {})

    if args.rescreen_words:
        # Re-check only words a previous pass ACCEPTED. Rejections are safe to
        # keep -- a false rejection leaves a gap, a false accept puts a wrong
        # headword in the dictionary.
        targets = [w for w, v in screened.items() if v[0] == "word"]
        by_word = {c["word"]: c for c in cands}
        records = [by_word.get(w) or {"word": w, "freq": 0, "channels": 0,
                                      "pos_hint": "", "contexts": []}
                   for w in targets]
        print(f"re-screening {len(records):,} previously accepted words")
    else:
        records = [c for c in cands
                   if c["word"] not in screened
                   or screened[c["word"]][0] == "unscreened"]
        print(f"screening {len(records):,} unscreened candidates")

    records = [{"word": r["word"], "freq": r.get("freq", 0),
                "channels": r.get("channels", 0), "pos_hint": r.get("pos_hint", ""),
                "contexts": r.get("contexts", [])[:2]} for r in records]
    paths = write_shards(records, SCREEN_SHARDS, args.size)
    print(f"wrote {len(paths)} shards of <= {args.size} into {SCREEN_SHARDS}")
    for p in paths:
        print(f"  {p}")


def cmd_merge_screen(args):
    screened = load_json(SCREEN_CACHE, {})
    before = len(screened)
    merged = skipped = 0
    missing_out = []

    for shard in input_shards(SCREEN_SHARDS):
        out = shard.with_suffix(".out.json")
        if not out.exists():
            missing_out.append(shard.name)
            continue
        data = load_json(out, None)
        if data is None:
            missing_out.append(shard.name)
            continue
        results = data.get("results") if isinstance(data, dict) else data
        if not isinstance(results, list):
            missing_out.append(shard.name)
            continue
        for item in results:
            if not isinstance(item, dict):
                continue
            w = str(item.get("w", "")).strip().lower()
            cat = str(item.get("c", "")).strip()
            if not w or cat not in VALID_CATS:
                skipped += 1
                continue
            lemma = item.get("lemma")
            lemma = str(lemma).strip().lower() if lemma else None
            screened[w] = [cat, lemma]
            merged += 1

    save_json(SCREEN_CACHE, screened)
    print(f"merged {merged:,} verdicts ({skipped} malformed skipped)")
    print(f"cache: {before:,} -> {len(screened):,}")
    if missing_out:
        print(f"MISSING output for {len(missing_out)} shards: {missing_out[:10]}")
    print("verdicts:", Counter(v[0] for v in screened.values()).most_common())


def cmd_shard_gen(args):
    """Shard the headwords that still need a dictionary entry."""
    import sqlite3
    from process_yt_corpus import lemmas_present, DB_PATH

    screened = load_json(SCREEN_CACHE, {})
    cands = {c["word"]: c for c in load_json(CANDIDATES, [])}
    gen_cache = load_json(GEN_CACHE, {})

    # headword -> a representative observed form (for context + pos hint)
    rep = {}
    for w, v in screened.items():
        if v[0] != "word":
            continue
        lemma = (v[1] or w).lower()
        if lemma not in rep:
            rep[lemma] = w

    conn = sqlite3.connect(DB_PATH)
    have = lemmas_present(conn, set(rep))
    conn.close()

    need = [l for l in rep if l not in have and l not in gen_cache]
    records = []
    for lemma in sorted(need):
        form = rep[lemma]
        c = cands.get(form, {})
        records.append({"headword": lemma, "observed_form": form,
                        "pos_hint": c.get("pos_hint", ""),
                        "contexts": c.get("contexts", [])[:2]})

    print(f"confirmed headwords: {len(rep):,}  already in dict: {len(have):,}  "
          f"cached: {sum(1 for l in rep if l in gen_cache):,}")
    print(f"need generation: {len(records):,}")
    paths = write_shards(records, GEN_SHARDS, args.size)
    print(f"wrote {len(paths)} shards of <= {args.size} into {GEN_SHARDS}")


def cmd_merge_gen(args):
    gen_cache = load_json(GEN_CACHE, {})
    before = len(gen_cache)
    merged = bad = 0
    missing_out = []

    for shard in input_shards(GEN_SHARDS):
        out = shard.with_suffix(".out.json")
        if not out.exists():
            missing_out.append(shard.name)
            continue
        entries = load_json(out, None)
        if isinstance(entries, dict):
            entries = entries.get("entries") or entries.get("results")
        if not isinstance(entries, list):
            missing_out.append(shard.name)
            continue
        for e in entries:
            if not isinstance(e, dict):
                bad += 1
                continue
            lemma = str(e.get("lemma", "")).strip().lower()
            if not lemma or not e.get("senses") or not e.get("pos"):
                bad += 1
                continue
            gen_cache[lemma] = e
            merged += 1

    save_json(GEN_CACHE, gen_cache)
    print(f"merged {merged:,} entries ({bad} malformed skipped)")
    print(f"cache: {before:,} -> {len(gen_cache):,}")
    if missing_out:
        print(f"MISSING output for {len(missing_out)} shards: {missing_out[:10]}")


def cmd_status(args):
    screened = load_json(SCREEN_CACHE, {})
    gen = load_json(GEN_CACHE, {})
    cands = load_json(CANDIDATES, [])
    print(f"candidates file : {len(cands):,}")
    print(f"screen verdicts : {len(screened):,}  {Counter(v[0] for v in screened.values()).most_common()}")
    print(f"generated entries: {len(gen):,}")
    for d, label in ((SCREEN_SHARDS, "screen"), (GEN_SHARDS, "gen")):
        if d.exists():
            shards = list(d.glob("shard_*.json"))
            shards = [s for s in shards if not s.name.endswith(".out.json")]
            outs = list(d.glob("shard_*.out.json"))
            print(f"{label} shards: {len(shards)} in, {len(outs)} done")


def main():
    ap = argparse.ArgumentParser(description="Shard/merge for agent swarm")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("shard-screen")
    s.add_argument("--size", type=int, default=300)
    s.add_argument("--rescreen-words", action="store_true",
                   help="Re-check previously ACCEPTED words instead of unscreened ones")
    s.set_defaults(func=cmd_shard_screen)

    s = sub.add_parser("merge-screen"); s.set_defaults(func=cmd_merge_screen)

    s = sub.add_parser("shard-gen")
    s.add_argument("--size", type=int, default=40)
    s.set_defaults(func=cmd_shard_gen)

    s = sub.add_parser("merge-gen"); s.set_defaults(func=cmd_merge_gen)
    s = sub.add_parser("status"); s.set_defaults(func=cmd_status)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

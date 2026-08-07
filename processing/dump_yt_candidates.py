#!/usr/bin/env python3
"""
Dump the YouTube corpus candidate words to JSON, with contexts.

This is the same candidate set process_yt_corpus.py screens, written out so it
can be sharded across worker agents instead of a single API loop.

Each record: word, freq, channels, majka lemma, pos hint, example contexts.

Usage:
    python3 dump_yt_candidates.py                       # all unscreened candidates
    python3 dump_yt_candidates.py --include-screened    # every candidate
    python3 dump_yt_candidates.py --out path.json
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from process_text import lemmatize_with_majka, find_missing, pos_hint_to_pos  # noqa: E402
from yt_word_filter import structural_verdict, looks_like_proper_noun  # noqa: E402
from process_yt_corpus import (  # noqa: E402
    load_word_stats, build_context_index, load_cache,
    DB_PATH, CORPUS_FILE, SCREEN_CACHE, CORPUS_DIR,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-channels", type=int, default=3)
    ap.add_argument("--min-freq", type=int, default=5)
    ap.add_argument("--cap-threshold", type=float, default=0.80)
    ap.add_argument("--include-screened", action="store_true")
    ap.add_argument("--out", default=str(CORPUS_DIR / "candidates.json"))
    args = ap.parse_args()

    stats = load_word_stats()
    word_freq = Counter({w: s["freq"] for w, s in stats.items()})
    print(f"forms={len(stats):,} tokens={sum(word_freq.values()):,}")

    print("Lemmatizing...")
    lemma_map, pos_map = lemmatize_with_majka(list(word_freq.keys()))

    print("Finding gaps...")
    conn = sqlite3.connect(DB_PATH)
    missing = find_missing(conn, word_freq, lemma_map, pos_map)
    conn.close()
    print(f"missing forms: {len(missing):,}")

    rejected = Counter()
    cands = []
    for m in missing:
        w = m["word"]
        s = stats[w]
        r = structural_verdict(w)
        if r:
            rejected[r] += 1
            continue
        if s["channels"] < args.min_channels:
            rejected["low_dispersion"] += 1
            continue
        if s["freq"] < args.min_freq:
            rejected["low_frequency"] += 1
            continue
        if looks_like_proper_noun(w, s["freq"], s["caps"], args.cap_threshold):
            rejected["proper_noun_caps"] += 1
            continue
        cands.append(m)

    print(f"candidates: {len(cands):,}")

    screened = load_cache(SCREEN_CACHE)
    if not args.include_screened:
        before = len(cands)
        cands = [m for m in cands
                 if m["word"] not in screened
                 or screened[m["word"]][0] == "unscreened"]
        print(f"already screened: {before - len(cands):,}  remaining: {len(cands):,}")

    print("Building context index...")
    contexts = build_context_index(CORPUS_FILE, [m["word"] for m in cands])

    out = []
    for m in cands:
        w = m["word"]
        s = stats[w]
        out.append({
            "word": w,
            "freq": s["freq"],
            "channels": s["channels"],
            "majka_lemma": m["lemma"] if m["lemma"] != w else None,
            "pos_hint": pos_hint_to_pos(m.get("pos_hint", "")),
            "contexts": [c[:180] for c in (contexts.get(w) or [])[:2]],
        })
    out.sort(key=lambda r: -r["freq"])

    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(out):,} candidates -> {args.out}")


if __name__ == "__main__":
    main()

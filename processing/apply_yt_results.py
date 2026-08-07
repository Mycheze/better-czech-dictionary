#!/usr/bin/env python3
"""
Apply screened + generated YouTube results to the dictionary, then measure.

Separate from process_yt_corpus.py because the results now come from a swarm of
worker agents rather than an API loop: this step makes no network calls at all,
it just folds the cached verdicts and entries into the database and reports what
changed.

Two things get written:
  entries      -- one row per newly generated headword
  inflections  -- one row per confirmed surface form -> headword, so colloquial
                  spellings like "cejtím" resolve to "cítit" without a
                  duplicate headword. Forms whose headword already existed cost
                  nothing and are linked here too.

Usage:
    python3 apply_yt_results.py --dry-run
    python3 apply_yt_results.py
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from process_text import lemmatize_with_majka, find_missing, save_entries  # noqa: E402
from yt_word_filter import structural_verdict  # noqa: E402
from process_yt_corpus import (  # noqa: E402
    load_word_stats, load_cache, save_cache, save_inflections, lemmas_present,
    DB_PATH, SCREEN_CACHE, GEN_CACHE, REPORT_FILE,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()

    stats = load_word_stats()
    word_freq = Counter({w: s["freq"] for w, s in stats.items()})
    total_tokens = sum(word_freq.values())

    print("Lemmatizing...")
    lemma_map, pos_map = lemmatize_with_majka(list(word_freq.keys()))

    conn = sqlite3.connect(args.db)
    print("Measuring baseline...")
    missing_before = find_missing(conn, word_freq, lemma_map, pos_map)
    miss_tok_before = sum(m["freq"] for m in missing_before)
    form_cov_before = (len(stats) - len(missing_before)) / len(stats) * 100
    tok_cov_before = (total_tokens - miss_tok_before) / total_tokens * 100

    screened = load_cache(SCREEN_CACHE)
    gen = load_cache(GEN_CACHE)
    verdicts = Counter(v[0] for v in screened.values())

    print(f"\n{'='*64}")
    print("INPUTS")
    print(f"{'='*64}")
    print(f"  screening verdicts : {len(screened):,}  {dict(verdicts)}")
    print(f"  generated entries  : {len(gen):,}")
    print(f"\n  BASELINE  forms {form_cov_before:6.2f}%   tokens {tok_cov_before:6.2f}%")

    # Confirmed surface form -> standard headword.
    form_to_lemma = {}
    for w, v in screened.items():
        if v[0] != "word":
            continue
        form_to_lemma[w] = (v[1] or lemma_map.get(w) or w).lower()

    print(f"  confirmed forms    : {len(form_to_lemma):,}")
    print(f"  distinct headwords : {len(set(form_to_lemma.values())):,}")

    if args.dry_run:
        print("\n(dry run -- nothing written)")
        conn.close()
        return

    # Only save entries whose headword is actually referenced by a confirmed form.
    wanted = set(form_to_lemma.values())
    to_save = {l: e for l, e in gen.items()
               if l in wanted and isinstance(e, dict) and "_error" not in e}
    print(f"\nSaving {len(to_save):,} entries...")
    saved, save_errors = save_entries(conn, to_save)
    print(f"  entries inserted: {saved:,}  (errors {save_errors})")

    pos_lookup = {l: e.get("pos") for l, e in to_save.items() if e.get("pos")}
    linked = save_inflections(conn, form_to_lemma, pos_lookup)
    print(f"  surface forms linked: {linked:,}")

    print("\nRe-measuring...")
    missing_after = find_missing(conn, word_freq, lemma_map, pos_map)
    miss_tok_after = sum(m["freq"] for m in missing_after)
    form_cov = (len(stats) - len(missing_after)) / len(stats) * 100
    tok_cov = (total_tokens - miss_tok_after) / total_tokens * 100

    # Real-word coverage: exclude tokens that are demonstrably not dictionary
    # material -- names, foreign words, ASR garbage, and anything the structural
    # filter rejects outright. Those should not count against a Czech
    # dictionary's coverage.
    noise_tokens = 0
    unjudged = 0
    for m in missing_after:
        w = m["word"]
        cat = screened.get(w, [None, None])[0]
        if cat in ("proper_noun", "foreign", "asr_error"):
            noise_tokens += m["freq"]
        elif cat is None and structural_verdict(w):
            noise_tokens += m["freq"]
        elif cat is None:
            unjudged += m["freq"]

    real_total = total_tokens - noise_tokens
    real_missing = miss_tok_after - noise_tokens
    real_cov = (real_total - real_missing) / real_total * 100 if real_total else 0

    c = conn.cursor()
    c.execute("INSERT INTO processed_texts (filename, total_lemmas, missing_lemmas,"
              " new_entries_added) VALUES (?, ?, ?, ?)",
              ("youtube_corpus/ (100-channel YouTube subtitle corpus)",
               len(stats), len(missing_before), saved))
    conn.commit()

    print(f"\n{'='*64}")
    print("COVERAGE")
    print(f"{'='*64}")
    print(f"  Forms    {form_cov_before:6.2f}%  ->  {form_cov:6.2f}%")
    print(f"  Tokens  {tok_cov_before:6.2f}%  ->  {tok_cov:6.2f}%")
    print(f"  Real-word tokens (excl. names/foreign/ASR noise): {real_cov:6.2f}%")
    print(f"    noise tokens excluded : {noise_tokens:,}")
    print(f"    still-unjudged tokens : {unjudged:,}  (never screened)")

    report = load_cache(REPORT_FILE) or {}
    report.update({
        "baseline_form_coverage_pct": round(form_cov_before, 3),
        "baseline_token_coverage_pct": round(tok_cov_before, 3),
        "final_form_coverage_pct": round(form_cov, 3),
        "final_token_coverage_pct": round(tok_cov, 3),
        "real_word_token_coverage_pct": round(real_cov, 3),
        "entries_saved": saved,
        "forms_linked": linked,
        "screen_verdicts": dict(verdicts),
        "noise_tokens_excluded": noise_tokens,
        "unjudged_tokens": unjudged,
    })
    save_cache(REPORT_FILE, report)
    conn.close()
    print(f"\nReport -> {REPORT_FILE}")


if __name__ == "__main__":
    main()

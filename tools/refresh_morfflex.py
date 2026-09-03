#!/usr/bin/env python3
"""Give MorfFlex paradigms to entries added after the last full build.

`build_dictionary.import_morfflex` only expands lemmas that exist in `entries`
at the moment it runs, and it runs LAST in the build. So every entry added
afterwards -- the DeepSeek gap-fills, the YouTube batch, the TV-show batch, any
re-audit -- gets no paradigm at all and is findable only in the exact form that
happened to be linked. Measured before the first run of this script: Wiktionary
adjectives averaged 571 inflected forms, Claude-sourced adjectives averaged 2.2.

Run this after anything that adds entries. It is incremental: only lemmas with
no `source='morfflex'` rows are targeted, so the cost is one pass over the
MorfFlex file rather than a rebuild.

Usage:
    python3 tools/refresh_morfflex.py --report   # what is missing, change nothing
    python3 tools/refresh_morfflex.py            # import the missing paradigms
"""

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from build_dictionary import import_morfflex, is_name_only  # noqa: E402

DB_PATH = PROJECT_ROOT / "dictionary.db"
MORFFLEX_DIR = PROJECT_ROOT / "data" / "morfflex"


def find_morfflex():
    if not MORFFLEX_DIR.exists():
        return None
    for f in sorted(MORFFLEX_DIR.iterdir()):
        if f.name.endswith(".tsv.xz") or f.name.endswith(".tsv"):
            return f
    return None


def targets(conn):
    """Single-word, non-name-only entry lemmas with no MorfFlex paradigm yet."""
    c = conn.cursor()
    c.execute("SELECT DISTINCT lemma FROM inflections WHERE source = 'morfflex'")
    have = {row[0] for row in c}

    by_lemma = defaultdict(list)
    c.execute("SELECT lemma, entry_json FROM entries")
    for lemma, entry_json in c.fetchall():
        try:
            by_lemma[lemma].append(json.loads(entry_json))
        except (json.JSONDecodeError, TypeError):
            by_lemma[lemma].append({})

    need, skipped_name, skipped_mw = set(), 0, 0
    for lemma, variants in by_lemma.items():
        if " " in lemma:
            skipped_mw += 1          # MorfFlex lemmas are single words
            continue
        if all(is_name_only(v) for v in variants):
            skipped_name += 1        # surname paradigms shadow ordinary words
            continue
        if lemma not in have:
            need.add(lemma)
    return need, have, skipped_name, skipped_mw


def report(conn):
    c = conn.cursor()
    # A Czech noun has ~14 forms (7 cases x 2 numbers), a verb ~60-100, an
    # adjective ~300 with the comparative/superlative grades. A source sitting
    # in single digits was added after the last MorfFlex run.
    print("Average inflected forms per lemma, by entry source and POS\n")
    c.execute("""
        SELECT e.source, e.pos, COUNT(*), ROUND(AVG(COALESCE(i.n, 0)), 1)
        FROM (SELECT DISTINCT lemma, pos, source FROM entries
              WHERE lemma NOT LIKE '% %' AND pos IN ('noun','verb','adjective')) e
        LEFT JOIN (SELECT lemma, COUNT(*) n FROM inflections GROUP BY lemma) i
          ON i.lemma = e.lemma
        GROUP BY e.source, e.pos ORDER BY e.pos, 3 DESC
    """)
    print(f"  {'source':<12}{'pos':<11}{'lemmas':>8}{'avg forms':>11}")
    expected = {"noun": 8.0, "verb": 20.0, "adjective": 40.0}
    for source, pos, lemmas, avg in c.fetchall():
        flag = ("   <-- looks unexpanded"
                if (avg or 0) < expected.get(pos, 8.0) else "")
        print(f"  {source:<12}{pos:<11}{lemmas:>8,}{avg:>11}{flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--report", action="store_true", help="report only")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    need, have, skipped_name, skipped_mw = targets(conn)
    print(f"lemmas with a MorfFlex paradigm: {len(have):,}")
    print(f"multi-word lemmas skipped:       {skipped_mw:,}")
    print(f"name-only lemmas skipped:        {skipped_name:,}")
    # Many of these are words MorfFlex simply does not contain (neologisms,
    # loanwords, colloquial coinages), so this count never reaches zero.
    print(f"lemmas with no MorfFlex paradigm: {len(need):,}\n")

    if args.report or not need:
        report(conn)
        if not need:
            print("\nNothing to do.")
        return 0

    path = find_morfflex()
    if not path:
        sys.exit(f"MorfFlex not found in {MORFFLEX_DIR}. See README step 1.")

    # OFF/OFF is safe here: the script is re-runnable and the caller keeps a
    # backup. It turns a ~40 minute import into a few minutes.
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-400000")

    before = conn.execute("SELECT COUNT(*) FROM inflections").fetchone()[0]
    started = time.time()
    import_morfflex(conn, path, target_lemmas=need)
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM inflections").fetchone()[0]
    print(f"\ninflections {before:,} -> {after:,} (+{after - before:,}) "
          f"in {time.time() - started:.0f}s")

    gained = {row[0] for row in conn.execute(
        "SELECT DISTINCT lemma FROM inflections WHERE source = 'morfflex'")} - have
    print(f"lemmas that gained a paradigm: {len(gained):,} "
          f"({len(need) - len(gained):,} not in MorfFlex)")
    report(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())

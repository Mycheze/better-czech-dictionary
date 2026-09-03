#!/usr/bin/env python3
"""Recover the Svobodné translations that the original import threw away.

build_dictionary.import_svobodne skips a Czech word entirely when Wiktionary
already supplied that (lemma, pos):

    c.execute("SELECT id FROM entries WHERE lemma = ? AND pos = ?", ...)
    if c.fetchone():
        skipped_existing += 1
        continue

That silently discards ~40k translations for ~14.5k words the dictionary already
has -- exactly the words a reader is most likely to look up. This walks the
source file again and merges the missing glosses into the existing entries.

Svobodné is a reversed English->Czech dictionary, so its glosses are bare
synonyms with no disambiguation ("táhnout" -> drag, haul, lug, migrate). They go
into a separate `also_en` list rather than into `senses`, so they add recall
without diluting the curated Wiktionary definitions. The exporters render them
as a trailing "also: ..." line.

Usage:
    python3 tools/merge_svobodne_senses.py [--dry-run]
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "dictionary.db"
SVOBODNE = (PROJECT_ROOT / "data" / "svobodne" /
            "stardict-english-czech-20210401-source" / "en-cs.txt")

MAX_ALSO = 12
# Svobodné rows whose "Czech" column is really an English gloss or a note.
_JUNK_EN = re.compile(r'^[\W\d_]+$')


def read_svobodne(path):
    """czech word -> ordered list of English glosses."""
    out = defaultdict(list)
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            en, cs = parts[0].strip(), parts[1].strip()
            if not en or not cs or _JUNK_EN.match(en):
                continue
            cs = cs.lower()
            if en not in out[cs]:
                out[cs].append(en)
    return out


def normalise(text):
    return re.sub(r'[^a-z ]+', '', (text or "").lower()).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--source", default=str(SVOBODNE))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not Path(args.source).exists():
        sys.exit(f"missing {args.source}")

    svobodne = read_svobodne(args.source)
    print(f"Svobodné: {len(svobodne):,} unique Czech words")

    conn = sqlite3.connect(args.db)
    c = conn.cursor()

    # Every entry row of every lemma, so we can tell what is already covered and
    # pick which row should carry the merged glosses.
    rows = defaultdict(list)  # lemma -> [(id, pos, source, data)]
    c.execute("SELECT id, lemma, pos, source, entry_json FROM entries")
    for entry_id, lemma, pos, source, entry_json in c.fetchall():
        try:
            data = json.loads(entry_json)
        except json.JSONDecodeError:
            continue
        rows[lemma].append((entry_id, pos, source, data))

    updates = []
    added_senses = 0
    for lemma, variants in rows.items():
        candidates = svobodne.get(lemma)
        if not candidates:
            continue

        covered = set()
        for _id, _pos, _source, data in variants:
            for sense in data.get("senses", []):
                covered.add(normalise(sense.get("definition_en")))
            for extra in data.get("also_en", []):
                covered.add(normalise(extra))
        covered.discard("")

        fresh = []
        for gloss in candidates:
            norm = normalise(gloss)
            if not norm or norm in covered:
                continue
            # "a copy" must not be added when "copy (the result of copying)" is
            # already there.
            if any(norm in existing or existing in norm for existing in covered):
                continue
            covered.add(norm)
            fresh.append(gloss)
        if not fresh:
            continue

        # Attach to the richest non-name row -- never to a bare surname entry.
        def rank(variant):
            _id, pos, source, data = variant
            return (pos == "proper_noun", -len(data.get("senses", [])))
        target_id, _pos, _source, data = sorted(variants, key=rank)[0]

        merged = (data.get("also_en") or []) + fresh
        data["also_en"] = merged[:MAX_ALSO]
        updates.append((json.dumps(data, ensure_ascii=False), target_id))
        added_senses += len(fresh)

    print(f"words gaining glosses: {len(updates):,}")
    print(f"glosses added:         {added_senses:,}")

    if args.dry_run:
        for entry_json, entry_id in updates[:8]:
            data = json.loads(entry_json)
            have = [s.get("definition_en") for s in data.get("senses", [])][:3]
            print(f"  {data['lemma']}: HAVE {have} -> also {data['also_en']}")
        print("\n--dry-run: nothing written")
        return 0

    c.executemany("UPDATE entries SET entry_json = ?, "
                  "updated_at = CURRENT_TIMESTAMP WHERE id = ?", updates)
    conn.commit()
    print(f"\nupdated {len(updates):,} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
BetterOfflineDict - Dictionary Builder
Imports data from multiple sources into a unified SQLite database.

Usage:
    python3 build_dictionary.py                # Run full import pipeline
    python3 build_dictionary.py --source X     # Import only source X
    python3 build_dictionary.py --stats        # Show database statistics

Sources:
    kaikki      - Kaikki.org English Wiktionary Czech data (JSONL)
    svobodne    - Svobodne Slovniky English-Czech (tab-delimited)
    tatoeba     - Tatoeba Czech-English sentence pairs
    morfflex    - MorfFlex CZ 2.1 inflection data (TSV)
"""

import sqlite3
import json
import sys
import os
import re
from pathlib import Path
from collections import defaultdict
import argparse

DB_PATH = Path(__file__).parent / "dictionary.db"
DATA_DIR = Path(__file__).parent / "data"


def create_database(db_path=DB_PATH):
    """Create the SQLite database with schema."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lemma TEXT NOT NULL,
            pos TEXT NOT NULL,
            gender TEXT,
            aspect TEXT,
            aspect_pair TEXT,
            frequency TEXT,
            entry_json TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            validated BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS inflections (
            form TEXT NOT NULL,
            lemma TEXT NOT NULL,
            pos TEXT NOT NULL,
            tag TEXT,
            source TEXT DEFAULT 'wiktionary'
        );

        CREATE TABLE IF NOT EXISTS processed_texts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_lemmas INTEGER,
            missing_lemmas INTEGER,
            new_entries_added INTEGER
        );

        CREATE TABLE IF NOT EXISTS word_encounters (
            lemma TEXT NOT NULL,
            text_id INTEGER NOT NULL,
            frequency INTEGER NOT NULL,
            FOREIGN KEY (text_id) REFERENCES processed_texts(id),
            PRIMARY KEY (lemma, text_id)
        );

        CREATE TABLE IF NOT EXISTS review_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL,
            reason TEXT,
            status TEXT DEFAULT 'pending',
            reviewer_notes TEXT,
            FOREIGN KEY (entry_id) REFERENCES entries(id)
        );

        CREATE TABLE IF NOT EXISTS tatoeba_sentences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            czech TEXT NOT NULL,
            english TEXT NOT NULL,
            attribution TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_lemma_pos
            ON entries(lemma, pos);
        CREATE INDEX IF NOT EXISTS idx_entries_lemma
            ON entries(lemma);
        CREATE INDEX IF NOT EXISTS idx_entries_source
            ON entries(source);
        CREATE INDEX IF NOT EXISTS idx_inflections_form
            ON inflections(form);
        CREATE INDEX IF NOT EXISTS idx_inflections_lemma
            ON inflections(lemma);
        CREATE INDEX IF NOT EXISTS idx_tatoeba_czech
            ON tatoeba_sentences(czech);
    """)

    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Kaikki / Wiktionary import
# ---------------------------------------------------------------------------

POS_MAP = {
    "noun": "noun", "verb": "verb", "adj": "adjective",
    "adv": "adverb", "pron": "pronoun", "prep": "preposition",
    "conj": "conjunction", "intj": "interjection", "num": "numeral",
    "particle": "particle", "det": "determiner", "name": "proper_noun",
    "phrase": "phrase", "prefix": "prefix", "suffix": "suffix",
    "character": "character", "proverb": "proverb",
    "prep_phrase": "prepositional_phrase",
    "contraction": "contraction", "combining_form": "combining_form",
    "interfix": "interfix", "symbol": "symbol",
}


def parse_kaikki_gender(entry):
    """Extract gender from kaikki head_templates or tags."""
    for tmpl in entry.get("head_templates", []):
        args = tmpl.get("args", {})
        g = args.get("g", "") or args.get("g1", "")
        if g:
            gender_map = {
                "m": "m_inanim", "m-in": "m_inanim", "m-an": "m_anim",
                "f": "f", "n": "n",
            }
            return gender_map.get(g, g)
    # Try to infer from tags in the first form
    for form in entry.get("forms", []):
        tags = form.get("tags", [])
        if "table-tags" in tags:
            if "masculine" in tags and "animate" in tags:
                return "m_anim"
            elif "masculine" in tags and "inanimate" in tags:
                return "m_inanim"
            elif "masculine" in tags:
                return "m_inanim"  # default masculine
            elif "feminine" in tags:
                return "f"
            elif "neuter" in tags:
                return "n"
    return None


def parse_kaikki_aspect(entry):
    """Extract aspect info from kaikki entry."""
    aspect = None
    aspect_pair = None

    # Check senses for aspect tags
    for sense in entry.get("senses", []):
        tags = sense.get("tags", [])
        if "imperfective" in tags:
            aspect = "imperfective"
        elif "perfective" in tags:
            aspect = "perfective"

    # Check forms for aspect pair
    for form in entry.get("forms", []):
        tags = form.get("tags", [])
        if "perfective" in tags and aspect != "perfective":
            aspect_pair = form.get("form", "")
            if not aspect:
                aspect = "imperfective"
        elif "imperfective" in tags and aspect != "imperfective":
            aspect_pair = form.get("form", "")
            if not aspect:
                aspect = "perfective"

    return aspect, aspect_pair


def parse_kaikki_inflections(entry):
    """Extract inflected forms from kaikki entry."""
    forms = []
    for form_data in entry.get("forms", []):
        form_text = form_data.get("form", "")
        source = form_data.get("source", "")
        tags = form_data.get("tags", [])

        # Skip metadata entries
        if not form_text or form_text in ("no-table-tags",):
            continue
        if "table-tags" in tags or "inflection-template" in tags:
            continue
        # Skip if it's the same as the headword and is just 'canonical'
        if source not in ("declension", "conjugation") and not tags:
            # Still include things like diminutives, perfective pairs
            tag_str = "|".join(tags) if tags else ""
            if tag_str in ("lowercase", "uppercase"):
                continue

        tag_str = "|".join(sorted(tags)) if tags else ""
        forms.append((form_text, tag_str))

    return forms


def import_kaikki(conn, filepath):
    """Import kaikki.org English Wiktionary Czech data."""
    print(f"Importing kaikki data from {filepath}...")
    c = conn.cursor()

    imported = 0
    skipped_dup = 0
    inflections_added = 0
    batch_entries = []
    batch_inflections = []

    # First pass: group entries by word to merge senses
    word_entries = defaultdict(list)
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            word = entry.get("word", "").strip()
            pos = entry.get("pos", "unknown")
            if not word:
                continue
            key = (word.lower(), POS_MAP.get(pos, pos))
            word_entries[key].append(entry)

    print(f"  Found {len(word_entries)} unique word+POS combinations from {sum(len(v) for v in word_entries.values())} entries")

    for (word, pos), entries in word_entries.items():
        # Merge senses from all entries for same word+pos
        all_senses = []
        gender = None
        aspect = None
        aspect_pair = None
        pronunciation = None
        etymology = None
        all_inflections = []

        for entry in entries:
            # Collect senses
            for sense in entry.get("senses", []):
                glosses = sense.get("glosses", [])
                if not glosses:
                    continue
                sense_data = {
                    "definition_en": "; ".join(glosses),
                }
                tags = sense.get("tags", [])
                if tags:
                    # Map common tags to register
                    register_tags = {"colloquial", "informal", "formal",
                                     "literary", "archaic", "vulgar", "slang",
                                     "dated", "rare", "obsolete", "poetic"}
                    register = [t for t in tags if t in register_tags]
                    if register:
                        sense_data["register"] = register[0]

                syns = sense.get("synonyms", [])
                if syns:
                    sense_data["synonyms_cs"] = [s["word"] for s in syns if "word" in s]

                examples = sense.get("examples", [])
                if examples:
                    sense_data["examples"] = []
                    for ex in examples[:3]:
                        ex_data = {}
                        if "text" in ex:
                            ex_data["cs"] = ex["text"]
                        if "english" in ex:
                            ex_data["en"] = ex["english"]
                        elif "translation" in ex:
                            ex_data["en"] = ex["translation"]
                        if ex_data:
                            sense_data["examples"].append(ex_data)

                all_senses.append(sense_data)

            # Gender (nouns)
            if not gender and pos == "noun":
                gender = parse_kaikki_gender(entry)

            # Aspect (verbs)
            if not aspect and pos == "verb":
                aspect, aspect_pair = parse_kaikki_aspect(entry)

            # Pronunciation
            if not pronunciation:
                for sound in entry.get("sounds", []):
                    if "ipa" in sound:
                        pronunciation = sound["ipa"]
                        break

            # Etymology
            if not etymology:
                etymology = entry.get("etymology_text", "")

            # Inflections
            all_inflections.extend(parse_kaikki_inflections(entry))

        if not all_senses:
            continue

        # Build entry JSON
        entry_json = {
            "lemma": word,
            "pos": pos,
            "senses": all_senses,
        }
        if gender:
            entry_json["gender"] = gender
        if aspect:
            entry_json["aspect"] = aspect
        if aspect_pair:
            entry_json["aspect_pair"] = aspect_pair
        if pronunciation:
            entry_json["pronunciation"] = pronunciation
        if etymology:
            entry_json["etymology"] = etymology

        try:
            c.execute(
                """INSERT INTO entries (lemma, pos, gender, aspect, aspect_pair,
                   entry_json, source, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, 'wiktionary', 1.0)""",
                (word, pos, gender, aspect, aspect_pair,
                 json.dumps(entry_json, ensure_ascii=False))
            )
            imported += 1

            # Add inflections
            seen_forms = set()
            for form_text, tag_str in all_inflections:
                form_lower = form_text.lower()
                if form_lower == word.lower():
                    continue  # Skip the lemma itself
                if (form_lower, tag_str) in seen_forms:
                    continue
                seen_forms.add((form_lower, tag_str))
                batch_inflections.append((form_lower, word, pos, tag_str, "wiktionary"))
                inflections_added += 1

        except sqlite3.IntegrityError:
            skipped_dup += 1

        # Batch insert inflections every 5000 entries
        if len(batch_inflections) >= 50000:
            c.executemany(
                "INSERT OR IGNORE INTO inflections (form, lemma, pos, tag, source) VALUES (?, ?, ?, ?, ?)",
                batch_inflections
            )
            batch_inflections = []

    # Final batch
    if batch_inflections:
        c.executemany(
            "INSERT OR IGNORE INTO inflections (form, lemma, pos, tag, source) VALUES (?, ?, ?, ?, ?)",
            batch_inflections
        )

    conn.commit()
    print(f"  Imported: {imported} entries, {inflections_added} inflections")
    print(f"  Skipped (duplicates): {skipped_dup}")
    return imported


# ---------------------------------------------------------------------------
# Svobodne Slovniky import (English->Czech, we reverse it)
# ---------------------------------------------------------------------------

def parse_svobodne_pos(pos_field):
    """Parse Svobodne POS tag like 'n:', 'v:', 'adj:', 'n: pl.' etc."""
    pos_field = pos_field.strip()
    # Remove brackets content like [hovor.], [eko.], [zkr.]
    pos_clean = re.sub(r'\[.*?\]', '', pos_field).strip()
    # Extract just the POS abbreviation (everything before colon or space)
    match = re.match(r'^([a-z]+)', pos_clean)
    pos_key = match.group(1) if match else ""

    mapping = {
        "n": "noun", "v": "verb", "adj": "adjective", "adv": "adverb",
        "prep": "preposition", "conj": "conjunction", "pron": "pronoun",
        "num": "numeral", "interj": "interjection", "abbr": "abbreviation",
        "phrase": "phrase", "prefix": "prefix", "suffix": "suffix",
        "": "unknown",
    }
    return mapping.get(pos_key, pos_key if pos_key else "unknown")


def import_svobodne(conn, filepath):
    """Import Svobodne Slovniky data.

    Format is English-Czech, tab-separated:
    english_word\tczech_translation\tPOS:\textra_info\tauthor

    We reverse this to create Czech->English entries.
    """
    print(f"Importing Svobodne Slovniky from {filepath}...")
    c = conn.cursor()

    # Group by Czech word to merge translations
    cs_entries = defaultdict(list)

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.strip().split("\t")
            if len(parts) < 2:
                continue

            en_word = parts[0].strip()
            cs_word = parts[1].strip()
            pos_raw = parts[2].strip() if len(parts) > 2 else ""
            extra = parts[3].strip() if len(parts) > 3 else ""

            if not cs_word or not en_word:
                continue

            # Extract domain/register info from brackets in extra or pos
            domain = ""
            register = "neutral"
            for match in re.findall(r'\[(.*?)\]', pos_raw + " " + extra):
                m = match.lower().strip(".")
                if m in ("hovor", "hovor.", "slang"):
                    register = "colloquial"
                elif m in ("kniž", "kniž."):
                    register = "literary"
                elif m in ("zast", "zast."):
                    register = "archaic"
                else:
                    domain = match

            pos = parse_svobodne_pos(pos_raw)

            cs_entries[cs_word.lower()].append({
                "en": en_word,
                "pos": pos,
                "register": register,
                "domain": domain,
                "extra": extra,
            })

    print(f"  Found {len(cs_entries)} unique Czech words")

    imported = 0
    skipped_existing = 0

    for cs_word, translations in cs_entries.items():
        # Determine most common POS
        pos_counts = defaultdict(int)
        for t in translations:
            pos_counts[t["pos"]] += 1
        primary_pos = max(pos_counts, key=pos_counts.get)
        if primary_pos == "unknown" and len(pos_counts) > 1:
            pos_counts.pop("unknown")
            primary_pos = max(pos_counts, key=pos_counts.get)

        # Check if already exists from kaikki
        c.execute("SELECT id FROM entries WHERE lemma = ? AND pos = ?",
                  (cs_word, primary_pos))
        if c.fetchone():
            skipped_existing += 1
            continue

        # Build entry
        senses = []
        seen_defs = set()
        for t in translations:
            def_text = t["en"]
            if def_text.lower() in seen_defs:
                continue
            seen_defs.add(def_text.lower())
            sense = {"definition_en": def_text}
            if t["register"] != "neutral":
                sense["register"] = t["register"]
            if t["domain"]:
                sense["domain"] = t["domain"]
            senses.append(sense)

        if not senses:
            continue

        entry_json = {
            "lemma": cs_word,
            "pos": primary_pos,
            "senses": senses,
        }

        try:
            c.execute(
                """INSERT INTO entries (lemma, pos, entry_json, source, confidence)
                   VALUES (?, ?, ?, 'svobodne', 0.9)""",
                (cs_word, primary_pos, json.dumps(entry_json, ensure_ascii=False))
            )
            imported += 1
        except sqlite3.IntegrityError:
            skipped_existing += 1

    conn.commit()
    print(f"  Imported: {imported} entries (new Czech words not in Wiktionary)")
    print(f"  Skipped (already in DB): {skipped_existing}")
    return imported


# ---------------------------------------------------------------------------
# Tatoeba import
# ---------------------------------------------------------------------------

def import_tatoeba(conn, filepath):
    """Import Tatoeba Czech-English sentence pairs."""
    print(f"Importing Tatoeba from {filepath}...")
    c = conn.cursor()

    imported = 0
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) < 2:
                continue
            en_text = parts[0].strip()
            cs_text = parts[1].strip()
            attribution = parts[2].strip() if len(parts) > 2 else ""

            c.execute(
                "INSERT INTO tatoeba_sentences (english, czech, attribution) VALUES (?, ?, ?)",
                (en_text, cs_text, attribution)
            )
            imported += 1

    conn.commit()
    print(f"  Imported: {imported} sentence pairs")
    return imported


# ---------------------------------------------------------------------------
# MorfFlex import
# ---------------------------------------------------------------------------

def import_morfflex(conn, filepath, target_lemmas=None):
    """Import MorfFlex CZ inflection data.

    MorfFlex format: Each line is lemma\ttag\twordform
    We only care about lemma and wordform for our inflection table.
    We only import forms for lemmas that exist in our entries table.

    If target_lemmas is provided (a set of lowercase lemma strings), only import
    inflections for those specific lemmas instead of all entries in the DB.
    """
    print(f"Importing MorfFlex from {filepath}...")
    c = conn.cursor()

    if target_lemmas is not None:
        known_lemmas = target_lemmas
        print(f"  Target lemmas provided: {len(known_lemmas)}")
    else:
        # Get all lemmas we have entries for
        print("  Loading existing lemmas from database...")
        c.execute("SELECT DISTINCT lemma FROM entries")
        known_lemmas = set(row[0] for row in c.fetchall())
        print(f"  Known lemmas in DB: {len(known_lemmas)}")

    # Also build a set of lemmas already having wiktionary inflections
    c.execute("SELECT DISTINCT lemma FROM inflections WHERE source = 'wiktionary'")
    wikt_inflected = set(row[0] for row in c.fetchall())
    print(f"  Lemmas already with Wiktionary inflections: {len(wikt_inflected)}")

    imported = 0
    skipped = 0
    batch = []
    line_count = 0

    # Handle both .tsv and .tsv.xz
    filepath = str(filepath)
    if filepath.endswith('.xz'):
        import lzma
        try:
            opener = lambda: lzma.open(filepath, 'rt', encoding='utf-8')
            # Quick test to make sure it's actually xz data
            with opener() as test_f:
                test_f.readline()
        except Exception as e:
            print(f"  ERROR: File is not valid xz data: {e}")
            print(f"  MorfFlex requires manual download from LINDAT (license agreement).")
            print(f"  Visit: https://lindat.mff.cuni.cz/repository/xmlui/handle/11234/1-5833")
            print(f"  Download czech-morfflex-2.1.tsv.xz and place in {Path(filepath).parent}/")
            return 0
        opener = lambda: lzma.open(filepath, 'rt', encoding='utf-8')
    else:
        opener = lambda: open(filepath, 'r', encoding='utf-8')

    with opener() as f:
        for line in f:
            line_count += 1
            if line_count % 5_000_000 == 0:
                print(f"  Processed {line_count:,} lines, imported {imported:,} inflections...")

            line = line.strip()
            if not line or line.startswith('#'):
                continue

            parts = line.split('\t')
            if len(parts) < 3:
                continue

            lemma_raw = parts[0]
            tag = parts[1]
            wordform = parts[2].lower()

            # MorfFlex lemmas: "město", "a-1", "být_:T", "Aabar_;Y", etc.
            # Strip underscore-metadata first, then numeric disambiguation suffix
            lemma = re.sub(r'[_`].*$', '', lemma_raw)  # "být_:T" -> "být"
            lemma = re.sub(r'-\d+$', '', lemma)          # "a-1" -> "a"
            lemma = lemma.lower()

            if lemma not in known_lemmas:
                skipped += 1
                continue

            if wordform == lemma:
                continue  # Skip the lemma form itself

            # Determine POS from tag (first character)
            pos_char = tag[0] if tag else ''
            pos_map = {
                'N': 'noun', 'A': 'adjective', 'V': 'verb', 'D': 'adverb',
                'P': 'pronoun', 'C': 'numeral', 'R': 'preposition',
                'J': 'conjunction', 'I': 'interjection', 'T': 'particle',
            }
            pos = pos_map.get(pos_char, 'unknown')

            batch.append((wordform, lemma, pos, tag, 'morfflex'))
            imported += 1

            if len(batch) >= 100_000:
                c.executemany(
                    "INSERT OR IGNORE INTO inflections (form, lemma, pos, tag, source) VALUES (?, ?, ?, ?, ?)",
                    batch
                )
                batch = []

    if batch:
        c.executemany(
            "INSERT OR IGNORE INTO inflections (form, lemma, pos, tag, source) VALUES (?, ?, ?, ?, ?)",
            batch
        )

    conn.commit()
    print(f"  Processed {line_count:,} total lines")
    print(f"  Imported: {imported:,} inflections for known lemmas")
    print(f"  Skipped: {skipped:,} (lemma not in dictionary)")
    return imported


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def print_stats(conn):
    """Print database statistics."""
    c = conn.cursor()

    print("\n" + "=" * 70)
    print("DICTIONARY DATABASE STATISTICS")
    print("=" * 70)

    c.execute("SELECT COUNT(*) FROM entries")
    total_entries = c.fetchone()[0]

    c.execute("SELECT COUNT(DISTINCT lemma) FROM entries")
    unique_lemmas = c.fetchone()[0]

    c.execute("SELECT source, COUNT(*) FROM entries GROUP BY source ORDER BY COUNT(*) DESC")
    source_counts = c.fetchall()

    c.execute("SELECT pos, COUNT(*) FROM entries GROUP BY pos ORDER BY COUNT(*) DESC")
    pos_counts = c.fetchall()

    c.execute("SELECT COUNT(*) FROM inflections")
    total_inflections = c.fetchone()[0]

    c.execute("SELECT COUNT(DISTINCT form) FROM inflections")
    unique_forms = c.fetchone()[0]

    c.execute("SELECT source, COUNT(*) FROM inflections GROUP BY source ORDER BY COUNT(*) DESC")
    infl_source_counts = c.fetchall()

    c.execute("SELECT COUNT(*) FROM tatoeba_sentences")
    tatoeba_count = c.fetchone()[0]

    print(f"\nEntries:")
    print(f"  Total entries: {total_entries:,}")
    print(f"  Unique lemmas: {unique_lemmas:,}")
    print(f"\n  By source:")
    for source, count in source_counts:
        print(f"    {source}: {count:,}")
    print(f"\n  By POS:")
    for pos, count in pos_counts[:15]:
        print(f"    {pos}: {count:,}")

    print(f"\nInflections:")
    print(f"  Total form-lemma mappings: {total_inflections:,}")
    print(f"  Unique inflected forms: {unique_forms:,}")
    print(f"\n  By source:")
    for source, count in infl_source_counts:
        print(f"    {source}: {count:,}")

    print(f"\nTatoeba sentences: {tatoeba_count:,}")

    # Coverage test against known_words.txt if it exists
    known_path = Path(__file__).parent / "known_words.txt"
    if known_path.exists():
        with open(known_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        known = set(w.strip().lower() for w in lines[1:] if w.strip())  # skip header
        c.execute("SELECT DISTINCT lemma FROM entries")
        dict_lemmas = set(row[0] for row in c.fetchall())
        c.execute("SELECT DISTINCT form FROM inflections")
        dict_forms = set(row[0] for row in c.fetchall())
        all_lookupable = dict_lemmas | dict_forms

        covered = known & all_lookupable
        missing = known - all_lookupable
        print(f"\nCoverage of your known words ({len(known):,}):")
        print(f"  Covered: {len(covered):,} ({len(covered)/len(known)*100:.1f}%)")
        print(f"  Missing: {len(missing):,} ({len(missing)/len(known)*100:.1f}%)")
        if missing:
            sample = sorted(missing)[:20]
            print(f"  Sample missing: {', '.join(sample)}")

    print("\n" + "=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build BetterOfflineDict database")
    parser.add_argument("--source", choices=["kaikki", "svobodne", "tatoeba", "morfflex", "all"],
                        default="all", help="Which source to import")
    parser.add_argument("--stats", action="store_true", help="Show database statistics")
    parser.add_argument("--reset", action="store_true", help="Reset database before import")
    args = parser.parse_args()

    if args.reset and DB_PATH.exists():
        print(f"Resetting database at {DB_PATH}")
        DB_PATH.unlink()

    conn = create_database()

    if args.stats:
        print_stats(conn)
        conn.close()
        return

    sources_to_import = [args.source] if args.source != "all" else ["kaikki", "svobodne", "tatoeba", "morfflex"]

    for source in sources_to_import:
        if source == "kaikki":
            filepath = DATA_DIR / "kaikki" / "kaikki-czech-en.jsonl"
            if filepath.exists():
                import_kaikki(conn, filepath)
            else:
                print(f"  SKIP: {filepath} not found")

        elif source == "svobodne":
            filepath = DATA_DIR / "svobodne" / "stardict-english-czech-20210401-source" / "en-cs.txt"
            if filepath.exists():
                import_svobodne(conn, filepath)
            else:
                print(f"  SKIP: {filepath} not found")

        elif source == "tatoeba":
            filepath = DATA_DIR / "tatoeba" / "ces.txt"
            if filepath.exists():
                import_tatoeba(conn, filepath)
            else:
                print(f"  SKIP: {filepath} not found")

        elif source == "morfflex":
            # Try both compressed and uncompressed, and any version
            filepath = None
            morfflex_dir = DATA_DIR / "morfflex"
            if morfflex_dir.exists():
                for f in sorted(morfflex_dir.iterdir()):
                    if f.name.endswith('.tsv.xz') or (f.name.endswith('.tsv') and not f.name.endswith('.tsv.xz')):
                        filepath = f
                        break
            if filepath and filepath.exists():
                import_morfflex(conn, filepath)
            else:
                print(f"  SKIP: MorfFlex not found.")
                print(f"  Download from: https://lindat.mff.cuni.cz/repository/xmlui/handle/11234/1-5833")
                print(f"  (Requires accepting CC BY-NC-SA 4.0 license)")
                print(f"  Place the .tsv.xz file in: {morfflex_dir}/")

    print_stats(conn)
    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()

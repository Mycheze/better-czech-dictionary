#!/usr/bin/env python3
"""
Export dictionary database to Yomitan format (formerly Yomichan).

Produces a ZIP file containing term_bank JSON files, a tag_bank, and
index.json metadata. The ZIP can be imported directly into Yomitan
(browser extension for reading Japanese/other languages).

Each inflected form gets its own term entry pointing to the lemma's
definition, so any word form can be looked up.

Usage:
    python3 export_yomitan.py [--output-dir DIR] [--dict-name NAME]
"""

import sqlite3
import json
import os
import re
import html
import argparse
import zipfile
from pathlib import Path
from collections import defaultdict

from export_stardict import (
    classify_entry_senses,
    resolve_crossref_entry,
)

DB_PATH = Path(__file__).parent / "dictionary.db"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "output" / "yomitan"
DEFAULT_DICT_NAME = "Czech-English"

# Yomitan term banks should stay under ~10MB each for performance
TERMS_PER_BANK = 50_000

# POS tag display names and categories
POS_TAGS = {
    "noun": ("noun", "partOfSpeech", "noun", 0),
    "verb": ("verb", "partOfSpeech", "verb", 0),
    "adjective": ("adj", "partOfSpeech", "adjective", 0),
    "adverb": ("adv", "partOfSpeech", "adverb", 0),
    "pronoun": ("pron", "partOfSpeech", "pronoun", 0),
    "preposition": ("prep", "partOfSpeech", "preposition", 0),
    "conjunction": ("conj", "partOfSpeech", "conjunction", 0),
    "interjection": ("intj", "partOfSpeech", "interjection", 0),
    "numeral": ("num", "partOfSpeech", "numeral", 0),
    "particle": ("part", "partOfSpeech", "particle", 0),
    "determiner": ("det", "partOfSpeech", "determiner", 0),
    "proper_noun": ("name", "name", "proper noun", 0),
    "phrase": ("phrase", "expression", "phrase", 0),
    "prefix": ("prefix", "partOfSpeech", "prefix", 0),
    "suffix": ("suffix", "partOfSpeech", "suffix", 0),
    "abbreviation": ("abbr", "partOfSpeech", "abbreviation", 0),
    "proverb": ("prov", "expression", "proverb", 0),
}


def format_structured_content(entry_data, is_inflection=False, lemma_ref=None):
    """Build a Yomitan structured-content definition from an entry.

    Returns a list of definition objects (strings or structured-content dicts).
    """
    definitions = []

    # If this is an inflection entry, add a reference to the lemma
    if is_inflection and lemma_ref:
        definitions.append({
            "type": "structured-content",
            "content": [
                {"tag": "span", "style": {"fontSize": "85%", "color": "#888"}, "content": "\u2192 "},
                {"tag": "b", "content": lemma_ref},
            ]
        })

    senses = entry_data.get("senses", [])
    if not senses:
        return definitions

    if len(senses) == 1:
        s = senses[0]
        defn = s.get("definition_en", "")
        register = s.get("register", "")

        content = []
        if register and register != "neutral":
            content.append({"tag": "span", "style": {"fontSize": "85%", "color": "#888"}, "content": f"[{register}] "})
        content.append(defn)

        examples = s.get("examples", [])
        for ex in examples[:2]:
            cs = ex.get("cs", "")
            en = ex.get("en", "")
            if cs:
                ex_parts = [{"tag": "i", "content": cs}]
                if en:
                    ex_parts.append(f" \u2014 {en}")
                content.append({"tag": "div", "style": {"fontSize": "85%", "color": "#555"}, "content": ex_parts})

        definitions.append({"type": "structured-content", "content": content})
    else:
        content = []
        for i, s in enumerate(senses, 1):
            defn = s.get("definition_en", "")
            register = s.get("register", "")
            reg_str = f"[{register}] " if register and register != "neutral" else ""

            sense_content = [f"{i}. {reg_str}{defn}"]

            examples = s.get("examples", [])
            for ex in examples[:1]:
                cs = ex.get("cs", "")
                en = ex.get("en", "")
                if cs:
                    ex_text = cs
                    if en:
                        ex_text += f" \u2014 {en}"
                    sense_content.append({"tag": "div", "style": {"fontSize": "85%", "color": "#555", "fontStyle": "italic"}, "content": ex_text})

            content.append({"tag": "div", "content": sense_content})

        definitions.append({"type": "structured-content", "content": content})

    return definitions


def build_compact_definition(entry_data, lemma_ref):
    """Build a compact definition for an inflected form entry."""
    senses = entry_data.get("senses", [])
    first_def = senses[0].get("definition_en", "") if senses else ""
    if not first_def:
        return [f"\u2192 {lemma_ref}"]

    content = [
        {"tag": "span", "style": {"fontSize": "85%", "color": "#888"}, "content": f"\u2192 {lemma_ref}: "},
        first_def,
    ]
    return [{"type": "structured-content", "content": content}]


def export_yomitan(db_path=DB_PATH, output_dir=DEFAULT_OUTPUT_DIR, dict_name=DEFAULT_DICT_NAME):
    """Export database to Yomitan dictionary format."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Load entries
    print("Loading entries from database...")
    c.execute("SELECT lemma, pos, entry_json, source FROM entries ORDER BY lemma")
    entries = c.fetchall()
    print(f"  {len(entries)} entries loaded")

    # Load inflections
    print("Loading inflections...")
    c.execute("SELECT form, lemma, pos FROM inflections")
    inflections = c.fetchall()
    print(f"  {len(inflections)} inflection mappings loaded")

    # Build entries_by_lemma for cross-reference resolution
    print("Resolving cross-references...")
    entries_by_lemma = defaultdict(list)
    entry_data_cache = {}
    lemma_by_name = defaultdict(list)

    for entry in entries:
        key = (entry["lemma"], entry["pos"])
        try:
            entry_data = json.loads(entry["entry_json"])
            entries_by_lemma[entry["lemma"].lower()].append(entry_data)
            entry_data_cache[key] = entry_data
            lemma_by_name[entry["lemma"]].append(key)
        except json.JSONDecodeError:
            pass

    # Resolve cross-references
    resolved_entries = {}  # key -> resolved entry_data
    resolved_count = 0

    for entry in entries:
        key = (entry["lemma"], entry["pos"])
        entry_data = entry_data_cache.get(key)
        if not entry_data:
            continue

        real_senses, xref_senses = classify_entry_senses(entry_data)

        if xref_senses and not real_senses:
            resolved_html, _ = resolve_crossref_entry(entry_data, entries_by_lemma)
            if resolved_html:
                # For Yomitan, we want the resolved entry_data, not HTML
                # Try to find the target and use its real senses
                for sense in entry_data.get("senses", []):
                    defn = sense.get("definition_en", "")
                    from export_stardict import parse_crossref
                    _, target, embedded = parse_crossref(defn)
                    if target:
                        target_entries = entries_by_lemma.get(target, [])
                        for te in target_entries:
                            real_s, _ = classify_entry_senses(te)
                            if real_s:
                                resolved = dict(entry_data)
                                if embedded:
                                    resolved["senses"] = [{"definition_en": embedded}] + real_s
                                else:
                                    resolved["senses"] = real_s
                                resolved_entries[key] = resolved
                                resolved_count += 1
                                break
                        if key in resolved_entries:
                            break
        elif xref_senses and real_senses:
            resolved = dict(entry_data)
            resolved["senses"] = real_senses
            resolved_entries[key] = resolved

    print(f"  Resolved {resolved_count} cross-reference entries")

    # Build term entries
    print("Building term entries...")
    term_entries = []  # list of 8-element arrays
    sequence = 0

    # Lemma entries
    for entry in entries:
        key = (entry["lemma"], entry["pos"])
        entry_data = resolved_entries.get(key, entry_data_cache.get(key))
        if not entry_data:
            continue

        sequence += 1
        pos = entry["pos"]
        tag_name = POS_TAGS.get(pos, (pos, "partOfSpeech", pos, 0))[0] if pos else ""

        definitions = format_structured_content(entry_data)
        if not definitions:
            continue

        # [expression, reading, definitionTags, rules, score, definitions, sequence, termTags]
        term_entries.append([
            entry["lemma"],  # expression
            "",              # reading (not needed for Czech)
            tag_name,        # definition tags (POS)
            "",              # rules
            0,               # score
            definitions,     # definitions
            sequence,        # sequence number
            "",              # term tags
        ])

    print(f"  {len(term_entries)} lemma entries")

    # Inflection entries (compact definitions pointing to lemma)
    print("Building inflection entries...")
    inflection_groups = defaultdict(list)
    for infl in inflections:
        inflection_groups[infl["form"]].append((infl["lemma"], infl["pos"]))

    lemma_words = {entry["lemma"].lower() for entry in entries}
    infl_count = 0

    for form, lemma_list in inflection_groups.items():
        # Skip if form is already a lemma entry
        if form in lemma_words:
            continue

        # Build compact definition from all possible lemmas
        definitions = []
        seen_lemmas = set()
        for lemma, pos in lemma_list:
            if lemma in seen_lemmas:
                continue
            seen_lemmas.add(lemma)

            key = (lemma, pos)
            entry_data = resolved_entries.get(key, entry_data_cache.get(key))
            if not entry_data:
                # Try any POS for this lemma
                for alt_key in lemma_by_name.get(lemma, []):
                    entry_data = resolved_entries.get(alt_key, entry_data_cache.get(alt_key))
                    if entry_data:
                        break

            if entry_data:
                definitions.extend(build_compact_definition(entry_data, lemma))

        if definitions:
            sequence += 1
            term_entries.append([
                form,        # expression
                "",          # reading
                "",          # definition tags
                "",          # rules
                -1,          # score (lower than lemma entries)
                definitions, # definitions
                sequence,    # sequence number
                "",          # term tags
            ])
            infl_count += 1

    print(f"  {infl_count} inflection entries")
    print(f"  {len(term_entries)} total entries")

    # Build tag bank
    tag_bank = []
    for pos, (name, category, description, order) in POS_TAGS.items():
        tag_bank.append([name, category, order, description, 0])

    # Write everything to ZIP
    print("Writing Yomitan ZIP...")
    zip_path = output_dir / f"{dict_name}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # index.json
        index = {
            "title": f"{dict_name}",
            "format": 3,
            "revision": "1.0.0",
            "sequenced": True,
            "author": "BetterOfflineDict",
            "url": "https://github.com/Mycheze/better-czech-dictionary",
            "description": f"Czech-English dictionary. {len(entries):,} entries, {len(inflections):,} inflection mappings.",
            "attribution": "Wiktionary (CC BY-SA), Svobodne Slovniky (GPL), MorfFlex CZ 2.1 (CC BY-NC-SA 4.0), Tatoeba (CC BY 2.0), DeepSeek.",
        }
        zf.writestr("index.json", json.dumps(index, ensure_ascii=False, indent=2))

        # tag_bank_1.json
        zf.writestr("tag_bank_1.json", json.dumps(tag_bank, ensure_ascii=False))

        # term_bank files
        bank_num = 1
        for i in range(0, len(term_entries), TERMS_PER_BANK):
            chunk = term_entries[i:i + TERMS_PER_BANK]
            zf.writestr(f"term_bank_{bank_num}.json", json.dumps(chunk, ensure_ascii=False))
            bank_num += 1

    zip_size = os.path.getsize(zip_path)

    conn.close()

    # Summary
    print(f"\n{'='*60}")
    print(f"Yomitan export complete!")
    print(f"{'='*60}")
    print(f"  Output: {zip_path}")
    print(f"  Size: {zip_size / 1024 / 1024:.1f} MB")
    print(f"  Lemma entries: {len(term_entries) - infl_count:,}")
    print(f"  Inflection entries: {infl_count:,}")
    print(f"  Total entries: {len(term_entries):,}")
    print(f"  Term banks: {bank_num - 1}")
    print(f"\n  Import in Yomitan: Settings > Dictionaries > Configure installed and enabled dictionaries > Import")

    return len(term_entries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export to Yomitan format")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--dict-name", default=DEFAULT_DICT_NAME)
    args = parser.parse_args()
    export_yomitan(output_dir=args.output_dir, dict_name=args.dict_name)

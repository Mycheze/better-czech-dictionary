#!/usr/bin/env python3
"""
Export dictionary database to StarDict format for KOReader/GoldenDict.

Strategy: Since KOReader has no morphological analysis, we create a separate
entry for each inflected form pointing to the lemma's definition.
This uses the "merged synonyms" approach for best lookup performance.

Usage:
    python3 export_stardict.py [--output-dir DIR] [--dict-name NAME]
"""

import sqlite3
import json
import struct
import os
import sys
import re
import argparse
import html
from pathlib import Path
from collections import defaultdict

DB_PATH = Path(__file__).parent / "dictionary.db"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "output" / "stardict"
DEFAULT_DICT_NAME = "Czech-English"


# ---------------------------------------------------------------------------
# Cross-reference detection and resolution
# ---------------------------------------------------------------------------

# Keywords that indicate a definition is a cross-reference, not a real definition
_XREF_KEYWORDS = {
    "inflection", "form", "plural", "singular", "diminutive", "augmentative",
    "abbreviation", "clipping", "participle", "imperative", "transgressive",
    "present", "conditional", "possessive", "accusative", "genitive", "dative",
    "nominative", "vocative", "instrumental", "locative", "feminine", "masculine",
    "neuter", "animate", "inanimate", "imperfective", "perfective", "comparative",
    "superlative", "alternative", "obsolete", "archaic", "dated", "short",
    "verbal", "noun", "past", "active", "passive", "degree", "spelling",
}

_XREF_RE = re.compile(
    r'^([\w\s/,-]+?)\s+of\s+(.+)$', re.IGNORECASE
)


def is_crossref_sense(definition_en):
    """Return True if a definition is a cross-reference rather than a real definition."""
    if not definition_en:
        return False
    m = _XREF_RE.match(definition_en)
    if not m:
        return False
    prefix = m.group(1).strip().lower()
    # Check that the prefix consists entirely of known grammatical/relationship terms
    prefix_words = set(re.split(r'[\s/,-]+', prefix))
    return prefix_words.issubset(_XREF_KEYWORDS)


def parse_crossref(definition_en):
    """Parse a cross-reference definition.

    Returns (relationship_type, target_lemma, embedded_definition).
    embedded_definition is None if there's no inline definition after the target.
    """
    m = _XREF_RE.match(definition_en)
    if not m:
        return None, None, None

    prefix = m.group(1).strip().lower()
    remainder = m.group(2).strip()

    # Classify relationship
    if "diminutive" in prefix:
        rel = "dim."
    elif "augmentative" in prefix:
        rel = "aug."
    elif "comparative" in prefix or "superlative" in prefix:
        rel = "comp." if "comparative" in prefix else "sup."
    elif "alternative" in prefix or "obsolete" in prefix or "archaic" in prefix or "dated" in prefix or "spelling" in prefix:
        rel = "="
    elif "abbreviation" in prefix or "clipping" in prefix:
        rel = "abbr."
    elif "imperfective" in prefix or "perfective" in prefix:
        rel = "asp."
    else:
        rel = None  # plain inflection, no tag needed

    # Parse target lemma and embedded definition from remainder
    # Patterns:
    #   "absurdita:; nominative/accusative..."  -> target="absurdita", embedded=None
    #   "hrad; small castle"                    -> target="hrad", embedded="small castle"
    #   "absint ("absinthe")"                   -> target="absint", embedded="absinthe"
    #   "nízký; lower"                          -> target="nízký", embedded="lower"

    # Strip trailing colon (common in "inflection of X:" patterns)
    embedded = None
    target = remainder

    # Handle semicolon: text after semicolon may be embedded definition or grammatical info
    if ";" in target:
        before_semi, after_semi = target.split(";", 1)
        after_semi = after_semi.strip()
        # If the part after semicolon looks like grammatical info, ignore it
        after_words = set(re.split(r'[\s/,-]+', after_semi.lower()))
        if not after_words.issubset(_XREF_KEYWORDS | {"", "of", "the", "a", "an"}):
            embedded = after_semi
        target = before_semi.strip()

    # Handle parenthetical: "absint ("absinthe")" or "zase ("again")"
    paren_match = re.match(r'^(.+?)\s*\("?([^"]+?)"?\)\s*$', target)
    if paren_match:
        target = paren_match.group(1).strip()
        if not embedded:
            embedded = paren_match.group(2).strip()

    # Handle colon separator: "ten: it, this, that" -> target="ten", embedded="it, this, that"
    if ":" in target and not embedded:
        before_colon, after_colon = target.split(":", 1)
        after_colon = after_colon.strip()
        if after_colon and not re.match(r'^[\s;,]*$', after_colon):
            # Looks like an embedded definition after the colon
            after_words = set(re.split(r'[\s/,-]+', after_colon.lower()))
            if not after_words.issubset(_XREF_KEYWORDS | {"", "of", "the", "a", "an"}):
                embedded = after_colon
                target = before_colon.strip()

    # Clean target: strip trailing colon, quotes, whitespace
    target = target.rstrip(":").strip().strip('"').strip("'").lower()

    return rel, target, embedded


def classify_entry_senses(entry_data):
    """Split an entry's senses into real definitions and cross-references."""
    real = []
    xref = []
    for sense in entry_data.get("senses", []):
        defn = sense.get("definition_en", "")
        if is_crossref_sense(defn):
            xref.append(sense)
        else:
            real.append(sense)
    return real, xref


def resolve_crossref_entry(entry_data, entries_by_lemma):
    """Resolve a cross-reference-only entry to the target's real definition.

    Returns (resolved_html, resolved_compact) or (None, None) if unresolvable.
    entries_by_lemma: dict mapping lowercase lemma -> list of parsed entry_json dicts
    """
    xref_senses = entry_data.get("senses", [])
    if not xref_senses:
        return None, None

    # Try each sense to find a resolvable target
    for sense in xref_senses:
        defn = sense.get("definition_en", "")
        rel, target, embedded = parse_crossref(defn)
        if not target:
            continue

        # Look up the target entry
        target_entries = entries_by_lemma.get(target, [])
        target_entry = None
        for te in target_entries:
            # Prefer entries with real definitions
            real_s, xref_s = classify_entry_senses(te)
            if real_s:
                target_entry = te
                break
        if not target_entry and target_entries:
            target_entry = target_entries[0]

        if target_entry:
            real_senses, _ = classify_entry_senses(target_entry)
            if real_senses:
                # Build resolved entry: use target's real senses
                resolved = dict(entry_data)
                if embedded:
                    # Use embedded def as primary, target senses as context
                    resolved["senses"] = [{"definition_en": embedded}] + real_senses
                else:
                    resolved["senses"] = real_senses

                # Build HTML with relationship indicator
                target_display = target_entry.get("lemma", target)
                tag = f"<small>({rel} {html.escape(target_display)})</small> " if rel else ""
                prefix = f"<small>→ {html.escape(target_display)}</small><br>"

                resolved_html = prefix + tag + _format_senses_html(real_senses, embedded)
                first_def = embedded or real_senses[0].get("definition_en", "")
                resolved_compact = f"<b>{html.escape(target_display)}</b>: {html.escape(first_def)}"
                return resolved_html, resolved_compact

        elif embedded:
            # No target found, but we have an embedded definition
            resolved_html = html.escape(embedded)
            resolved_compact = html.escape(embedded)
            return resolved_html, resolved_compact

    return None, None


def _format_senses_html(senses, embedded=None):
    """Format a list of senses as HTML (lightweight version for resolved entries)."""
    parts = []
    if embedded:
        parts.append(html.escape(embedded))

    display_senses = senses
    if len(display_senses) == 1:
        defn = display_senses[0].get("definition_en", "")
        if not embedded or defn != embedded:
            parts.append(html.escape(defn))
    else:
        for i, s in enumerate(display_senses[:3], 1):
            defn = s.get("definition_en", "")
            if embedded and defn == embedded:
                continue
            register = s.get("register", "")
            reg_str = f"[{register}] " if register and register != "neutral" else ""
            parts.append(f"{i}. {reg_str}{html.escape(defn)}")
    return "<br>".join(parts)


def format_entry_html(entry_json, lemma, pos):
    """Format a dictionary entry as compact HTML for StarDict."""
    try:
        entry = json.loads(entry_json) if isinstance(entry_json, str) else entry_json
    except json.JSONDecodeError:
        return f"<b>{html.escape(lemma)}</b> — (error parsing entry)"

    parts = []

    # Header: lemma + POS + gender/aspect
    header = f"<b>{html.escape(entry.get('lemma', lemma))}</b>"
    pos_display = entry.get("pos", pos)
    if pos_display and pos_display not in ("unknown", "pl", ""):
        header += f" <i>{html.escape(pos_display)}</i>"

    gender = entry.get("gender", "")
    if gender:
        gender_display = {"m_anim": "m.anim", "m_inanim": "m.inanim", "f": "f", "n": "n"}.get(gender, gender)
        header += f" <small>({gender_display})</small>"

    aspect = entry.get("aspect", "")
    if aspect:
        header += f" <small>[{aspect[:4]}.]</small>"
        pair = entry.get("aspect_pair", "")
        if pair:
            header += f" <small>(→ {html.escape(pair)})</small>"

    pronunciation = entry.get("pronunciation", "")
    if pronunciation:
        header += f" <small>{html.escape(pronunciation)}</small>"

    parts.append(header)

    # Senses
    senses = entry.get("senses", [])
    if len(senses) == 1:
        s = senses[0]
        defn = s.get("definition_en", "")
        register = s.get("register", "")
        reg_str = f"<small>[{register}]</small> " if register and register != "neutral" else ""
        parts.append(f"{reg_str}{html.escape(defn)}")

        examples = s.get("examples", [])
        for ex in examples[:2]:
            cs = ex.get("cs", "")
            en = ex.get("en", "")
            if cs:
                ex_str = f"<small><i>{html.escape(cs)}</i>"
                if en:
                    ex_str += f" — {html.escape(en)}"
                ex_str += "</small>"
                parts.append(ex_str)
    else:
        for i, s in enumerate(senses, 1):
            defn = s.get("definition_en", "")
            register = s.get("register", "")
            reg_str = f"[{register}] " if register and register != "neutral" else ""
            parts.append(f"{i}. {reg_str}{html.escape(defn)}")

            examples = s.get("examples", [])
            for ex in examples[:1]:
                cs = ex.get("cs", "")
                en = ex.get("en", "")
                if cs:
                    ex_str = f"<small>  <i>{html.escape(cs)}</i>"
                    if en:
                        ex_str += f" — {html.escape(en)}"
                    ex_str += "</small>"
                    parts.append(ex_str)

    # Notes
    notes = entry.get("notes", "")
    if notes:
        parts.append(f"<small>Note: {html.escape(notes)}</small>")

    return "<br>".join(parts)


def format_inflection_html(lemma, pos, form, entry_html):
    """Format an inflection entry that redirects to the lemma."""
    return f"<small>→ <b>{html.escape(lemma)}</b></small><br>{entry_html}"


def export_stardict(db_path=DB_PATH, output_dir=DEFAULT_OUTPUT_DIR, dict_name=DEFAULT_DICT_NAME):
    """Export database to StarDict format."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Collect all entries
    print("Loading entries from database...")
    c.execute("SELECT lemma, pos, entry_json, source FROM entries ORDER BY lemma")
    entries = c.fetchall()
    print(f"  {len(entries)} entries loaded")

    # Collect all inflections
    print("Loading inflections...")
    c.execute("SELECT form, lemma, pos FROM inflections")
    inflections = c.fetchall()
    print(f"  {len(inflections)} inflection mappings loaded")

    # Build lemma -> entry_html lookup and lemma -> compact summary
    print("Formatting entries...")
    lemma_html = {}      # (lemma, pos) -> full html
    lemma_compact = {}   # (lemma, pos) -> compact one-liner for inflection entries
    lemma_by_name = defaultdict(list)  # lemma_str -> [(lemma, pos)]

    # Also build entries_by_lemma for cross-reference resolution
    entries_by_lemma = defaultdict(list)  # lowercase lemma -> [parsed entry_json dicts]

    for entry in entries:
        key = (entry["lemma"], entry["pos"])
        lemma_html[key] = format_entry_html(entry["entry_json"], entry["lemma"], entry["pos"])
        lemma_by_name[entry["lemma"]].append(key)

        # Build compact summary for inflection redirects (no POS/gender for cleaner display)
        try:
            entry_data = json.loads(entry["entry_json"])
            entries_by_lemma[entry["lemma"].lower()].append(entry_data)
            senses = entry_data.get("senses", [])
            first_def = senses[0].get("definition_en", "") if senses else ""
            compact = f"<b>{html.escape(entry['lemma'])}</b>: {html.escape(first_def)}"
        except:
            compact = f"<b>{html.escape(entry['lemma'])}</b>"
        lemma_compact[key] = compact

    # Resolve cross-reference entries
    print("Resolving cross-reference entries...")
    resolved_count = 0
    mixed_fixed = 0

    for entry in entries:
        key = (entry["lemma"], entry["pos"])
        try:
            entry_data = json.loads(entry["entry_json"])
        except:
            continue

        real_senses, xref_senses = classify_entry_senses(entry_data)

        if xref_senses and not real_senses:
            # All senses are cross-references: resolve to target
            resolved_html, resolved_compact = resolve_crossref_entry(
                entry_data, entries_by_lemma
            )
            if resolved_html:
                lemma_html[key] = resolved_html
                lemma_compact[key] = resolved_compact
                resolved_count += 1

        elif xref_senses and real_senses:
            # Mixed entry: strip crossref senses, keep real ones
            filtered = dict(entry_data)
            filtered["senses"] = real_senses
            lemma_html[key] = format_entry_html(filtered, entry["lemma"], entry["pos"])
            first_def = real_senses[0].get("definition_en", "")
            lemma_compact[key] = f"<b>{html.escape(entry['lemma'])}</b>: {html.escape(first_def)}"
            mixed_fixed += 1

    print(f"  Resolved {resolved_count} cross-reference entries")
    print(f"  Fixed {mixed_fixed} mixed entries")

    # Build the full word list: lemma entries + inflected form entries
    # Each item is (headword, definition_html)
    print("Building word list with inflected forms...")
    word_list = []

    # Add lemma entries
    for entry in entries:
        word_list.append((entry["lemma"].lower(), lemma_html[(entry["lemma"], entry["pos"])]))

    # Add inflected form entries (compact: first sense only to save space)
    print("Building compact inflection entries...")
    inflection_groups = defaultdict(list)
    for infl in inflections:
        inflection_groups[infl["form"]].append((infl["lemma"], infl["pos"]))

    for form, lemma_list in inflection_groups.items():
        # Build COMPACT definition for inflected forms
        parts = []
        seen_lemmas = set()
        for lemma, pos in lemma_list:
            if lemma in seen_lemmas:
                continue
            seen_lemmas.add(lemma)
            key = (lemma, pos)
            if key in lemma_compact:
                parts.append(lemma_compact[key])
            else:
                # Try any POS for this lemma
                alt_keys = lemma_by_name.get(lemma, [])
                for alt_key in alt_keys:
                    if alt_key in lemma_compact:
                        parts.append(lemma_compact[alt_key])
                        break

        if parts:
            final_html = "<br>".join(parts)
            word_list.append((form, final_html))

    # Deduplicate: if an inflected form is the same as a lemma, prefer the lemma entry
    # We tag entries so we can distinguish lemma entries from inflection entries
    print("Deduplicating...")
    word_dict = {}    # word -> definition html
    word_is_lemma = {} # word -> bool (is this a lemma entry?)

    # Add lemma entries first (they take priority)
    lemma_words = set()
    for word, definition in word_list[:len(entries)]:  # First N items are lemma entries
        if word in word_dict:
            # Multiple POS for same lemma - merge
            word_dict[word] = word_dict[word] + "<hr>" + definition
        else:
            word_dict[word] = definition
        word_is_lemma[word] = True
        lemma_words.add(word)

    # Add inflection entries (skip if lemma entry already exists)
    for word, definition in word_list[len(entries):]:  # Remaining items are inflection entries
        if word not in word_dict:
            word_dict[word] = definition
            word_is_lemma[word] = False
        # If lemma entry exists, keep it (don't overwrite with inflection)

    # Sort by StarDict convention (case-insensitive, then case-sensitive)
    print(f"Sorting {len(word_dict)} entries...")
    sorted_words = sorted(word_dict.keys(), key=lambda w: (w.lower(), w))

    # Write .dict file (definitions)
    print("Writing .dict file...")
    dict_path = output_dir / f"{dict_name}.dict"
    idx_data = []  # (word, offset, size)
    offset = 0

    with open(dict_path, "wb") as dict_file:
        for word in sorted_words:
            definition = word_dict[word]
            def_bytes = definition.encode("utf-8")
            dict_file.write(def_bytes)
            dict_file.write(b"\x00")  # null terminator
            size = len(def_bytes) + 1
            idx_data.append((word, offset, size))
            offset += size

    # Write .idx file (index)
    print("Writing .idx file...")
    idx_path = output_dir / f"{dict_name}.idx"
    with open(idx_path, "wb") as idx_file:
        for word, off, size in idx_data:
            idx_file.write(word.encode("utf-8"))
            idx_file.write(b"\x00")
            idx_file.write(struct.pack(">II", off, size))

    idx_size = os.path.getsize(idx_path)

    # Write .ifo file (metadata)
    print("Writing .ifo file...")
    ifo_path = output_dir / f"{dict_name}.ifo"
    with open(ifo_path, "w", encoding="utf-8") as ifo_file:
        ifo_file.write("StarDict's dict ifo file\n")
        ifo_file.write(f"version=2.4.2\n")
        ifo_file.write(f"wordcount={len(idx_data)}\n")
        ifo_file.write(f"idxfilesize={idx_size}\n")
        ifo_file.write(f"bookname={dict_name}\n")
        ifo_file.write(f"sametypesequence=h\n")
        ifo_file.write(f"description=BetterOfflineDict Czech-English. {len(entries)} entries, {len(inflections)} inflections.\n")

    # Compress with dictzip if available
    print("Compressing .dict file...")
    try:
        import subprocess
        subprocess.run(["dictzip", str(dict_path)], check=True)
        print("  Compressed with dictzip")
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("  dictzip not found, leaving uncompressed")
        print("  Install with: sudo apt install dictzip")

    # Summary
    dict_size = os.path.getsize(dict_path) if dict_path.exists() else 0
    dz_path = output_dir / f"{dict_name}.dict.dz"
    dz_size = os.path.getsize(dz_path) if dz_path.exists() else 0

    print(f"\n{'='*60}")
    print(f"StarDict export complete!")
    print(f"{'='*60}")
    print(f"  Output directory: {output_dir}")
    print(f"  Total headwords: {len(idx_data):,}")
    print(f"  Lemma entries: {len(entries):,}")
    print(f"  Inflected form entries: {len(idx_data) - len(entries):,}")
    print(f"  .ifo: {os.path.getsize(ifo_path):,} bytes")
    print(f"  .idx: {idx_size:,} bytes")
    if dz_size:
        print(f"  .dict.dz: {dz_size:,} bytes ({dz_size/1024/1024:.1f} MB)")
    else:
        print(f"  .dict: {dict_size:,} bytes ({dict_size/1024/1024:.1f} MB)")

    # Test a few lookups
    print(f"\nSample lookups:")
    test_words = ["město", "městě", "dělat", "dělali", "mladý", "mladých", "kniha", "knih",
                   "absurdity", "reality", "zas", "her", "let"]
    for tw in test_words:
        if tw in word_dict:
            defn = word_dict[tw]
            # Truncate for display
            defn_short = defn[:120].replace("<br>", " | ").replace("<hr>", " || ")
            defn_short = defn_short.replace("<b>", "").replace("</b>", "")
            defn_short = defn_short.replace("<i>", "").replace("</i>", "")
            defn_short = defn_short.replace("<small>", "").replace("</small>", "")
            print(f"  {tw}: {defn_short}...")
        else:
            print(f"  {tw}: NOT FOUND")

    conn.close()
    return len(idx_data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export to StarDict format")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--dict-name", default=DEFAULT_DICT_NAME)
    args = parser.parse_args()
    export_stardict(output_dir=args.output_dir, dict_name=args.dict_name)

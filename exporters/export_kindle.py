#!/usr/bin/env python3
"""
Export dictionary database to Kindle MOBI format.

Generates XHTML files with Kindle dictionary markup (<idx:entry>, <idx:orth>,
<idx:infl>) plus an OPF manifest, then compiles with kindlegen.

Unlike StarDict (one entry per inflected form), Kindle supports listing all
inflected forms inside a single entry via <idx:iform> tags, keeping the
output compact.

Usage:
    python3 export_kindle.py [--output-dir DIR] [--dict-name NAME]

Requires:
    kindlegen - Amazon's Kindle dictionary compiler
    Download from: https://archive.org/details/kindlegen-2.9
"""

import sqlite3
import json
import os
import re
import html
import argparse
import subprocess
from pathlib import Path
from collections import defaultdict

from export_stardict import (
    classify_entry_senses,
    resolve_crossref_entry,
    format_entry_html,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DB_PATH = PROJECT_ROOT / "dictionary.db"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "kindle"
DEFAULT_DICT_NAME = "Czech-English"

# Kindle's kindlegen works best with ~10K entries per XHTML file
ENTRIES_PER_FILE = 10_000


def format_definition_html(entry_json, lemma, pos):
    """Format a definition for Kindle display (reuses StarDict HTML formatter)."""
    return format_entry_html(entry_json, lemma, pos)


def write_xhtml_file(filepath, entries_chunk, chunk_index):
    """Write one XHTML content file with dictionary entries.

    Each entry gets <idx:entry> markup with <idx:orth> for the headword
    and <idx:infl>/<idx:iform> for inflected forms.
    """
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns:idx="http://www.amazon.com/kindlegen/2011/xhtml"
      xmlns:mbp="http://www.amazon.com/kindlegen/2011/mbp"
      xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
  <style>
    .entry { margin-bottom: 1em; }
    .headword { font-weight: bold; font-size: 1.1em; }
    .pos { font-style: italic; color: #555; }
    .gender { font-size: 0.9em; color: #777; }
    .definition { margin-top: 0.2em; }
    .example { font-size: 0.9em; font-style: italic; color: #333; }
  </style>
</head>
<body>
  <mbp:frameset>
""")

        for lemma, pos, definition_html, inflected_forms in entries_chunk:
            # Escape for XML attribute context
            lemma_attr = html.escape(lemma, quote=True)

            f.write(f'    <idx:entry name="default" scriptable="yes" spell="yes">\n')
            f.write(f'      <idx:orth value="{lemma_attr}">{html.escape(lemma)}\n')

            # Add inflected forms
            if inflected_forms:
                f.write(f'        <idx:infl>\n')
                for form in inflected_forms:
                    form_attr = html.escape(form, quote=True)
                    f.write(f'          <idx:iform value="{form_attr}" exact="yes"/>\n')
                f.write(f'        </idx:infl>\n')

            f.write(f'      </idx:orth>\n')
            f.write(f'      <div class="definition">{definition_html}</div>\n')
            f.write(f'    </idx:entry>\n')
            f.write(f'    <hr/>\n')

        f.write("""  </mbp:frameset>
</body>
</html>
""")


def write_opf(output_dir, dict_name, content_files, num_entries):
    """Write the OPF manifest for kindlegen."""
    opf_path = output_dir / f"{dict_name}.opf"
    with open(opf_path, "w", encoding="utf-8") as f:
        f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{html.escape(dict_name)} Dictionary</dc:title>
    <dc:language>cs</dc:language>
    <dc:creator>BetterOfflineDict</dc:creator>
    <dc:description>Czech-English dictionary with {num_entries:,} entries and full morphological coverage.</dc:description>
    <x-metadata>
      <DictionaryInLanguage>cs</DictionaryInLanguage>
      <DictionaryOutLanguage>en</DictionaryOutLanguage>
      <DefaultLookupIndex>default</DefaultLookupIndex>
    </x-metadata>
  </metadata>
  <manifest>
    <item id="cover" href="cover.html" media-type="application/xhtml+xml"/>
""")
        for i, cf in enumerate(content_files):
            f.write(f'    <item id="content{i}" href="{cf.name}" media-type="application/xhtml+xml"/>\n')
        f.write("""  </manifest>
  <spine>
    <itemref idref="cover"/>
""")
        for i in range(len(content_files)):
            f.write(f'    <itemref idref="content{i}"/>\n')
        f.write("""  </spine>
  <guide>
    <reference type="text" title="Content" href="cover.html"/>
  </guide>
</package>
""")
    return opf_path


def write_cover(output_dir, dict_name, num_entries, num_forms):
    """Write a simple cover page."""
    cover_path = output_dir / "cover.html"
    with open(cover_path, "w", encoding="utf-8") as f:
        f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
  <title>{html.escape(dict_name)} Dictionary</title>
</head>
<body>
  <h1>{html.escape(dict_name)} Dictionary</h1>
  <p>{num_entries:,} entries with {num_forms:,} lookupable inflected forms.</p>
  <p>Built with <b>BetterOfflineDict</b>.</p>
  <p>Data: Wiktionary, Svobodne Slovniky, MorfFlex CZ 2.1, Tatoeba, DeepSeek.</p>
</body>
</html>
""")


def export_kindle(db_path=DB_PATH, output_dir=DEFAULT_OUTPUT_DIR, dict_name=DEFAULT_DICT_NAME):
    """Export database to Kindle dictionary format."""
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

    # Load inflections, grouped by lemma
    print("Loading inflections...")
    c.execute("SELECT form, lemma, pos FROM inflections")
    inflections = c.fetchall()
    print(f"  {len(inflections)} inflection mappings loaded")

    inflection_map = defaultdict(set)  # (lemma, pos) -> set of inflected forms
    for infl in inflections:
        inflection_map[(infl["lemma"], infl["pos"])].add(infl["form"])

    # Build entries_by_lemma for cross-reference resolution
    print("Resolving cross-references...")
    entries_by_lemma = defaultdict(list)
    entry_data_cache = {}

    for entry in entries:
        key = (entry["lemma"], entry["pos"])
        try:
            entry_data = json.loads(entry["entry_json"])
            entries_by_lemma[entry["lemma"].lower()].append(entry_data)
            entry_data_cache[key] = entry_data
        except json.JSONDecodeError:
            pass

    # Resolve cross-references and build final entries
    kindle_entries = []  # (lemma, pos, definition_html, inflected_forms)
    resolved_count = 0

    for entry in entries:
        key = (entry["lemma"], entry["pos"])
        entry_data = entry_data_cache.get(key)
        if not entry_data:
            continue

        real_senses, xref_senses = classify_entry_senses(entry_data)

        if xref_senses and not real_senses:
            # Pure cross-reference: resolve
            resolved_html, _ = resolve_crossref_entry(entry_data, entries_by_lemma)
            if resolved_html:
                definition_html = resolved_html
                resolved_count += 1
            else:
                definition_html = format_definition_html(entry["entry_json"], entry["lemma"], entry["pos"])
        elif xref_senses and real_senses:
            # Mixed: keep only real senses
            filtered = dict(entry_data)
            filtered["senses"] = real_senses
            definition_html = format_definition_html(filtered, entry["lemma"], entry["pos"])
        else:
            definition_html = format_definition_html(entry["entry_json"], entry["lemma"], entry["pos"])

        # Get inflected forms for this entry
        forms = inflection_map.get(key, set())

        kindle_entries.append((entry["lemma"], entry["pos"], definition_html, sorted(forms)))

    print(f"  Resolved {resolved_count} cross-reference entries")
    print(f"  {len(kindle_entries)} entries to export")

    total_iform_count = sum(len(forms) for _, _, _, forms in kindle_entries)
    print(f"  {total_iform_count:,} total inflected form lookups")

    # Write XHTML content files in chunks
    print("Writing XHTML content files...")
    content_files = []

    for chunk_idx in range(0, len(kindle_entries), ENTRIES_PER_FILE):
        chunk = kindle_entries[chunk_idx:chunk_idx + ENTRIES_PER_FILE]
        filename = f"content_{chunk_idx // ENTRIES_PER_FILE:03d}.html"
        filepath = output_dir / filename
        write_xhtml_file(filepath, chunk, chunk_idx // ENTRIES_PER_FILE)
        content_files.append(filepath)

    print(f"  Wrote {len(content_files)} content files")

    # Write cover and OPF
    write_cover(output_dir, dict_name, len(kindle_entries), total_iform_count)
    opf_path = write_opf(output_dir, dict_name, content_files, len(kindle_entries))

    # Try to compile with kindlegen
    print("Compiling with kindlegen...")
    mobi_path = output_dir / f"{dict_name}.mobi"
    try:
        result = subprocess.run(
            ["kindlegen", str(opf_path), "-o", f"{dict_name}.mobi"],
            capture_output=True, text=True, timeout=300
        )
        if mobi_path.exists():
            mobi_size = os.path.getsize(mobi_path)
            print(f"  MOBI created: {mobi_size / 1024 / 1024:.1f} MB")
        else:
            print(f"  kindlegen ran but no MOBI produced")
            if result.stderr:
                print(f"  stderr: {result.stderr[:500]}")
    except FileNotFoundError:
        print("  kindlegen not found. XHTML + OPF files are ready for manual compilation.")
        print("  Download kindlegen from: https://archive.org/details/kindlegen-2.9")
        print(f"  Then run: kindlegen {opf_path} -o {dict_name}.mobi")
    except subprocess.TimeoutExpired:
        print("  kindlegen timed out (large dictionary). Try running manually:")
        print(f"  kindlegen {opf_path} -o {dict_name}.mobi")

    conn.close()

    # Summary
    print(f"\n{'='*60}")
    print(f"Kindle export complete!")
    print(f"{'='*60}")
    print(f"  Output directory: {output_dir}")
    print(f"  Entries: {len(kindle_entries):,}")
    print(f"  Inflected form lookups: {total_iform_count:,}")
    print(f"  Content files: {len(content_files)}")
    if mobi_path.exists():
        print(f"  MOBI file: {mobi_path}")
    else:
        print(f"  OPF manifest: {opf_path}")
        print(f"  Compile with: kindlegen {opf_path}")

    return len(kindle_entries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export to Kindle MOBI format")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--dict-name", default=DEFAULT_DICT_NAME)
    args = parser.parse_args()
    export_kindle(output_dir=args.output_dir, dict_name=args.dict_name)

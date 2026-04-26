#!/usr/bin/env python3
"""
Process all ebook files in books/ directory to expand dictionary coverage:
1. Extract plain text from .epub files using calibre's ebook-convert
2. Find words missing from the dictionary
3. Optionally generate definitions via DeepSeek
4. Optionally re-import MorfFlex inflections for newly added lemmas

Usage:
    python3 process_books.py                                  # Report missing words
    python3 process_books.py --generate                       # Generate definitions via DeepSeek
    python3 process_books.py --generate --reimport-morfflex   # Generate + add inflections
    python3 process_books.py --generate --dry-run             # Show what would be generated
    python3 process_books.py --generate --max-words 50        # Generate first 50
"""

import sqlite3
import json
import sys
import os
import re
import subprocess
import asyncio
import argparse
import tempfile
from collections import Counter
from pathlib import Path

DB_PATH = Path(__file__).parent / "dictionary.db"
BOOKS_DIR = Path(__file__).parent / "books"
DATA_DIR = Path(__file__).parent / "data"

# Import shared logic from process_text
from process_text import (
    tokenize, lemmatize_with_majka, find_missing, classify_missing,
    pos_hint_to_pos, extract_context, generate_definitions,
    validate_entry, save_entries, SYSTEM_PROMPT, FEW_SHOT
)


def extract_text_from_epub(filepath):
    """Extract plain text from epub using calibre's ebook-convert."""
    with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as tmp:
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ['ebook-convert', str(filepath), tmp_path,
             '--txt-output-encoding=utf-8'],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            print(f"  WARNING: ebook-convert failed for {filepath.name}")
            return ""
        with open(tmp_path, 'r', encoding='utf-8') as f:
            text = f.read()
        return text
    except subprocess.TimeoutExpired:
        print(f"  WARNING: ebook-convert timed out for {filepath.name}")
        return ""
    except FileNotFoundError:
        print("ERROR: ebook-convert not found. Install calibre: sudo apt install calibre")
        sys.exit(1)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def is_likely_czech(word, pos_map):
    """Check if a word is likely Czech rather than English/brand/metadata."""
    czech_chars = set("ěščřžýáíéúůďťňó")
    skip_words = {
        "www", "com", "http", "https", "html", "url", "email", "epub",
        "isbn", "pdf", "txt", "jpg", "png", "gif", "xml", "css",
        "copyright", "ebook", "kindle", "calibre", "albatrosmedia",
        "the", "you", "for", "and", "this", "that", "with", "are",
        "not", "but", "was", "have", "has", "will", "can", "your",
        "all", "from", "they", "been", "would", "there", "their",
        "what", "about", "which", "when", "one", "she", "her",
        "his", "how", "out", "its", "than", "into", "some",
        "cooboo", "grada", "fragment", "leda",
    }

    w = word.lower()
    if w in skip_words:
        return False
    if len(w) <= 1:
        return False
    # Contains Czech-specific diacritics -> definitely Czech
    if any(c in czech_chars for c in w):
        return True
    # All ASCII and very short -> might be English/artifact
    if w.isascii() and len(w) <= 3:
        return False
    # Pure ASCII words: check if Majka recognized it
    if w.isascii():
        if pos_map.get(w, '') != '':
            return True
        return False
    return True


def reimport_morfflex_for_lemmas(conn, new_lemmas):
    """Re-import MorfFlex inflections for newly added lemmas."""
    from build_dictionary import import_morfflex

    # Find the MorfFlex file
    morfflex_dir = DATA_DIR / "morfflex"
    morfflex_path = None
    if morfflex_dir.exists():
        for f in sorted(morfflex_dir.iterdir()):
            if f.name.endswith('.tsv.xz') or (f.name.endswith('.tsv') and not f.name.endswith('.tsv.xz')):
                morfflex_path = f
                break

    if not morfflex_path:
        print("WARNING: MorfFlex file not found, skipping inflection re-import")
        print(f"  Expected location: {morfflex_dir}/czech-morfflex-*.tsv.xz")
        return 0

    print(f"\nRe-importing MorfFlex inflections for {len(new_lemmas)} new lemmas...")
    return import_morfflex(conn, morfflex_path, target_lemmas=new_lemmas)


def main():
    parser = argparse.ArgumentParser(description="Process ebook files for dictionary coverage")
    parser.add_argument("--generate", action="store_true", help="Generate definitions via DeepSeek")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be generated")
    parser.add_argument("--max-words", type=int, default=0, help="Limit words to generate (0=all)")
    parser.add_argument("--min-freq", type=int, default=1, help="Min word frequency (default: 1)")
    parser.add_argument("--batch-size", type=int, default=50, help="API batch size (default: 50)")
    parser.add_argument("--reimport-morfflex", action="store_true",
                        help="Re-import MorfFlex inflections for newly added lemmas")
    parser.add_argument("--dir", type=str, default=str(BOOKS_DIR), help="Books directory")
    args = parser.parse_args()

    books_dir = Path(args.dir)
    if not books_dir.exists():
        print(f"ERROR: Directory not found: {books_dir}")
        sys.exit(1)

    # Collect all epub files
    epub_files = sorted(books_dir.glob("*.epub"))
    print(f"Found {len(epub_files)} epub files in {books_dir}")

    if not epub_files:
        print("No .epub files found.")
        sys.exit(1)

    # Extract text from each book
    print("Extracting text from ebooks...")
    all_text_parts = []
    file_count = 0
    for f in epub_files:
        print(f"  Extracting: {f.name}...")
        text = extract_text_from_epub(f)
        if text.strip():
            all_text_parts.append(text)
            file_count += 1

    combined_text = '\n'.join(all_text_parts)
    print(f"\nExtracted text from {file_count} books")
    print(f"Combined corpus: {len(combined_text):,} characters")

    # Save combined corpus for reference
    corpus_path = Path(__file__).parent / "books_corpus.txt"
    with open(corpus_path, 'w', encoding='utf-8') as f_out:
        f_out.write(combined_text)
    print(f"Saved combined corpus to: {corpus_path}")

    # Tokenize
    tokens = tokenize(combined_text)
    word_freq = Counter(t.lower() for t in tokens if len(t) > 1)
    print(f"Tokens: {len(tokens):,}, Unique forms: {len(word_freq):,}")

    # Lemmatize
    print("Lemmatizing with Majka...")
    unique_words = list(word_freq.keys())
    lemma_map, pos_map = lemmatize_with_majka(unique_words)

    # Find missing
    conn = sqlite3.connect(DB_PATH)
    print("Checking dictionary coverage...")
    missing = find_missing(conn, word_freq, lemma_map, pos_map)

    # Classify proper nouns vs vocabulary
    vocabulary, proper_nouns = classify_missing(missing, tokens)

    # Filter out non-Czech words
    vocabulary = [v for v in vocabulary if is_likely_czech(v["word"], pos_map)]

    # Filter by frequency
    vocab_above_freq = [v for v in vocabulary if v["freq"] >= args.min_freq]

    # Deduplicate by lemma: group inflected forms, keep only unique lemmas
    # This avoids sending "vzhlédl" and "vzhlédla" separately when both map to "vzhlédnout"
    lemma_groups = {}  # lemma -> best item (highest combined freq, use lemma form)
    for item in vocab_above_freq:
        lemma = item["lemma"]
        if lemma in lemma_groups:
            lemma_groups[lemma]["freq"] += item["freq"]
        else:
            lemma_groups[lemma] = {
                "word": lemma,  # send the lemma form to the LLM
                "lemma": lemma,
                "freq": item["freq"],
                "pos_hint": item.get("pos_hint", ""),
                "type": item.get("type", "vocabulary"),
            }

    unique_lemmas = sorted(lemma_groups.values(), key=lambda x: -x["freq"])

    print(f"\n{'='*60}")
    print(f"BOOK CORPUS - MISSING WORD ANALYSIS")
    print(f"{'='*60}")
    print(f"  Books processed: {file_count}")
    print(f"  Total tokens: {len(tokens):,}")
    print(f"  Unique word forms: {len(word_freq):,}")
    print(f"  Total missing: {len(missing):,}")
    print(f"  Proper nouns (excluded): {len(proper_nouns):,}")
    print(f"  Czech vocabulary words: {len(vocabulary):,}")
    print(f"  Vocabulary (freq >= {args.min_freq}): {len(vocab_above_freq):,}")
    print(f"  Unique lemmas to generate: {len(unique_lemmas):,}")

    # Coverage stats
    total_forms = len(word_freq)
    covered_forms = total_forms - len(missing)
    coverage_pct = (covered_forms / total_forms * 100) if total_forms > 0 else 0
    print(f"\n  Dictionary coverage: {covered_forms:,}/{total_forms:,} forms ({coverage_pct:.1f}%)")

    # Token-level coverage
    total_tokens = sum(word_freq.values())
    missing_token_count = sum(item["freq"] for item in missing)
    token_coverage = ((total_tokens - missing_token_count) / total_tokens * 100) if total_tokens > 0 else 0
    print(f"  Token coverage: {total_tokens - missing_token_count:,}/{total_tokens:,} ({token_coverage:.1f}%)")

    if unique_lemmas:
        print(f"\nTop 50 missing lemmas:")
        for i, item in enumerate(unique_lemmas[:50], 1):
            pos = pos_hint_to_pos(item.get('pos_hint', ''))
            pos_str = f" [{pos}]" if pos else ""
            print(f"  {i:3}. {item['word']}{pos_str} (combined freq: {item['freq']})")

    # Cost estimate
    if unique_lemmas:
        est_cost = len(unique_lemmas) * 0.0002
        print(f"\nEstimated DeepSeek cost for {len(unique_lemmas)} unique lemmas: ${est_cost:.2f}")

    # Generate if requested
    new_lemmas = set()
    if args.generate or args.dry_run:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            key_file = Path(__file__).parent / "deepseek_key.txt"
            if key_file.exists():
                api_key = key_file.read_text().strip()

        if not api_key and not args.dry_run:
            print("\nERROR: Set DEEPSEEK_API_KEY environment variable or put key in deepseek_key.txt")
            conn.close()
            sys.exit(1)

        words_to_generate = unique_lemmas
        if args.max_words > 0:
            words_to_generate = words_to_generate[:args.max_words]

        print(f"\n{'='*60}")
        print(f"GENERATING DEFINITIONS ({'DRY RUN' if args.dry_run else 'LIVE'})")
        print(f"{'='*60}")
        print(f"  Unique lemmas to generate: {len(words_to_generate)}")

        results = asyncio.run(
            generate_definitions(words_to_generate, combined_text, api_key,
                                 batch_size=args.batch_size, dry_run=args.dry_run)
        )

        if args.dry_run:
            print(f"\n  Would generate {len(results)} definitions")
            for word, entry in list(results.items())[:5]:
                print(f"\n  --- {word} ---")
                print(f"  Prompt: {entry.get('prompt', '?')[:200]}")
        else:
            valid_count = sum(1 for e in results.values() if "_error" not in e)
            error_count = sum(1 for e in results.values() if "_error" in e)
            print(f"\n  Generated: {valid_count}, Errors: {error_count}")

            if valid_count > 0:
                saved, save_errors = save_entries(conn, results)
                print(f"  Saved to database: {saved}")
                if save_errors:
                    print(f"  Save errors: {save_errors}")

                # Track newly added lemmas for MorfFlex re-import
                for word, entry in results.items():
                    if "_error" not in entry and "_dry_run" not in entry:
                        lemma = entry.get("lemma", word).lower()
                        new_lemmas.add(lemma)

            # Show samples
            print(f"\nSample generated entries:")
            for word, entry in list(results.items())[:10]:
                if "_error" in entry:
                    print(f"  {word}: ERROR - {entry['_error']}")
                else:
                    senses = entry.get("senses", [])
                    first_def = senses[0].get("definition_en", "?") if senses else "?"
                    print(f"  {word} [{entry.get('pos', '?')}]: {first_def}")

    # Re-import MorfFlex inflections for new lemmas
    if args.reimport_morfflex and new_lemmas:
        reimport_morfflex_for_lemmas(conn, new_lemmas)
    elif args.reimport_morfflex and not new_lemmas:
        print("\nNo new lemmas to re-import MorfFlex for.")

    # Record processing
    c = conn.cursor()
    c.execute(
        "INSERT INTO processed_texts (filename, total_lemmas, missing_lemmas, new_entries_added) VALUES (?, ?, ?, ?)",
        (f"books/ ({file_count} epub files)", len(word_freq), len(vocabulary), len(new_lemmas))
    )
    conn.commit()
    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()

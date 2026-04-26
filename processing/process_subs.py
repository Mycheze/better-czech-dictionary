#!/usr/bin/env python3
"""
Process all subtitle files in 1k_sub_files/ directory:
1. Extract clean Czech text (strip WEBVTT headers)
2. Find words missing from the dictionary
3. Optionally generate definitions via DeepSeek

Usage:
    python3 process_subs.py                    # Report missing words
    python3 process_subs.py --generate         # Generate definitions via DeepSeek
    python3 process_subs.py --generate --dry-run  # Show what would be generated
"""

import sqlite3
import json
import sys
import os
import re
import subprocess
import asyncio
import argparse
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DB_PATH = PROJECT_ROOT / "dictionary.db"
MAJKA_PATH = PROJECT_ROOT / "majka"
MAJKA_DICT = PROJECT_ROOT / "majka.w-lt"
SUBS_DIR = PROJECT_ROOT / "1k_sub_files"

# Import shared logic from process_text
from process_text import (
    tokenize, lemmatize_with_majka, find_missing, classify_missing,
    pos_hint_to_pos, extract_context, generate_definitions,
    validate_entry, save_entries, SYSTEM_PROMPT, FEW_SHOT
)


def extract_text_from_sub(filepath):
    """Extract clean Czech text from a subtitle file, stripping WEBVTT headers."""
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()

    lines = text.split('\n')
    clean_lines = []
    skip_headers = True

    for line in lines:
        stripped = line.strip()

        # Skip WEBVTT header block
        if skip_headers:
            if stripped.startswith('WEBVTT') or stripped.startswith('\ufeffWEBVTT'):
                continue
            if stripped.startswith('Kind:') or stripped.startswith('Language:'):
                continue
            if stripped.startswith('NOTE '):
                continue
            if stripped == '':
                continue
            skip_headers = False

        # Skip timestamp lines (00:00:00.000 --> 00:00:00.000)
        if re.match(r'\d{2}:\d{2}', stripped):
            continue
        if '-->' in stripped:
            continue

        # Skip stage directions in ALL CAPS with colon (e.g. "VNITŘNÍ HLAS PETRA:")
        if stripped.endswith(':') and stripped == stripped.upper() and len(stripped) > 3:
            continue

        if stripped:
            clean_lines.append(stripped)

    return '\n'.join(clean_lines)


def main():
    parser = argparse.ArgumentParser(description="Process all subtitle files for dictionary coverage")
    parser.add_argument("--generate", action="store_true", help="Generate definitions via DeepSeek")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be generated")
    parser.add_argument("--max-words", type=int, default=0, help="Limit words to generate (0=all)")
    parser.add_argument("--min-freq", type=int, default=2, help="Min word frequency (default: 2)")
    parser.add_argument("--batch-size", type=int, default=10, help="API batch size (default: 10)")
    parser.add_argument("--dir", type=str, default=str(SUBS_DIR), help="Subtitle directory")
    args = parser.parse_args()

    subs_dir = Path(args.dir)
    if not subs_dir.exists():
        print(f"ERROR: Directory not found: {subs_dir}")
        sys.exit(1)

    # Collect all subtitle files
    sub_files = sorted(subs_dir.glob("*.txt"))
    print(f"Found {len(sub_files)} subtitle files in {subs_dir}")

    # Extract and combine text
    print("Extracting clean text from subtitle files...")
    all_text_parts = []
    file_count = 0
    for f in sub_files:
        text = extract_text_from_sub(f)
        if text.strip():
            all_text_parts.append(text)
            file_count += 1

    combined_text = '\n'.join(all_text_parts)
    print(f"Extracted text from {file_count} files")
    print(f"Combined corpus: {len(combined_text):,} characters")

    # Save combined corpus for reference
    corpus_path = PROJECT_ROOT / "subs_corpus.txt"
    with open(corpus_path, 'w', encoding='utf-8') as f:
        f.write(combined_text)
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

    # Classify
    vocabulary, proper_nouns = classify_missing(missing, tokens)

    # Filter out English/brand words and non-Czech vocabulary
    czech_chars = set("ěščřžýáíéúůďťňó")
    english_brand_words = {
        "slowczech", "youtube", "patreon", "instagram", "instagramu",
        "facebook", "facebooku", "spotify", "italki", "tiktok",
        "podcast", "podcastu", "podcasty", "podcastem", "podcastech",
        "www", "com", "http", "https", "html", "url", "email",
        "the", "you", "for", "and", "this", "that", "with", "are",
        "not", "but", "was", "have", "has", "will", "can", "your",
        "all", "from", "they", "been", "would", "there", "their",
        "what", "about", "which", "when", "one", "she", "her",
        "his", "how", "out", "its", "than", "into", "some",
        "could", "them", "only", "come", "made", "after", "did",
        "should", "more", "these", "other", "may", "just", "also",
        "mail", "online", "video", "audio", "grammar", "challenge",
        "level", "link", "click", "subscribe", "like", "comment",
        "channel", "check", "free", "learn", "lesson",
        "gt", "lt", "amp",  # HTML entities
    }

    def is_likely_czech(word):
        """Check if a word is likely Czech rather than English/brand."""
        w = word.lower()
        if w in english_brand_words:
            return False
        # Contains Czech-specific diacritics -> definitely Czech
        if any(c in czech_chars for c in w):
            return True
        # All ASCII and short -> might be English
        if len(w) <= 3 and w.isascii():
            return False
        # Pure ASCII words that don't look Czech
        if w.isascii() and len(w) > 2:
            # If Majka recognized it, it's Czech
            if pos_map.get(w, '') != '':
                return True
            # Otherwise probably English
            return False
        return True

    vocabulary = [v for v in vocabulary if is_likely_czech(v["word"])]

    # Filter by frequency
    vocab_above_freq = [v for v in vocabulary if v["freq"] >= args.min_freq]

    print(f"\n{'='*60}")
    print(f"SUBTITLE CORPUS - MISSING WORD ANALYSIS")
    print(f"{'='*60}")
    print(f"  Files processed: {file_count}")
    print(f"  Total tokens: {len(tokens):,}")
    print(f"  Unique word forms: {len(word_freq):,}")
    print(f"  Total missing: {len(missing):,}")
    print(f"  Proper nouns: {len(proper_nouns):,}")
    print(f"  Vocabulary words: {len(vocabulary):,}")
    print(f"  Vocabulary (freq >= {args.min_freq}): {len(vocab_above_freq):,}")

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

    if vocab_above_freq:
        print(f"\nTop 30 missing vocabulary words:")
        for i, item in enumerate(vocab_above_freq[:30], 1):
            lemma_str = f" (-> {item['lemma']})" if item['lemma'] != item['word'] else ""
            pos = pos_hint_to_pos(item.get('pos_hint', ''))
            pos_str = f" [{pos}]" if pos else ""
            print(f"  {i:3}. {item['word']}{lemma_str}{pos_str} (freq: {item['freq']})")

    # Cost estimate
    if vocab_above_freq:
        est_cost = len(vocab_above_freq) * 0.0002
        print(f"\nEstimated DeepSeek cost for {len(vocab_above_freq)} words: ${est_cost:.2f}")

    # Generate if requested
    if args.generate or args.dry_run:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            key_file = PROJECT_ROOT / "deepseek_key.txt"
            if key_file.exists():
                api_key = key_file.read_text().strip()

        if not api_key and not args.dry_run:
            print("\nERROR: Set DEEPSEEK_API_KEY environment variable or put key in deepseek_key.txt")
            conn.close()
            sys.exit(1)

        words_to_generate = vocab_above_freq
        if args.max_words > 0:
            words_to_generate = words_to_generate[:args.max_words]

        print(f"\n{'='*60}")
        print(f"GENERATING DEFINITIONS ({'DRY RUN' if args.dry_run else 'LIVE'})")
        print(f"{'='*60}")
        print(f"  Words to generate: {len(words_to_generate)}")

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

            # Show samples
            print(f"\nSample generated entries:")
            for word, entry in list(results.items())[:5]:
                if "_error" in entry:
                    print(f"  {word}: ERROR - {entry['_error']}")
                else:
                    senses = entry.get("senses", [])
                    first_def = senses[0].get("definition_en", "?") if senses else "?"
                    print(f"  {word} [{entry.get('pos', '?')}]: {first_def}")

    # Record processing
    c = conn.cursor()
    c.execute(
        "INSERT INTO processed_texts (filename, total_lemmas, missing_lemmas, new_entries_added) VALUES (?, ?, ?, ?)",
        ("1k_sub_files/ (all subs)", len(word_freq), len(vocabulary), 0)
    )
    conn.commit()
    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()

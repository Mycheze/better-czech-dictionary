#!/usr/bin/env python3
"""
Test the dictionary by looking up words and testing coverage against a Czech text.

Usage:
    python3 test_dictionary.py lookup <word> [<word2> ...]
    python3 test_dictionary.py coverage <text_file>
"""

import sqlite3
import sys
import re
import subprocess
import json
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DB_PATH = PROJECT_ROOT / "dictionary.db"


def lookup_word(conn, word):
    """Look up a word in the dictionary (checks both lemmas and inflections)."""
    word_lower = word.lower()
    c = conn.cursor()

    # Check as lemma
    c.execute("SELECT lemma, pos, entry_json, source FROM entries WHERE lemma = ?", (word_lower,))
    lemma_results = c.fetchall()

    # Check as inflected form
    c.execute("""SELECT i.form, i.lemma, i.pos, i.tag, e.entry_json
                 FROM inflections i
                 JOIN entries e ON e.lemma = i.lemma AND e.pos = i.pos
                 WHERE i.form = ?""", (word_lower,))
    infl_results = c.fetchall()

    return lemma_results, infl_results


def print_lookup(word, lemma_results, infl_results):
    """Print lookup results for a word."""
    print(f"\n{'='*60}")
    print(f"  {word}")
    print(f"{'='*60}")

    if lemma_results:
        for lemma, pos, entry_json, source in lemma_results:
            entry = json.loads(entry_json)
            senses = entry.get("senses", [])
            aspect = entry.get("aspect", "")
            gender = entry.get("gender", "")

            extra = []
            if gender:
                extra.append(gender)
            if aspect:
                extra.append(aspect)
                pair = entry.get("aspect_pair", "")
                if pair:
                    extra.append(f"→ {pair}")

            extra_str = f" ({', '.join(extra)})" if extra else ""
            print(f"  [{pos}]{extra_str} [{source}]")

            for i, s in enumerate(senses, 1):
                defn = s.get("definition_en", "")
                register = s.get("register", "")
                reg = f" [{register}]" if register and register != "neutral" else ""
                print(f"    {i}. {defn}{reg}")

    if infl_results:
        # Group by lemma
        by_lemma = {}
        for form, lemma, pos, tag, entry_json in infl_results:
            key = (lemma, pos)
            if key not in by_lemma:
                entry = json.loads(entry_json)
                first_def = entry.get("senses", [{}])[0].get("definition_en", "?")
                by_lemma[key] = (tag, first_def)

        if not lemma_results:
            print(f"  (inflected form)")
        for (lemma, pos), (tag, first_def) in by_lemma.items():
            print(f"  → {lemma} [{pos}]: {first_def}")

    if not lemma_results and not infl_results:
        print(f"  NOT FOUND")


def tokenize_text(text):
    """Simple tokenization."""
    tokens = []
    current_word = []
    punctuation = '.,!?;:()[]{}""\'«»—–-„""‚''…0123456789'

    for char in text:
        if char.isspace():
            if current_word:
                tokens.append(''.join(current_word))
                current_word = []
        elif char in punctuation:
            if current_word:
                tokens.append(''.join(current_word))
                current_word = []
        else:
            current_word.append(char)

    if current_word:
        tokens.append(''.join(current_word))

    return tokens


def _thin_lookups(conn, resolved_lemma):
    """Which resolved lookups land on an entry tools/audit_entries.py calls thin."""
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        sys.path.insert(0, str(PROJECT_ROOT / "exporters"))
        from audit_entries import classify
        from export_stardict import is_crossref_sense
    except ImportError as exc:
        print(f"  (quality check unavailable: {exc})")
        return None, None

    c = conn.cursor()
    by_lemma = defaultdict(list)
    c.execute("SELECT lemma, pos, source, entry_json FROM entries")
    for lemma, pos, source, entry_json in c:
        try:
            by_lemma[lemma].append((pos, source, json.loads(entry_json)))
        except json.JSONDecodeError:
            continue

    real_sense_lemmas = {
        lemma for lemma, variants in by_lemma.items()
        if any(not is_crossref_sense(sense.get("definition_en") or "")
               for _p, _s, data in variants
               for sense in data.get("senses", []))
    }

    c.execute("SELECT DISTINCT form FROM inflections WHERE form <> lemma")
    inflected_forms = {row[0] for row in c}

    verdict = {}
    thin_forms, reasons = set(), Counter()
    for form, lemma in resolved_lemma.items():
        if lemma not in verdict:
            variants = by_lemma.get(lemma)
            verdict[lemma] = classify(
                variants, real_sense_lemmas.__contains__,
                lemma in inflected_forms) if variants else "no_entry"
        reason = verdict[lemma]
        if reason:
            thin_forms.add(form)
            reasons[reason] += 1
    return thin_forms, reasons


def test_coverage(conn, text_file):
    """Test dictionary coverage against a Czech text."""
    print(f"Testing coverage against: {text_file}")

    with open(text_file, 'r', encoding='utf-8') as f:
        text = f.read()

    tokens = tokenize_text(text)
    print(f"Total tokens: {len(tokens):,}")

    # Count unique word forms
    word_freq = Counter(t.lower() for t in tokens if len(t) > 0)
    unique_forms = len(word_freq)
    print(f"Unique word forms: {unique_forms:,}")

    c = conn.cursor()

    # Check coverage. This is an EXISTENCE test only -- it says nothing about
    # whether the entry a word resolves to is any good, which is why it can read
    # 99.9% while real lookups return "diminutive of houba" or "a male surname".
    # The quality figure below is the number worth watching.
    found_as_lemma = set()
    found_as_inflection = set()
    not_found = set()
    resolved_lemma = {}   # word form -> the lemma its lookup lands on

    for word in word_freq:
        c.execute("SELECT 1 FROM entries WHERE lemma = ?", (word,))
        if c.fetchone():
            found_as_lemma.add(word)
            resolved_lemma[word] = word
            continue

        c.execute("SELECT lemma FROM inflections WHERE form = ? LIMIT 1", (word,))
        row = c.fetchone()
        if row:
            found_as_inflection.add(word)
            resolved_lemma[word] = row[0]
            continue

        not_found.add(word)

    total_found = len(found_as_lemma) + len(found_as_inflection)
    pct = total_found / unique_forms * 100 if unique_forms > 0 else 0

    # Token-level coverage
    tokens_covered = sum(word_freq[w] for w in found_as_lemma | found_as_inflection)
    token_pct = tokens_covered / len(tokens) * 100 if tokens else 0

    print(f"\n{'='*60}")
    print(f"COVERAGE RESULTS")
    print(f"{'='*60}")
    print(f"  Word form coverage: {total_found:,} / {unique_forms:,} ({pct:.1f}%)")
    print(f"    As lemma: {len(found_as_lemma):,}")
    print(f"    As inflection: {len(found_as_inflection):,}")
    print(f"    Not found: {len(not_found):,}")
    print(f"  Token coverage: {tokens_covered:,} / {len(tokens):,} ({token_pct:.1f}%)")

    thin_forms, thin_reasons = _thin_lookups(conn, resolved_lemma)
    if thin_forms is not None:
        good_forms = total_found - len(thin_forms)
        thin_tokens = sum(word_freq[w] for w in thin_forms)
        good_tokens = tokens_covered - thin_tokens
        print(f"\n  Quality (does the entry actually say something useful?)")
        print(f"    Word forms landing on a usable entry: "
              f"{good_forms:,} / {unique_forms:,} "
              f"({good_forms / unique_forms * 100 if unique_forms else 0:.1f}%)")
        print(f"    Tokens landing on a usable entry:     "
              f"{good_tokens:,} / {len(tokens):,} "
              f"({good_tokens / len(tokens) * 100 if tokens else 0:.1f}%)")
        for reason, count in thin_reasons.most_common():
            print(f"      {reason:<24} {count:,} forms")
        worst = sorted(thin_forms, key=lambda w: -word_freq[w])[:15]
        if worst:
            print(f"    Most-read thin lookups: "
                  + ", ".join(f"{w} ({word_freq[w]})" for w in worst))

    # Show most frequent missing words
    missing_by_freq = sorted(not_found, key=lambda w: -word_freq[w])
    print(f"\nTop 50 missing words (by frequency in text):")
    print(f"{'Rank':<6} {'Word':<25} {'Freq':<8}")
    print("-" * 45)
    for i, word in enumerate(missing_by_freq[:50], 1):
        print(f"{i:<6} {word:<25} {word_freq[word]:<8}")

    # Also try with Majka lemmatization
    majka_path = PROJECT_ROOT / "majka"
    majka_dict = PROJECT_ROOT / "majka.w-lt"
    if majka_path.exists() and majka_dict.exists():
        print(f"\n{'='*60}")
        print(f"COVERAGE WITH MAJKA LEMMATIZATION")
        print(f"{'='*60}")

        # Run Majka on missing words
        missing_list = list(not_found)
        input_text = '\n'.join(missing_list)
        try:
            result = subprocess.run(
                [str(majka_path), "-f", str(majka_dict)],
                input=input_text, capture_output=True, text=True, check=True
            )
            majka_lines = result.stdout.strip().split('\n')

            recovered = 0
            still_missing = []
            for word, majka_line in zip(missing_list, majka_lines):
                if ':' in majka_line:
                    lemma = majka_line.split(':')[0].lower()
                    c.execute("SELECT 1 FROM entries WHERE lemma = ?", (lemma,))
                    if c.fetchone():
                        recovered += 1
                        continue
                still_missing.append(word)

            new_total = total_found + recovered
            new_pct = new_total / unique_forms * 100

            print(f"  Additional words found via Majka: {recovered:,}")
            print(f"  Total coverage with Majka: {new_total:,} / {unique_forms:,} ({new_pct:.1f}%)")
            print(f"  Still missing: {len(still_missing):,}")

            still_by_freq = sorted(still_missing, key=lambda w: -word_freq[w])
            print(f"\nTop 30 still-missing words after Majka:")
            print(f"{'Rank':<6} {'Word':<25} {'Freq':<8}")
            print("-" * 45)
            for i, word in enumerate(still_by_freq[:30], 1):
                print(f"{i:<6} {word:<25} {word_freq[word]:<8}")

        except Exception as e:
            print(f"  Majka error: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 test_dictionary.py lookup <word> [<word2> ...]")
        print("  python3 test_dictionary.py coverage <text_file>")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    command = sys.argv[1]

    if command == "lookup":
        for word in sys.argv[2:]:
            lemma_r, infl_r = lookup_word(conn, word)
            print_lookup(word, lemma_r, infl_r)

    elif command == "coverage":
        if len(sys.argv) < 3:
            print("Usage: python3 test_dictionary.py coverage <text_file>")
            sys.exit(1)
        test_coverage(conn, sys.argv[2])

    conn.close()


if __name__ == "__main__":
    main()

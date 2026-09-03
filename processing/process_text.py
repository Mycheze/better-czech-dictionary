#!/usr/bin/env python3
"""
Process a Czech text to find missing dictionary entries and optionally
generate definitions using DeepSeek API.

Usage:
    python3 process_text.py <text_file>                    # Report missing words
    python3 process_text.py <text_file> --generate         # Generate definitions via DeepSeek
    python3 process_text.py <text_file> --generate --dry-run  # Show what would be generated

Requires DEEPSEEK_API_KEY environment variable for --generate mode.
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

SYSTEM_PROMPT = """You are a Czech-English lexicographer creating dictionary entries.
For each Czech word, produce a JSON entry with these fields:

{
  "lemma": "the dictionary form",
  "pos": "noun|verb|adjective|adverb|preposition|conjunction|pronoun|numeral|particle|interjection",
  "gender": "m_anim|m_inanim|f|n" (nouns only, omit for other POS),
  "aspect": "imperfective|perfective|biaspectual" (verbs only, omit for other POS),
  "aspect_pair": "the other aspect form if known" (verbs only, omit if unknown),
  "senses": [
    {
      "definition_en": "English definition/translation",
      "register": "neutral|formal|informal|colloquial|vulgar|archaic|literary|technical" (omit if neutral),
      "examples": [
        {"cs": "Czech example sentence", "en": "English translation"}
      ]
    }
  ],
  "frequency": "common|moderate|uncommon|rare" (omit if unknown),
  "notes": "usage notes, false friends, learner pitfalls" (omit if none)
}

Rules:
1. Provide at least one natural example sentence per sense.
2. For verbs, ALWAYS identify aspect and provide the aspect pair if you know it.
3. List senses from most common to least common.
4. Mark register accurately - most words are neutral, omit the field if neutral.
5. Flag false friends with English if applicable.
6. If unsure about any optional field, omit it rather than guessing.
7. Output ONLY valid JSON, no markdown or commentary.
8. Enumerate EVERY common sense, not just the most frequent one. A reader meets
   the word in one specific sentence, so a single-sense entry sends them away
   with a confidently wrong answer: "houbicka" shipped as "little mushroom" when
   the text meant sponge, "dil" as "part" when it meant episode, "par" as "pair"
   when it meant a few. Concrete/abstract splits, domain uses and colloquial
   uses each get their own sense.
9. Never return a definition that is only a list of bare English synonyms.
   "drag, haul, lug" is not an entry - each sense needs enough gloss to tell it
   apart from the others (domain, typical object, or a parenthetical).
10. Reflexives: for a verb, say in "notes" whether the se/si form differs in
   meaning from the bare verb ("ucit" = to teach but "ucit se" = to learn,
   "vratit" = to give back but "vratit se" = to come back).
11. Do not write surname or given-name glosses for ordinary words."""

FEW_SHOT = [
    {
        "role": "user",
        "content": 'Word: vařit\nPOS hint: verb'
    },
    {
        "role": "assistant",
        "content": json.dumps({
            "lemma": "vařit",
            "pos": "verb",
            "aspect": "imperfective",
            "aspect_pair": "uvařit",
            "senses": [
                {
                    "definition_en": "to cook, to boil",
                    "examples": [
                        {"cs": "Maminka vaří oběd.", "en": "Mom is cooking lunch."},
                        {"cs": "Voda už vaří.", "en": "The water is already boiling."}
                    ]
                },
                {
                    "definition_en": "to brew (coffee, tea)",
                    "examples": [
                        {"cs": "Uvařím ti kávu.", "en": "I'll make you coffee."}
                    ]
                }
            ],
            "frequency": "common",
            "notes": "Reflexive 'vařit se' = 'to be boiling' or figuratively 'to seethe'."
        }, ensure_ascii=False)
    },
    {
        "role": "user",
        "content": 'Word: striga\nPOS hint: noun'
    },
    {
        "role": "assistant",
        "content": json.dumps({
            "lemma": "striga",
            "pos": "noun",
            "gender": "f",
            "senses": [
                {
                    "definition_en": "striga, a cursed woman transformed into a monster",
                    "examples": [
                        {"cs": "Striga se skrývala v opuštěném hradu.", "en": "The striga was hiding in the abandoned castle."}
                    ],
                    "register": "literary"
                }
            ],
            "frequency": "rare",
            "notes": "From Slavic folklore. Popularized by Andrzej Sapkowski's Witcher series."
        }, ensure_ascii=False)
    }
]


def tokenize(text):
    """Tokenize Czech text."""
    tokens = []
    current = []
    for char in text:
        if char.isspace():
            if current:
                tokens.append(''.join(current))
                current = []
        elif not char.isalpha():
            if current:
                tokens.append(''.join(current))
                current = []
        else:
            current.append(char)
    if current:
        tokens.append(''.join(current))
    return tokens


def lemmatize_with_majka(words):
    """Lemmatize words using Majka with -p flag for aligned output."""
    if not MAJKA_PATH.exists():
        return {w: w for w in words}, {w: '' for w in words}

    input_text = '\n'.join(words)
    result = subprocess.run(
        [str(MAJKA_PATH), "-f", str(MAJKA_DICT), "-p"],
        input=input_text, capture_output=True, text=True, check=True
    )
    lines = result.stdout.strip().split('\n')

    lemma_map = {}
    pos_map = {}

    for line in lines:
        if not line.strip():
            continue
        # -p format: originalword:lemma1:tag1:lemma2:tag2:...
        # If no results: originalword (no colons)
        parts = line.split(':')
        original = parts[0].lower()

        if len(parts) >= 3:
            # Take the first lemma and tag
            lemma_map[original] = parts[1].lower()
            pos_map[original] = parts[2]
        else:
            lemma_map[original] = original
            pos_map[original] = ''

    # Fill in any words that Majka didn't return (unknown words)
    for w in words:
        wl = w.lower()
        if wl not in lemma_map:
            lemma_map[wl] = wl
            pos_map[wl] = ''

    return lemma_map, pos_map


def find_missing(conn, word_freq, lemma_map, pos_map):
    """Find words missing from the dictionary.

    Uses batch queries for performance (~seconds instead of ~10 minutes).
    """
    c = conn.cursor()

    all_words = set(word_freq.keys())

    # Also collect all Majka lemmas we might need to check
    all_lemmas = set()
    for w in all_words:
        lemma = lemma_map.get(w, w)
        if lemma != w:
            all_lemmas.add(lemma)

    # Batch query: which words exist as entry lemmas?
    known_entry_lemmas = set()
    word_list = list(all_words | all_lemmas)
    batch_size = 500
    for i in range(0, len(word_list), batch_size):
        batch = word_list[i:i+batch_size]
        placeholders = ','.join('?' * len(batch))
        c.execute(f"SELECT DISTINCT lemma FROM entries WHERE lemma IN ({placeholders})", batch)
        known_entry_lemmas.update(row[0] for row in c.fetchall())

    # Batch query: which words exist as inflection forms?
    known_inflection_forms = set()
    word_list_only = list(all_words)
    for i in range(0, len(word_list_only), batch_size):
        batch = word_list_only[i:i+batch_size]
        placeholders = ','.join('?' * len(batch))
        c.execute(f"SELECT DISTINCT form FROM inflections WHERE form IN ({placeholders})", batch)
        known_inflection_forms.update(row[0] for row in c.fetchall())

    # Now classify each word
    missing = []
    for word, freq in word_freq.most_common():
        # Check word directly as entry lemma
        if word in known_entry_lemmas:
            continue

        # Check word as inflection form
        if word in known_inflection_forms:
            continue

        # Check Majka lemma as entry lemma
        lemma = lemma_map.get(word, word)
        if lemma != word and lemma in known_entry_lemmas:
            continue

        missing.append({
            "word": word,
            "lemma": lemma,
            "freq": freq,
            "pos_hint": pos_map.get(word, ''),
        })

    return missing


def extract_context(text, word, max_sentences=3):
    """Extract sentences containing the word from the text."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    matching = [s.strip() for s in sentences if re.search(r'\b' + re.escape(word) + r'\b', s, re.IGNORECASE)]
    return matching[:max_sentences]


def classify_missing(missing, tokens):
    """Classify missing words as proper nouns vs real vocabulary."""
    token_set = set(tokens)
    proper_nouns = []
    vocabulary = []

    for item in missing:
        word = item["word"]
        # Heuristic: if the word appears capitalized in text, it's likely a proper noun
        capitalized = any(t for t in token_set if t.lower() == word and t[0].isupper())
        all_caps = all(t[0].isupper() for t in token_set if t.lower() == word) if capitalized else False

        if all_caps and len(word) > 1:
            item["type"] = "proper_noun"
            proper_nouns.append(item)
        else:
            item["type"] = "vocabulary"
            vocabulary.append(item)

    return vocabulary, proper_nouns


def pos_hint_to_pos(hint):
    """Convert Majka POS tag to simple POS string."""
    if not hint:
        return ""
    char = hint[0] if hint else ''
    mapping = {
        'k1': 'noun', 'k2': 'adjective', 'k3': 'pronoun',
        'k4': 'numeral', 'k5': 'verb', 'k6': 'adverb',
        'k7': 'preposition', 'k8': 'conjunction', 'k9': 'particle',
        'k0': 'interjection',
    }
    # Try to extract k-tag
    k_match = re.search(r'k(\d)', hint)
    if k_match:
        return mapping.get(f'k{k_match.group(1)}', '')

    # Fallback to first character
    char_map = {
        'N': 'noun', 'A': 'adjective', 'V': 'verb', 'D': 'adverb',
        'P': 'pronoun', 'C': 'numeral', 'R': 'preposition',
        'J': 'conjunction', 'I': 'interjection', 'T': 'particle',
    }
    return char_map.get(char, '')


async def generate_definitions(missing_words, text, api_key, batch_size=10, dry_run=False):
    """Generate definitions for missing words using DeepSeek API."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        print("ERROR: openai package required. Install with: pip install openai")
        return {}

    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com/v1"
    )

    results = {}
    total = len(missing_words)

    async def generate_one(item):
        word = item["lemma"] if item["lemma"] != item["word"] else item["word"]
        pos = pos_hint_to_pos(item.get("pos_hint", ""))
        context = extract_context(text, item["word"])

        user_content = f"Word: {word}"
        if pos:
            user_content += f"\nPOS hint: {pos}"
        if context:
            user_content += "\nContext from text:\n" + "\n".join(f"- {s}" for s in context[:2])

        if dry_run:
            return word, {"_dry_run": True, "prompt": user_content}

        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                *FEW_SHOT,
                {"role": "user", "content": user_content},
            ]

            response = await client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=1000,
            )

            entry = json.loads(response.choices[0].message.content)
            return word, entry
        except Exception as e:
            return word, {"_error": str(e)}

    for i in range(0, total, batch_size):
        batch = missing_words[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} words)...")

        tasks = [generate_one(item) for item in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in batch_results:
            if isinstance(result, Exception):
                print(f"    Error: {result}")
            else:
                word, entry = result
                results[word] = entry

        if not dry_run and i + batch_size < total:
            await asyncio.sleep(0.5)

    return results


def validate_entry(entry, word):
    """Validate a generated entry."""
    issues = []

    if "_error" in entry:
        return False, [f"Generation error: {entry['_error']}"]
    if "_dry_run" in entry:
        return True, []

    required = ["lemma", "pos", "senses"]
    for field in required:
        if field not in entry:
            issues.append(f"Missing field: {field}")

    if entry.get("pos") == "verb" and "aspect" not in entry:
        issues.append("Verb missing aspect")

    if entry.get("pos") == "noun" and "gender" not in entry:
        issues.append("Noun missing gender")

    senses = entry.get("senses", [])
    if not senses:
        issues.append("No senses")
    for i, s in enumerate(senses):
        if "definition_en" not in s:
            issues.append(f"Sense {i} missing definition_en")

    # Check for Czech chars in English definitions
    czech_chars = set("ěščřžýáíéúůďťňó")
    for s in senses:
        defn = s.get("definition_en", "").lower()
        if any(c in czech_chars for c in defn):
            issues.append(f"Definition may contain Czech: {defn[:50]}")

    return len(issues) == 0, issues


def _normalise_definition(text):
    return re.sub(r'[^a-z ]+', '', (text or "").lower()).strip()


_SENSE_STOPWORDS = frozenset(
    "a an the to of or and in on for with by that which is are be as it its "
    "something someone one from at not".split())


def _sense_words(sense):
    words = set(_normalise_definition(sense.get("definition_en")).split())
    return words - _SENSE_STOPWORDS


def _same_sense(a, b):
    """Do two glosses describe the same meaning?

    A regenerated entry restates senses in its own words ("faucet" ->
    "tap, faucet"; "pair, couple" -> "a pair or couple (of two matching
    things)"). Word-set containment alone misses the paraphrases, so fall back
    to overlap: without this every re-audit leaves the entry full of
    near-duplicate lines.
    """
    if not a or not b:
        return False
    if a <= b or b <= a:
        return True
    overlap = len(a & b)
    return overlap >= 2 and overlap / min(len(a), len(b)) >= 0.5


def merge_entry_senses(conn, lemma, pos, entry, replace=False):
    """Fold a regenerated entry into the EXISTING (lemma, pos) row.

    `INSERT OR IGNORE` against UNIQUE(lemma, pos) means nothing in this project
    could ever improve an entry that already exists -- a word flagged by
    tools/audit_entries.py would be regenerated and then silently discarded.

    replace=False unions the new senses in and never drops an existing one.
    replace=True treats the regenerated senses as authoritative, which is what a
    re-audit needs: the worker is shown the existing definitions and asked to
    keep what is right and fix what is not, so keeping the originals as well
    both duplicates every sense and preserves the errors the audit found
    ("tlacenka" glossed as haggis, "famozni" as famous). Examples, register and
    entry-level metadata are carried over from the row being replaced.

    Returns True when the row changed.
    """
    c = conn.cursor()
    c.execute("SELECT id, entry_json FROM entries WHERE lemma = ? AND pos = ?",
              (lemma, pos))
    row = c.fetchone()
    if not row:
        return False
    entry_id, existing_json = row
    try:
        existing = json.loads(existing_json)
    except (json.JSONDecodeError, TypeError):
        return False

    old_senses = list(existing.get("senses") or [])
    new_senses = [s for s in (entry.get("senses") or [])
                  if (s.get("definition_en") or "").strip()]
    changed = False

    if replace and new_senses:
        merged = []
        for sense in new_senses:
            words = _sense_words(sense)
            match = next((o for o in old_senses if _same_sense(words, _sense_words(o))), None)
            if match:
                combined = dict(match)
                combined.update(sense)
                if match.get("examples") and not sense.get("examples"):
                    combined["examples"] = match["examples"]
                merged.append(combined)
            else:
                merged.append(sense)
        if merged != old_senses:
            old_senses = merged
            changed = True
    else:
        for sense in new_senses:
            words = _sense_words(sense)
            subsumed = None
            covered = False
            for i, have in enumerate(old_senses):
                have_words = _sense_words(have)
                if not have_words:
                    continue
                if _same_sense(words, have_words):
                    subsumed = i if len(have_words) < len(words) else None
                    covered = subsumed is None
                    break
            if covered:
                continue
            if subsumed is None:
                old_senses.append(sense)
            else:
                combined = dict(old_senses[subsumed])
                combined.update(sense)
                if old_senses[subsumed].get("examples") and not sense.get("examples"):
                    combined["examples"] = old_senses[subsumed]["examples"]
                old_senses[subsumed] = combined
            changed = True

    # Fill gaps in the old row, but never overwrite what it already asserts.
    for field in ("gender", "aspect", "aspect_pair", "pronunciation",
                  "notes", "frequency"):
        if not existing.get(field) and entry.get(field):
            existing[field] = entry[field]
            changed = True

    if not changed:
        return False

    existing["senses"] = old_senses
    c.execute("UPDATE entries SET entry_json = ?, updated_at = CURRENT_TIMESTAMP "
              "WHERE id = ?", (json.dumps(existing, ensure_ascii=False), entry_id))
    return True


def adopt_placeholder_pos(conn, lemma, pos):
    """Retag a Svobodne `unknown`-POS row so a regenerated entry lands on it.

    Svobodne stores ~28.8k rows with pos='unknown'. Without this, a regenerated
    entry for the same word inserts a SECOND row under the real POS and the
    reader sees both -- the corrected gloss next to the wrong one it replaced.
    Returns True when a placeholder row was retagged to `pos`.
    """
    if pos in ("", "unknown"):
        return False
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM entries WHERE lemma = ?", (lemma,))
    if c.fetchone()[0] != 1:
        return False
    c.execute("SELECT id FROM entries WHERE lemma = ? AND pos = 'unknown'", (lemma,))
    row = c.fetchone()
    if not row:
        return False
    c.execute("UPDATE entries SET pos = ? WHERE id = ?", (pos, row[0]))
    return True


def save_entries(conn, results, refresh=False):
    """Save generated entries to the database.

    With refresh=True, an entry whose (lemma, pos) already exists is merged into
    that row instead of being dropped.
    """
    c = conn.cursor()
    saved = 0
    errors = 0
    refreshed = 0

    for word, entry in results.items():
        if "_error" in entry or "_dry_run" in entry:
            continue

        valid, issues = validate_entry(entry, word)

        lemma = entry.get("lemma", word).lower()
        pos = entry.get("pos", "unknown")
        gender = entry.get("gender")
        aspect = entry.get("aspect")
        aspect_pair = entry.get("aspect_pair")
        confidence = 0.8 if valid else 0.5

        try:
            # Must run BEFORE the insert -- see process_show_subs for why.
            if refresh:
                adopt_placeholder_pos(conn, lemma, pos)
            c.execute(
                """INSERT OR IGNORE INTO entries
                   (lemma, pos, gender, aspect, aspect_pair, entry_json, source, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, 'deepseek', ?)""",
                (lemma, pos, gender, aspect, aspect_pair,
                 json.dumps(entry, ensure_ascii=False), confidence)
            )
            if c.rowcount > 0:
                saved += 1

                if not valid:
                    entry_id = c.lastrowid
                    c.execute(
                        "INSERT INTO review_queue (entry_id, reason) VALUES (?, ?)",
                        (entry_id, "; ".join(issues))
                    )
            elif refresh and merge_entry_senses(conn, lemma, pos, entry, replace=True):
                refreshed += 1
        except Exception as e:
            print(f"  Error saving '{lemma}': {e}")
            errors += 1

    conn.commit()
    if refresh:
        print(f"  Merged into {refreshed} existing entries")
    return saved, errors


def main():
    parser = argparse.ArgumentParser(description="Process Czech text for dictionary gaps")
    parser.add_argument("text_file", help="Path to Czech text file")
    parser.add_argument("--generate", action="store_true", help="Generate definitions via DeepSeek")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be generated")
    parser.add_argument("--max-words", type=int, default=0, help="Limit number of words to generate (0=all)")
    parser.add_argument("--min-freq", type=int, default=2, help="Minimum word frequency to generate (default: 2)")
    parser.add_argument("--batch-size", type=int, default=10, help="API batch size (default: 10)")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)

    # Read text
    print(f"Reading: {args.text_file}")
    with open(args.text_file, 'r', encoding='utf-8') as f:
        text = f.read()

    # Tokenize
    tokens = tokenize(text)
    word_freq = Counter(t.lower() for t in tokens if len(t) > 1)
    print(f"Tokens: {len(tokens):,}, Unique forms: {len(word_freq):,}")

    # Lemmatize
    print("Lemmatizing with Majka...")
    unique_words = list(word_freq.keys())
    lemma_map, pos_map = lemmatize_with_majka(unique_words)

    # Find missing
    print("Checking dictionary coverage...")
    missing = find_missing(conn, word_freq, lemma_map, pos_map)

    # Classify
    vocabulary, proper_nouns = classify_missing(missing, tokens)

    # Filter by frequency
    vocab_above_freq = [v for v in vocabulary if v["freq"] >= args.min_freq]

    print(f"\n{'='*60}")
    print(f"MISSING WORD ANALYSIS")
    print(f"{'='*60}")
    print(f"  Total missing: {len(missing):,}")
    print(f"  Proper nouns: {len(proper_nouns):,}")
    print(f"  Vocabulary words: {len(vocabulary):,}")
    print(f"  Vocabulary (freq >= {args.min_freq}): {len(vocab_above_freq):,}")

    if vocab_above_freq:
        print(f"\nTop missing vocabulary words:")
        for i, item in enumerate(vocab_above_freq[:25], 1):
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
        if not api_key and not args.dry_run:
            print("\nERROR: Set DEEPSEEK_API_KEY environment variable")
            print("  export DEEPSEEK_API_KEY=your-key-here")
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
            generate_definitions(words_to_generate, text, api_key,
                                 batch_size=args.batch_size, dry_run=args.dry_run)
        )

        if args.dry_run:
            print(f"\n  Would generate {len(results)} definitions")
            for word, entry in list(results.items())[:3]:
                print(f"\n  --- {word} ---")
                print(f"  Prompt: {entry.get('prompt', '?')[:200]}")
        else:
            # Validate and save
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
        (args.text_file, len(word_freq), len(vocabulary), 0)
    )
    conn.commit()
    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()

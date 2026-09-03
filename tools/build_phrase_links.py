#!/usr/bin/env python3
"""Link every multi-word entry back to its component words.

The dictionary holds ~33k multi-word headwords, but a reader taps a single word,
and the exporters only ever index the full phrase string. So "živý plot" (hedge)
is unreachable from "plot" (fence), and "vrátit se" (come back) is unreachable
from "vrátit" (return something) -- the reader gets a confidently wrong answer.

This builds a component -> phrase index using the Majka lemmatiser, so the
exporters can append a "Phrases:" block to the component word's entry. Entirely
offline and deterministic; no LLM.

Usage:
    python3 tools/build_phrase_links.py            # rebuild the table
    python3 tools/build_phrase_links.py --dry-run  # report only, write nothing
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "dictionary.db"
MAJKA_BIN = PROJECT_ROOT / "majka"
MAJKA_DICT = PROJECT_ROOT / "majka.w-lt"

MAX_PHRASE_WORDS = 4
MAX_LINKS_PER_COMPONENT = 8

# Words that must not become an index key of their own -- linking every phrase
# containing "na" or "být" would bury the useful entries. They stay inside the
# stored phrase string, they just do not get their own back-link.
STOPWORDS = set("""
a i o u v k s z na do od po za pro při ve ze ke se si
je to co ne že aby jak ale nebo než už jen tak ten ta ty
být mít jako
""".split())

# Svobodné stores whole sentences and abbreviation notes as if they were
# headwords ("nemohu si dovolit nové auto.", "hl.m. - nový zéland").
_JUNK_RE = re.compile(r'[.,()\[\]!?;:/–—0-9"]|hl\.m\.|např\.')
_CZECH_RE = re.compile(r'^[a-záčďéěíňóřšťúůýžťďňá-ž -]+$')


def load_multiword(conn):
    c = conn.cursor()
    c.execute(
        "SELECT lemma, pos, source, entry_json FROM entries WHERE lemma LIKE '% %'"
    )
    return c.fetchall()


def keep_phrase(lemma, pos):
    if pos == "proper_noun":
        return False
    words = lemma.split()
    if not 2 <= len(words) <= MAX_PHRASE_WORDS:
        return False
    if _JUNK_RE.search(lemma):
        return False
    if not _CZECH_RE.match(lemma):
        return False
    return True


def majka_lemmas(tokens):
    """Batch-lemmatise. -p echoes the input word so output lines can be aligned.

    Returns (best lemma per token, set of all candidate lemmas per token). The
    candidate set matters because Majka is genuinely ambiguous for some forms --
    "páry" is both pár (pair) and pára (steam) -- and guessing wrong files
    "desorpce vodní páry" under "pár".
    """
    proc = subprocess.run(
        [str(MAJKA_BIN), "-f", str(MAJKA_DICT), "-p"],
        input="\n".join(tokens), capture_output=True, text=True,
    )
    best, alternatives = {}, {}
    for line in proc.stdout.splitlines():
        head, sep, rest = line.partition(":")
        if not sep:
            continue
        # -p emits one line per input word: "form:lemma:tag:lemma:tag:..."
        # so the lemmas are the even-indexed fields.
        fields = rest.split(":")
        candidates = [f.lower() for f in fields[0::2] if f]
        if candidates:
            best[head] = Counter(candidates).most_common(1)[0][0]
            alternatives[head] = set(candidates)
    return best, alternatives


_WORD_RE = re.compile(r'[a-záčďéěíňóřšťúůýž]+')


def gloss_words(entry_json):
    """Content words of an entry's English definitions, for the idiomaticity test."""
    try:
        data = json.loads(entry_json)
    except (json.JSONDecodeError, TypeError):
        return set()
    words = set()
    for sense in data.get("senses", []):
        for w in re.findall(r"[a-z']+", (sense.get("definition_en") or "").lower()):
            if len(w) > 2:
                words.add(w)
    return words


def first_gloss(entry_json):
    try:
        data = json.loads(entry_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    senses = data.get("senses") or []
    return (senses[0].get("definition_en") or "").strip() if senses else ""


SOURCE_WEIGHT = {
    "wiktionary": 3.0, "claude": 3.0, "manual": 3.0,
    "deepseek": 2.5, "svobodne": 1.5,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cap", type=int, default=MAX_LINKS_PER_COMPONENT)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = load_multiword(conn)
    print(f"multi-word entries: {len(rows):,}")
    kept = [r for r in rows if keep_phrase(r["lemma"], r["pos"])]
    print(f"  after junk filter: {len(kept):,}")

    tokens = sorted({w for r in kept for w in r["lemma"].split() if w.isalpha()})
    print(f"  component tokens to lemmatise: {len(tokens):,}")
    lemma_of, lemma_alts = majka_lemmas(tokens)
    print(f"  Majka resolved: {len(lemma_of):,}")

    # Glosses of the single-word entries, used for the idiomaticity bonus.
    c = conn.cursor()
    c.execute("SELECT lemma, entry_json FROM entries WHERE lemma NOT LIKE '% %'")
    base_glosses = defaultdict(set)
    for lemma, entry_json in c:
        base_glosses[lemma] |= gloss_words(entry_json)

    links = defaultdict(list)  # component -> [(score, phrase, phrase_pos)]
    for row in kept:
        phrase, pos, source = row["lemma"], row["pos"], row["source"]
        words = phrase.split()
        phrase_words = gloss_words(row["entry_json"])
        base = SOURCE_WEIGHT.get(source, 1.0)
        base += {2: 2.0, 3: 1.0}.get(len(words), 0.0)

        seen = set()
        for word in words:
            if len(word) < 2:
                continue
            component = lemma_of.get(word, word).lower()
            if component in STOPWORDS or component in seen or component == phrase:
                continue
            # Ambiguous surface form whose readings are BOTH real headwords:
            # linking it would file the phrase under the wrong word half the
            # time, so link it under neither.
            plausible = {alt for alt in lemma_alts.get(word, {component})
                         if alt in base_glosses}
            if word != component and len(plausible) > 1:
                continue
            seen.add(component)

            score = base
            # Non-compositional phrases are the ones a reader cannot guess:
            # "živý plot" = hedge shares nothing with "plot" = fence, whereas
            # "tyčkový plot" = picket fence is already obvious.
            if phrase_words and not (phrase_words & base_glosses.get(component, set())):
                score += 2.0
            # A reflexive whose bare verb also exists is the single highest-value
            # case: "vrátit" = return something, "vrátit se" = come back.
            # Require the literal infinitive as the headword, so the Svobodné
            # row "učí se" does not outrank the real entry "učit se".
            if len(words) == 2 and words[1] in ("se", "si") and words[0] == component:
                score += 10.0
            links[component].append((score, phrase, pos))

    total_before = sum(len(v) for v in links.values())
    for component in links:
        links[component].sort(key=lambda t: (-t[0], len(t[1]), t[1]))
        # One phrase string can exist under several POS rows (a svobodne
        # "unknown" alongside a wiktionary noun); keep only the best-scoring one.
        deduped, seen_phrase = [], set()
        for score, phrase, pos in links[component]:
            if phrase in seen_phrase:
                continue
            seen_phrase.add(phrase)
            deduped.append((score, phrase, pos))
        links[component] = deduped[:args.cap]
    total = sum(len(v) for v in links.values())
    print(f"\ncomponents: {len(links):,}   links: {total:,} (capped from {total_before:,})")

    for probe in ("plot", "houba", "vrátit", "učit", "voda", "oko"):
        shown = [f"{p} [{s:.1f}]" for s, p, _ in links.get(probe, [])]
        print(f"  {probe}: {shown}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    c.executescript("""
        DROP TABLE IF EXISTS phrase_links;
        CREATE TABLE phrase_links (
            component  TEXT NOT NULL,
            phrase     TEXT NOT NULL,
            phrase_pos TEXT NOT NULL,
            score      REAL DEFAULT 0
        );
    """)
    c.executemany(
        "INSERT INTO phrase_links (component, phrase, phrase_pos, score) VALUES (?,?,?,?)",
        [(comp, phrase, pos, score)
         for comp, items in links.items() for score, phrase, pos in items],
    )
    c.execute("CREATE INDEX idx_phrase_links_component ON phrase_links(component)")
    conn.commit()
    print(f"\nwrote {total:,} rows to phrase_links")
    return 0


if __name__ == "__main__":
    sys.exit(main())

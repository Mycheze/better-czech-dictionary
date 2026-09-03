#!/usr/bin/env python3
"""Find entries that EXIST but are not good enough, ranked by how often they are read.

Every gap check in this project asks only "is there a row?"
(process_text.find_missing, test_dictionary.test_coverage). So a word with one
impoverished sense is invisible forever, and `INSERT OR IGNORE` on
UNIQUE(lemma,pos) means nothing can replace it. That is why "houbička" stayed
"diminutive of houba" and why raw coverage reads 99.99% while real lookups fail.

This flags thin entries and emits a candidate shard payload in the same shape
processing/yt_swarm.py already uses, so the existing swarm can regenerate them.

Usage:
    python3 tools/audit_entries.py --report
    python3 tools/audit_entries.py --limit 5000 --out show_subs_cache/thin_candidates.json
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "exporters"))
from export_stardict import is_crossref_sense, parse_crossref  # noqa: E402

DB_PATH = PROJECT_ROOT / "dictionary.db"
YT_STATS = PROJECT_ROOT / "youtube_corpus" / "word_stats.tsv"
CONTEXT_CORPORA = [
    PROJECT_ROOT / "books_corpus.txt",
    PROJECT_ROOT / "subs_corpus.txt",
]

MIN_CHANNELS = 5      # cross-channel dispersion: filters one-video noise
MAX_CONTEXTS = 3

_FORM_OF_RE = re.compile(
    r'^\s*(?:\w+[\s/,-]+)*?'
    r'(?:diminutive|augmentative|genitive|dative|vocative|locative|instrumental|'
    r'accusative|nominative|plural|singular|feminine|masculine|neuter|verbal noun|'
    r'alternative form|alternative spelling|superlative|comparative|past participle|'
    r'passive participle|present participle|obsolete form|rare form|archaic form|'
    r'abbreviation|initialism|acronym|inflection|synonym|form)\s+of\s',
    re.IGNORECASE,
)
_NAME_RE = re.compile(
    r'^\s*(?:an?\s+)?(?:male\s+|female\s+|common\s+|masculine\s+|feminine\s+)?'
    r'(?:surname|given\s+name|family\s+name|patronymic)\b', re.IGNORECASE)

# "drag, haul, lug" -- a list of bare synonyms with no disambiguating gloss.
_BARE_SYNONYMS_RE = re.compile(r'^[a-z][a-z\' -]*(?:,\s*[a-z][a-z\' -]*)+$', re.IGNORECASE)


# Only content words can plausibly be hiding a missing sense. Function words and
# adverbs glossed with a synonym list ("vlastně" -> actually, as a matter of
# fact) are complete entries, not gaps.
CONTENT_POS = {"noun", "verb", "adjective", "unknown", ""}


def classify(variants, resolvable, is_inflection_of_other=False):
    """Return a reason string if the lemma's entries are collectively thin.

    `resolvable(target)` reports whether a cross-reference target exists with
    real senses -- the exporter already rewrites those into the target's full
    entry, so "mi = clitic dative of já" is NOT a gap and must not be requeued.
    """
    senses, sources, poses, has_examples = [], set(), set(), False
    for pos, source, data in variants:
        sources.add(source)
        poses.add(pos)
        for sense in data.get("senses", []):
            text = (sense.get("definition_en") or "").strip()
            if text:
                senses.append(text)
            if sense.get("examples"):
                has_examples = True
    if not senses:
        return "no_senses"

    if all(_NAME_RE.match(s) for s in senses):
        return "name_only"

    if all(is_crossref_sense(s) or _FORM_OF_RE.match(s) for s in senses):
        # Only a problem when the redirect dead-ends.
        for text in senses:
            _rel, target, embedded = parse_crossref(text)
            if embedded or (target and resolvable(target)):
                return None
        return "unresolved_crossref"

    if not (poses & CONTENT_POS):
        return None

    # The lemma is really an inflected form that picked up its own entry
    # ("videa", "uvidíme", "funguje"). Its base word is what needs the work.
    if is_inflection_of_other:
        return None

    if len(senses) == 1:
        only = senses[0]
        if sources & {"svobodne", "deepseek", "claude"} and not has_examples:
            return ("bare_synonyms" if _BARE_SYNONYMS_RE.match(only)
                    else "single_sense_generated")
        if sources == {"wiktionary"} and _BARE_SYNONYMS_RE.match(only):
            return "bare_synonyms"
    return None


def load_frequency():
    freq = {}
    if not YT_STATS.exists():
        print(f"  (no {YT_STATS}; ranking alphabetically)", file=sys.stderr)
        return freq
    with open(YT_STATS, encoding="utf-8") as f:
        next(f, None)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            try:
                count, channels = int(parts[1]), int(parts[2])
            except ValueError:
                continue
            if channels >= MIN_CHANNELS:
                freq[parts[0]] = count
    return freq


_SENT_RE = re.compile(r'[^.!?…]+[.!?…]?')


def collect_contexts(wanted):
    """One pass per corpus; keeps up to MAX_CONTEXTS sentences per wanted word."""
    out = defaultdict(list)
    for path in CONTEXT_CORPORA:
        if not path.exists():
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                lowered = line.lower()
                for word in set(re.findall(r"[a-záčďéěíňóřšťúůýž]+", lowered)):
                    if word not in wanted or len(out[word]) >= MAX_CONTEXTS:
                        continue
                    for sentence in _SENT_RE.findall(line.strip()):
                        sentence = sentence.strip()
                        if 15 < len(sentence) < 200 and word in sentence.lower():
                            out[word].append(sentence)
                            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--out")
    ap.add_argument("--report", action="store_true", help="summary only")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    c = conn.cursor()
    c.execute("SELECT lemma, pos, source, entry_json FROM entries")
    by_lemma = defaultdict(list)
    for lemma, pos, source, entry_json in c:
        try:
            by_lemma[lemma].append((pos, source, json.loads(entry_json)))
        except json.JSONDecodeError:
            continue

    # A cross-reference target counts as resolvable when it has a real sense.
    real_sense_lemmas = set()
    for lemma, variants in by_lemma.items():
        for _pos, _source, data in variants:
            if any(not is_crossref_sense(s.get("definition_en") or "")
                   for s in data.get("senses", [])):
                real_sense_lemmas.add(lemma.lower())
                break

    freq = load_frequency()
    print(f"lemmas: {len(by_lemma):,}   corpus words (>={MIN_CHANNELS} channels): {len(freq):,}")

    # Lemmas that are also an inflected form of a DIFFERENT lemma.
    c.execute("SELECT DISTINCT form FROM inflections WHERE form <> lemma")
    inflected_forms = {row[0] for row in c}

    thin = []
    reasons = Counter()
    for lemma, variants in by_lemma.items():
        if " " in lemma:
            continue
        reason = classify(variants, real_sense_lemmas.__contains__,
                          lemma in inflected_forms)
        if not reason:
            continue
        reasons[reason] += 1
        thin.append((freq.get(lemma, 0), lemma, reason, variants))

    thin.sort(key=lambda t: (-t[0], t[1]))
    in_corpus = [t for t in thin if t[0] > 0]

    print(f"\nthin lemmas: {len(thin):,}   of which seen in the corpus: {len(in_corpus):,}")
    for reason, count in reasons.most_common():
        print(f"  {reason:<24} {count:,}")

    print(f"\ntop 20 thin lemmas by corpus frequency:")
    for count, lemma, reason, variants in thin[:20]:
        gloss = ""
        for _pos, _source, data in variants:
            senses = data.get("senses") or []
            if senses:
                gloss = (senses[0].get("definition_en") or "")[:60]
                break
        print(f"  {count:>7}  {lemma:<18} {reason:<24} {gloss}")

    if args.report:
        return 0

    selected = in_corpus[:args.limit]
    print(f"\ncollecting contexts for {len(selected):,} words...")
    contexts = collect_contexts({lemma for _f, lemma, _r, _v in selected})

    payload = []
    for count, lemma, reason, variants in selected:
        pos_hint = ""
        for pos, _source, _data in variants:
            if pos and pos != "unknown":
                pos_hint = pos
                break
        payload.append({
            "headword": lemma,
            "observed_form": lemma,
            "pos_hint": pos_hint,
            "freq": count,
            "reason": reason,
            "existing": [s.get("definition_en")
                         for _p, _s, d in variants
                         for s in d.get("senses", [])],
            "contexts": contexts.get(lemma, []),
        })

    out_path = Path(args.out) if args.out else PROJECT_ROOT / "show_subs_cache" / "thin_candidates.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(payload):,} candidates to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

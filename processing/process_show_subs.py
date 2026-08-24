#!/usr/bin/env python3
"""
Fold a directory of TV-show subtitles into the dictionary.

Unlike the YouTube corpus (auto-captions, heavy ASR noise), these are mostly
human-made broadcast subs grouped one-folder-per-show, so the noise profile is
proper nouns and colloquial spellings rather than mishearings. Dispersion is
measured across shows instead of channels.

The LLM steps (screening + entry generation) are decoupled from this script:
it EXPORTS candidate words to JSON and IMPORTS verdicts/entries produced by an
external model (e.g. Claude agents), so the pipeline is not tied to one API.

Usage:
    process_show_subs.py --dir DIR                        # coverage report
    process_show_subs.py --dir DIR --export-candidates candidates.json
    process_show_subs.py --dir DIR --import-screen screen.json \
                         --import-entries entries.json    # save + final report

Screen JSON:  {"word": {"c": "word|proper_noun|foreign|error", "lemma": "..."}}
Entries JSON: {"lemma": {<entry in the standard entry_json schema>}}
"""

import argparse
import hashlib
import io
import json
import re
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from process_text import (
    tokenize, lemmatize_with_majka, find_missing, pos_hint_to_pos,
    validate_entry,
)
from yt_word_filter import structural_verdict, looks_like_proper_noun

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "dictionary.db"

TIMESTAMP_RE = re.compile(r"-->")
TAG_RE = re.compile(r"<[^>]+>")
ENTITY_RE = re.compile(r"&(?:amp|lt|gt|nbsp|quot|#\d+);")
SKIP_DIRS = {"__pycache__", "Reformatted"}
SKIP_SUFFIXES = {".py", ".pyc", ".csv", ".json"}


def clean_subtitle_text(raw):
    """Strip VTT/SRT scaffolding, keep dialogue lines only."""
    lines = []
    prev = None
    for line in raw.split("\n"):
        s = line.strip().lstrip("﻿")
        if not s:
            continue
        if TIMESTAMP_RE.search(s):
            continue
        if s.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE", "X-TIMESTAMP")):
            continue
        if s.isdigit():  # SRT cue numbers
            continue
        s = TAG_RE.sub("", s)
        s = ENTITY_RE.sub(" ", s)
        if not s.strip():
            continue
        # Auto-caption rolling windows repeat each line; collapse them.
        if s == prev:
            continue
        prev = s
        lines.append(s)
    return "\n".join(lines)


def iter_subtitle_files(root):
    """Yield (show, name, raw_text) for every subtitle file, zips included."""
    root = Path(root)
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        show = rel.parts[0] if len(rel.parts) > 1 else "_root"

        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    inner = Path(info.filename)
                    if inner.suffix.lower() not in (".vtt", ".srt", ".txt"):
                        continue
                    zshow = inner.parts[0] if len(inner.parts) > 1 else path.stem
                    raw = zf.read(info).decode("utf-8", errors="replace")
                    yield zshow, info.filename, raw
            continue

        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.suffix.lower() not in (".vtt", ".srt", ".txt", ""):
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not raw.strip():
            continue
        # Extensionless files must at least look like subtitles.
        if path.suffix == "" and "-->" not in raw:
            continue
        yield show, str(rel), raw


def build_corpus(root):
    """Read every subtitle file, dedup identical content, group text by show."""
    show_texts = defaultdict(list)
    seen_hashes = set()
    files = dupes = 0
    for show, name, raw in iter_subtitle_files(root):
        text = clean_subtitle_text(raw)
        if not text:
            continue
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if digest in seen_hashes:
            dupes += 1
            continue
        seen_hashes.add(digest)
        show_texts[show].append(text)
        files += 1
    return {s: "\n".join(t) for s, t in show_texts.items()}, files, dupes


def collect_stats(show_corpus):
    """Per-form frequency, capitalization count and show dispersion."""
    freq = Counter()
    caps = Counter()
    shows = defaultdict(set)
    show_freqs = {}
    for show, text in show_corpus.items():
        tokens = [t for t in tokenize(text) if len(t) > 1]
        sf = Counter(t.lower() for t in tokens)
        show_freqs[show] = sf
        freq.update(sf)
        for t in tokens:
            if t[0].isupper():
                caps[t.lower()] += 1
        for w in sf:
            shows[w].add(show)
    return freq, caps, {w: len(s) for w, s in shows.items()}, show_freqs


def find_contexts(show_corpus, wanted, max_per_word=3):
    """One pass over the corpus collecting example lines per candidate word."""
    contexts = defaultdict(list)
    wanted = set(wanted)
    word_re = re.compile(r"[^\W\d_]+", re.UNICODE)
    for text in show_corpus.values():
        for line in text.split("\n"):
            if len(contexts) == len(wanted) and all(
                    len(v) >= max_per_word for v in contexts.values()):
                break
            for tok in word_re.findall(line):
                w = tok.lower()
                if w in wanted and len(contexts[w]) < max_per_word:
                    contexts[w].append(line.strip()[:200])
    return dict(contexts)


def coverage(conn, word_freq, lemma_map, pos_map):
    missing = find_missing(conn, word_freq, lemma_map, pos_map)
    total = sum(word_freq.values())
    miss_tokens = sum(m["freq"] for m in missing)
    form_cov = (len(word_freq) - len(missing)) / len(word_freq) * 100 if word_freq else 0
    token_cov = (total - miss_tokens) / total * 100 if total else 0
    return missing, form_cov, token_cov


def per_show_coverage(conn, show_freqs, lemma_map, pos_map):
    rows = []
    for show, sf in sorted(show_freqs.items()):
        missing, form_cov, token_cov = coverage(conn, sf, lemma_map, pos_map)
        rows.append((show, sum(sf.values()), len(sf), form_cov, token_cov))
    return rows


def save_generated_entries(conn, entries, source):
    """Validate and insert generated entries; queue imperfect ones for review."""
    c = conn.cursor()
    saved = errors = 0
    for word, entry in entries.items():
        if not isinstance(entry, dict) or "senses" not in entry:
            errors += 1
            continue
        valid, issues = validate_entry(entry, word)
        lemma = str(entry.get("lemma", word)).lower()
        try:
            c.execute(
                """INSERT OR IGNORE INTO entries
                   (lemma, pos, gender, aspect, aspect_pair, entry_json, source, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (lemma, entry.get("pos", "unknown"), entry.get("gender"),
                 entry.get("aspect"), entry.get("aspect_pair"),
                 json.dumps(entry, ensure_ascii=False), source,
                 0.8 if valid else 0.5))
            if c.rowcount > 0:
                saved += 1
                if not valid:
                    c.execute("INSERT INTO review_queue (entry_id, reason) VALUES (?, ?)",
                              (c.lastrowid, "; ".join(issues)))
        except Exception as e:
            print(f"  save error '{lemma}': {e}")
            errors += 1
    conn.commit()
    return saved, errors


def link_forms(conn, form_to_lemma, source):
    """Point colloquial/unlisted surface forms at their existing headwords."""
    c = conn.cursor()
    lemmas = list(set(form_to_lemma.values()))
    pos_lookup = {}
    for i in range(0, len(lemmas), 500):
        batch = lemmas[i:i + 500]
        ph = ",".join("?" * len(batch))
        c.execute(f"SELECT lemma, pos FROM entries WHERE lemma IN ({ph})", batch)
        for lemma, pos in c.fetchall():
            pos_lookup.setdefault(lemma, pos)

    linked = 0
    for form, lemma in form_to_lemma.items():
        if form == lemma or lemma not in pos_lookup:
            continue
        c.execute("SELECT 1 FROM inflections WHERE form=? AND lemma=? LIMIT 1",
                  (form, lemma))
        if c.fetchone():
            continue
        c.execute("INSERT INTO inflections (form, lemma, pos, tag, source)"
                  " VALUES (?, ?, ?, 'colloquial', ?)",
                  (form, lemma, pos_lookup[lemma], source))
        linked += 1
    conn.commit()
    return linked


def main():
    ap = argparse.ArgumentParser(description="Fold TV-show subtitles into the dictionary")
    ap.add_argument("--dir", required=True, help="Directory of show subfolders")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--min-freq", type=int, default=1)
    ap.add_argument("--min-shows", type=int, default=1)
    ap.add_argument("--cap-threshold", type=float, default=0.85)
    ap.add_argument("--export-candidates", metavar="FILE",
                    help="Write filtered candidate words + contexts to JSON")
    ap.add_argument("--import-screen", metavar="FILE",
                    help="JSON of screening verdicts keyed by surface form")
    ap.add_argument("--import-entries", metavar="FILE",
                    help="JSON of generated entries keyed by headword")
    ap.add_argument("--source", default="claude",
                    help="`source` value for inserted rows (default: claude)")
    ap.add_argument("--per-show", action="store_true", help="Print per-show coverage")
    args = ap.parse_args()

    print(f"Reading subtitles from {args.dir} ...")
    show_corpus, files, dupes = build_corpus(args.dir)
    print(f"  Shows: {len(show_corpus)}   Files used: {files}   Duplicate files skipped: {dupes}")

    freq, caps, dispersion, show_freqs = collect_stats(show_corpus)
    total_tokens = sum(freq.values())
    print(f"  Unique forms: {len(freq):,}   Total tokens: {total_tokens:,}")

    print("Lemmatizing with Majka...")
    lemma_map, pos_map = lemmatize_with_majka(list(freq.keys()))

    conn = sqlite3.connect(args.db)
    print("Checking dictionary coverage...")
    missing, form_cov, token_cov = coverage(conn, freq, lemma_map, pos_map)
    print(f"\n{'='*64}\nBASELINE COVERAGE\n{'='*64}")
    print(f"  Forms covered:  {len(freq)-len(missing):,}/{len(freq):,} ({form_cov:.2f}%)")
    print(f"  Tokens covered: {total_tokens-sum(m['freq'] for m in missing):,}"
          f"/{total_tokens:,} ({token_cov:.2f}%)")
    print(f"  Missing forms:  {len(missing):,}")

    # ---- Structural / dispersion / capitalization filters ----------------
    rejected = Counter()
    candidates = []
    for m in missing:
        w = m["word"]
        reason = structural_verdict(w)
        if reason:
            rejected[reason] += 1
            continue
        if dispersion.get(w, 0) < args.min_shows:
            rejected["low_dispersion"] += 1
            continue
        if freq[w] < args.min_freq:
            rejected["low_frequency"] += 1
            continue
        if looks_like_proper_noun(w, freq[w], caps.get(w, 0), args.cap_threshold):
            rejected["proper_noun_caps"] += 1
            continue
        candidates.append(m)

    print(f"\nFILTERING\n  Missing forms: {len(missing):,}")
    for reason, n in rejected.most_common():
        print(f"    rejected {reason:24} {n:8,}")
    print(f"  Candidates after filtering: {len(candidates):,}")
    cand_tokens = sum(m["freq"] for m in candidates)
    print(f"  Candidate tokens: {cand_tokens:,} "
          f"({cand_tokens/total_tokens*100:.2f}% of corpus)")

    if args.per_show:
        print(f"\n{'='*64}\nPER-SHOW COVERAGE\n{'='*64}")
        for show, toks, forms, fc, tc in per_show_coverage(conn, show_freqs, lemma_map, pos_map):
            print(f"  {show[:40]:42} tokens={toks:8,}  token-cov={tc:6.2f}%")

    if args.export_candidates:
        words = [m["word"] for m in candidates]
        contexts = find_contexts(show_corpus, words)
        payload = [{
            "word": m["word"],
            "majka_lemma": m["lemma"],
            "pos_hint": pos_hint_to_pos(m.get("pos_hint", "")),
            "freq": m["freq"],
            "shows": dispersion.get(m["word"], 0),
            "caps_ratio": round(caps.get(m["word"], 0) / m["freq"], 2),
            "contexts": contexts.get(m["word"], []),
        } for m in sorted(candidates, key=lambda x: -x["freq"])]
        Path(args.export_candidates).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nWrote {len(payload):,} candidates -> {args.export_candidates}")
        print("Worker briefs for the screening/generation models: "
              "processing/prompts/")

    screen = {}
    if args.import_screen:
        screen = json.loads(Path(args.import_screen).read_text(encoding="utf-8"))
        verdicts = Counter(v.get("c", "?") for v in screen.values())
        print(f"\nScreen verdicts: {dict(verdicts)}")

    if args.import_entries:
        entries = json.loads(Path(args.import_entries).read_text(encoding="utf-8"))
        saved, errors = save_generated_entries(conn, entries, args.source)
        print(f"\nSaved entries: {saved:,}   Errors: {errors}")

        # Link every screened-real surface form to its headword.
        form_to_lemma = {}
        for w, v in screen.items():
            if v.get("c") == "word" and v.get("lemma"):
                form_to_lemma[w.lower()] = str(v["lemma"]).lower()
        linked = link_forms(conn, form_to_lemma, args.source)
        print(f"Linked surface forms: {linked:,}")

        c = conn.cursor()
        c.execute("INSERT INTO processed_texts (filename, total_lemmas, missing_lemmas,"
                  " new_entries_added) VALUES (?, ?, ?, ?)",
                  (f"{args.dir} (TV show subtitles)", len(freq), len(missing), saved))
        conn.commit()

    if screen:
        # ---- Final coverage ---------------------------------------------
        missing_after, form_cov2, token_cov2 = coverage(conn, freq, lemma_map, pos_map)
        noise_tokens = 0
        for m in missing_after:
            w = m["word"]
            cat = screen.get(w, {}).get("c")
            if cat in ("proper_noun", "foreign", "error", "asr_error"):
                noise_tokens += m["freq"]
            elif cat is None and (
                    structural_verdict(w) or
                    looks_like_proper_noun(w, freq[w], caps.get(w, 0),
                                           args.cap_threshold)):
                # Never screened because a pre-filter rejected it: count it as
                # the noise the filter said it was, not as a real-word gap.
                noise_tokens += m["freq"]
        real_total = total_tokens - noise_tokens
        real_missing = sum(m["freq"] for m in missing_after) - noise_tokens
        real_cov = (real_total - real_missing) / real_total * 100 if real_total else 0

        print(f"\n{'='*64}\nFINAL COVERAGE\n{'='*64}")
        print(f"  Form coverage:   {form_cov:6.2f}%  ->  {form_cov2:6.2f}%")
        print(f"  Token coverage:  {token_cov:6.2f}%  ->  {token_cov2:6.2f}%")
        print(f"  Real-word token coverage (excl. names/foreign/errors): {real_cov:6.2f}%")
        if args.per_show:
            print(f"\nPER-SHOW COVERAGE (after)")
            for show, toks, forms, fc, tc in per_show_coverage(conn, show_freqs, lemma_map, pos_map):
                print(f"  {show[:40]:42} tokens={toks:8,}  token-cov={tc:6.2f}%")

    conn.close()


if __name__ == "__main__":
    main()

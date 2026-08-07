#!/usr/bin/env python3
"""
Fold the YouTube subtitle corpus into the dictionary.

Pipeline:
  1. Load word_stats.tsv (form -> freq / channel dispersion / capitalization)
  2. Lemmatize every form with Majka
  3. Ask the database which forms it already covers
  4. Filter the gap down to plausible Czech vocabulary (yt_word_filter)
  5. Screen survivors with DeepSeek to strip ASR errors / names / foreign words
  6. Generate dictionary entries for confirmed words and save them
  7. Report token coverage before and after, over *real* words

Steps 5 and 6 cache to disk, so an interrupted run resumes without re-paying.

Usage:
    python3 process_yt_corpus.py --report                 # coverage only, no API
    python3 process_yt_corpus.py --screen                 # + LLM screening
    python3 process_yt_corpus.py --screen --generate      # full run
    python3 process_yt_corpus.py --screen --generate --max-words 500
"""

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from process_text import (  # noqa: E402
    lemmatize_with_majka, find_missing, pos_hint_to_pos,
    validate_entry, save_entries, SYSTEM_PROMPT, FEW_SHOT,
)
from yt_word_filter import (  # noqa: E402
    structural_verdict, looks_like_proper_noun,
    SCREEN_SYSTEM_PROMPT, build_screen_prompt, parse_screen_response,
)

DB_PATH = PROJECT_ROOT / "dictionary.db"
CORPUS_DIR = PROJECT_ROOT / "youtube_corpus"
STATS_FILE = CORPUS_DIR / "word_stats.tsv"
CORPUS_FILE = CORPUS_DIR / "corpus.txt"
SCREEN_CACHE = CORPUS_DIR / "screen_results.json"
GEN_CACHE = CORPUS_DIR / "generated_entries.json"
REPORT_FILE = CORPUS_DIR / "coverage_report.json"

DEFAULT_MODEL = "deepseek-v4-flash"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_word_stats():
    if not STATS_FILE.exists():
        print(f"ERROR: {STATS_FILE} not found. Run build_yt_corpus.py first.")
        sys.exit(1)

    stats = {}
    with open(STATS_FILE, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        idx = {name: i for i, name in enumerate(header)}
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            word = parts[idx["word"]]
            stats[word] = {
                "freq": int(parts[idx["freq"]]),
                "channels": int(parts[idx["channels"]]),
                "videos": int(parts[idx["videos"]]),
                "caps": int(parts[idx["caps"]]) if "caps" in idx and len(parts) > idx["caps"] else 0,
            }
    return stats


def build_context_index(corpus_path, wanted, max_per_word=2):
    """One pass over the corpus collecting example sentences for `wanted` words.

    Doing this per-word with a regex would rescan a ~100 MB corpus thousands of
    times; a single pass keeps it to seconds.
    """
    contexts = {w: [] for w in wanted}
    if not corpus_path.exists():
        return contexts

    wanted_set = set(wanted)
    remaining = set(wanted)
    token_re = re.compile(r"[^\W\d_]+", re.UNICODE)
    sentence_split = re.compile(r"(?<=[.!?])\s+")

    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            for sent in sentence_split.split(line):
                sent = sent.strip()
                if not (8 <= len(sent) <= 300):
                    continue
                toks = {t.lower() for t in token_re.findall(sent)}
                hit = toks & remaining
                if not hit:
                    continue
                for w in hit:
                    if w in wanted_set and len(contexts[w]) < max_per_word:
                        contexts[w].append(sent)
                        if len(contexts[w]) >= max_per_word:
                            remaining.discard(w)
            if not remaining:
                break
    return contexts


def load_cache(path):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"WARNING: {path} corrupt, ignoring")
    return {}


def save_cache(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def get_api_key():
    import os
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        kf = PROJECT_ROOT / "deepseek_key.txt"
        if kf.exists():
            key = kf.read_text().strip()
    return key


def lemmas_present(conn, lemmas):
    """Subset of `lemmas` that already have an entry in the database."""
    found = set()
    c = conn.cursor()
    lemmas = list(lemmas)
    for i in range(0, len(lemmas), 500):
        batch = lemmas[i:i + 500]
        ph = ",".join("?" * len(batch))
        c.execute(f"SELECT DISTINCT lemma FROM entries WHERE lemma IN ({ph})", batch)
        found.update(r[0] for r in c.fetchall())
    return found


def save_inflections(conn, form_to_lemma, pos_lookup):
    """Link observed surface forms to their headwords.

    A colloquial form like "cejtím" is absent from MorfFlex, so the dictionary
    reports it missing even though its headword "cítit" is already present.
    Writing the form->lemma link makes the form findable at lookup time without
    generating a redundant entry, which is both cheaper and cleaner than adding
    one headword per spoken variant.
    """
    c = conn.cursor()

    # Headwords that already existed carry no generated entry, so read their
    # part of speech straight from the database rather than storing "unknown".
    unknown = [l for l in set(form_to_lemma.values()) if l not in pos_lookup]
    for i in range(0, len(unknown), 500):
        batch = unknown[i:i + 500]
        ph = ",".join("?" * len(batch))
        c.execute(f"SELECT lemma, pos FROM entries WHERE lemma IN ({ph})", batch)
        for lemma, pos in c.fetchall():
            pos_lookup.setdefault(lemma, pos)

    inserted = 0
    for form, lemma in form_to_lemma.items():
        if form == lemma:
            continue
        c.execute("SELECT 1 FROM inflections WHERE form=? AND lemma=? LIMIT 1", (form, lemma))
        if c.fetchone():
            continue
        c.execute(
            "INSERT INTO inflections (form, lemma, pos, tag, source) VALUES (?, ?, ?, ?, 'youtube')",
            (form, lemma, pos_lookup.get(lemma, "unknown"), "colloquial"),
        )
        inserted += 1
    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# LLM screening
# ---------------------------------------------------------------------------

async def screen_words(words, contexts, api_key, model, batch_size=40,
                       concurrency=8, cache=None, checkpoint=None):
    """Classify candidate words. Returns {word: (category, lemma)}."""
    from openai import AsyncOpenAI

    cache = cache if cache is not None else {}
    # "unscreened" means the API never gave a verdict (rate limit, empty reply).
    # Those words must be retried, not treated as settled -- otherwise a burst
    # of 429s would silently drop them from the dictionary forever.
    todo = [w for w in words
            if w not in cache or cache.get(w, [None])[0] == "unscreened"]
    if not todo:
        return cache

    # The library defaults to a 600s timeout with 2 internal retries, so one
    # hung connection can stall a worker for half an hour with no log output.
    # Fail fast and let the explicit backoff loop below own all retrying.
    client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1",
                         timeout=180.0, max_retries=0)
    sem = asyncio.Semaphore(concurrency)
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    done = 0
    started = time.time()

    ctx_first = {w: (contexts.get(w) or [""])[0] for w in todo}

    async def run_batch(batch):
        nonlocal done
        async with sem:
            parsed = {}
            # deepseek-v4-flash is a reasoning model: most of the completion
            # budget goes to hidden reasoning tokens, which scale with batch
            # size. Too small a budget yields an EMPTY content string rather
            # than an error, so the budget is generous and empties are retried.
            for attempt in range(5):
                try:
                    resp = await client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": SCREEN_SYSTEM_PROMPT},
                            {"role": "user", "content": build_screen_prompt(batch, ctx_first)},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.0,
                        max_tokens=16000,
                    )
                    content = resp.choices[0].message.content or ""
                    if content.strip():
                        parsed = parse_screen_response(content, batch)
                        if parsed:
                            break
                except Exception as e:
                    if attempt == 4:
                        print(f"    screen batch error: {str(e)[:120]}")
                # Exponential backoff: 429s need real headroom, not 1.5s.
                await asyncio.sleep(min(2 ** attempt * 2, 30))

        # Record results as soon as this batch lands. Collecting them per-chunk
        # instead would idle most of the pool while the slowest reasoning call
        # in each chunk finishes.
        for w, (cat, lemma) in parsed.items():
            cache[w] = [cat, lemma]
        # Words the model silently dropped stay "unscreened" so a later run
        # retries them rather than treating them as settled.
        for w in batch:
            cache.setdefault(w, ["unscreened", None])

        done += 1
        if checkpoint and done % 40 == 0:
            checkpoint(cache)
        if done % 10 == 0 or done == len(batches):
            rate = done / max(time.time() - started, 1e-6)
            eta = (len(batches) - done) / rate / 60 if rate else 0
            print(f"    screened {done}/{len(batches)} batches  (ETA {eta:.1f} min)")
        return parsed

    # One continuous pool over every batch: the semaphore alone caps in-flight
    # requests, so a slow call never blocks the others. Results and checkpoints
    # are written from inside run_batch as each one completes.
    await asyncio.gather(*(run_batch(b) for b in batches))
    if checkpoint:
        checkpoint(cache)

    return cache


# ---------------------------------------------------------------------------
# Entry generation
# ---------------------------------------------------------------------------

async def generate_entries(items, contexts, api_key, model, concurrency=8,
                           cache=None, dry_run=False, checkpoint=None):
    """Generate dictionary entries. `items` are dicts with lemma/word/pos_hint."""
    from openai import AsyncOpenAI

    cache = cache if cache is not None else {}
    todo = [it for it in items if it["target"] not in cache]
    if not todo:
        return cache

    # The library defaults to a 600s timeout with 2 internal retries, so one
    # hung connection can stall a worker for half an hour with no log output.
    # Fail fast and let the explicit backoff loop below own all retrying.
    client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1",
                         timeout=180.0, max_retries=0)
    sem = asyncio.Semaphore(concurrency)
    done = 0
    started = time.time()

    async def one(item):
        nonlocal done
        target = item["target"]
        pos = pos_hint_to_pos(item.get("pos_hint", ""))
        ctx = contexts.get(item["word"]) or []

        user = f"Word: {target}"
        if pos:
            user += f"\nPOS hint: {pos}"
        if ctx:
            user += "\nContext from Czech YouTube subtitles:\n" + \
                    "\n".join(f"- {s[:200]}" for s in ctx[:2])

        if dry_run:
            return target, {"_dry_run": True, "prompt": user}

        async with sem:
            # Reasoning tokens consume most of the completion budget, so a
            # small max_tokens truncates the JSON mid-string instead of
            # failing loudly. Budget generously and retry.
            entry = {"_error": "no attempt"}
            for attempt in range(5):
                try:
                    resp = await client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            *FEW_SHOT,
                            {"role": "user", "content": user},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.3,
                        max_tokens=8000,
                    )
                    content = resp.choices[0].message.content or ""
                    if content.strip():
                        entry = json.loads(content)
                        break
                    entry = {"_error": "empty response"}
                except Exception as e:
                    entry = {"_error": str(e)[:200]}
                await asyncio.sleep(min(2 ** attempt * 2, 30))

        # Never cache failures -- a cached error would be replayed forever
        # instead of being retried on the next run.
        if "_error" not in entry:
            cache[target] = entry

        done += 1
        if checkpoint and done % 100 == 0:
            checkpoint(cache)
        if done % 100 == 0 or done == len(todo):
            rate = done / max(time.time() - started, 1e-6)
            eta = (len(todo) - done) / rate / 60 if rate else 0
            print(f"    generated {done}/{len(todo)}  (ETA {eta:.1f} min)")
        return target, entry

    # Continuous pool, same reasoning as screening: no chunk barrier, so the
    # slowest generation never idles the rest of the workers.
    await asyncio.gather(*(one(it) for it in todo))
    if checkpoint:
        checkpoint(cache)
    return cache


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Fold YouTube corpus into the dictionary")
    ap.add_argument("--min-channels", type=int, default=3,
                    help="Word must appear in >= this many distinct channels (default 3)")
    ap.add_argument("--min-freq", type=int, default=5,
                    help="Minimum total corpus frequency (default 5)")
    ap.add_argument("--cap-threshold", type=float, default=0.80,
                    help="Capitalization ratio above which a form is a proper noun")
    ap.add_argument("--screen", action="store_true", help="Run LLM screening")
    ap.add_argument("--generate", action="store_true", help="Generate + save entries")
    ap.add_argument("--report", action="store_true", help="Coverage report only")
    ap.add_argument("--dry-run", action="store_true", help="No API writes")
    ap.add_argument("--max-words", type=int, default=0, help="Cap words processed (0=all)")
    ap.add_argument("--batch-size", type=int, default=20,
                    help="Screening batch size (small: reasoning tokens scale with it)")
    ap.add_argument("--concurrency", type=int, default=8, help="Concurrent API calls")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--db", default=str(DB_PATH),
                    help="Database path (point at a copy to rehearse a run)")
    args = ap.parse_args()

    print("Loading word statistics...")
    stats = load_word_stats()
    word_freq = Counter({w: s["freq"] for w, s in stats.items()})
    total_tokens = sum(word_freq.values())
    print(f"  Unique forms: {len(stats):,}   Total tokens: {total_tokens:,}")

    print("Lemmatizing with Majka...")
    lemma_map, pos_map = lemmatize_with_majka(list(word_freq.keys()))

    print("Checking dictionary coverage...")
    conn = sqlite3.connect(args.db)
    missing = find_missing(conn, word_freq, lemma_map, pos_map)
    missing_tokens = sum(m["freq"] for m in missing)

    baseline_form_cov = (len(stats) - len(missing)) / len(stats) * 100 if stats else 0
    baseline_token_cov = (total_tokens - missing_tokens) / total_tokens * 100 if total_tokens else 0

    print(f"\n{'='*64}")
    print("BASELINE COVERAGE (all tokens, including ASR noise)")
    print(f"{'='*64}")
    print(f"  Forms covered:  {len(stats)-len(missing):,}/{len(stats):,} ({baseline_form_cov:.2f}%)")
    print(f"  Tokens covered: {total_tokens-missing_tokens:,}/{total_tokens:,} ({baseline_token_cov:.2f}%)")
    print(f"  Missing forms:  {len(missing):,}")

    # ---- Layer 1: structural plausibility -------------------------------
    rejected = Counter()
    stage1 = []
    for m in missing:
        reason = structural_verdict(m["word"])
        if reason:
            rejected[reason] += 1
            continue
        stage1.append(m)

    # ---- Layer 2: cross-channel dispersion + frequency -------------------
    stage2 = []
    for m in stage1:
        s = stats[m["word"]]
        if s["channels"] < args.min_channels:
            rejected["low_dispersion"] += 1
            continue
        if s["freq"] < args.min_freq:
            rejected["low_frequency"] += 1
            continue
        stage2.append(m)

    # ---- Layer 3: capitalization / proper nouns --------------------------
    stage3 = []
    for m in stage2:
        s = stats[m["word"]]
        if looks_like_proper_noun(m["word"], s["freq"], s["caps"], args.cap_threshold):
            rejected["proper_noun_caps"] += 1
            continue
        stage3.append(m)

    print(f"\n{'='*64}")
    print("FILTERING")
    print(f"{'='*64}")
    print(f"  Missing forms:              {len(missing):,}")
    for reason, n in rejected.most_common():
        print(f"    rejected {reason:24} {n:8,}")
    print(f"  Candidates after filtering: {len(stage3):,}")

    if args.max_words > 0:
        stage3 = sorted(stage3, key=lambda m: -stats[m["word"]]["freq"])[: args.max_words]
        print(f"  Capped to {len(stage3):,} highest-frequency candidates")

    candidate_words = [m["word"] for m in stage3]

    print("\nBuilding context index (single corpus pass)...")
    contexts = build_context_index(CORPUS_FILE, candidate_words)
    with_ctx = sum(1 for w in candidate_words if contexts.get(w))
    print(f"  Contexts found for {with_ctx:,}/{len(candidate_words):,} candidates")

    report = {
        "baseline_form_coverage_pct": round(baseline_form_cov, 3),
        "baseline_token_coverage_pct": round(baseline_token_cov, 3),
        "unique_forms": len(stats),
        "total_tokens": total_tokens,
        "missing_forms": len(missing),
        "missing_tokens": missing_tokens,
        "rejected": dict(rejected),
        "candidates": len(stage3),
    }

    if args.report or (not args.screen and not args.generate):
        print("\nTop 40 candidates:")
        for i, m in enumerate(sorted(stage3, key=lambda x: -stats[x["word"]]["freq"])[:40], 1):
            s = stats[m["word"]]
            print(f"  {i:3}. {m['word']:24} freq={s['freq']:6,} ch={s['channels']:3} "
                  f"lemma={m['lemma']}")
        save_cache(REPORT_FILE, report)
        conn.close()
        return

    api_key = get_api_key()
    if not api_key and not args.dry_run:
        print("\nERROR: no DeepSeek key (DEEPSEEK_API_KEY or deepseek_key.txt)")
        conn.close()
        sys.exit(1)

    # ---- Layer 4: LLM screening -----------------------------------------
    screen_cache = load_cache(SCREEN_CACHE)
    if args.screen:
        print(f"\n{'='*64}")
        print(f"LLM SCREENING ({len(candidate_words):,} candidates)")
        print(f"{'='*64}")
        already = sum(1 for w in candidate_words if w in screen_cache)
        print(f"  Cached: {already:,}  To screen: {len(candidate_words)-already:,}")
        if not args.dry_run:
            screen_cache = asyncio.run(screen_words(
                candidate_words, contexts, api_key, args.model,
                batch_size=args.batch_size, concurrency=args.concurrency,
                cache=screen_cache,
                checkpoint=lambda c: save_cache(SCREEN_CACHE, c)))
            save_cache(SCREEN_CACHE, screen_cache)

        verdicts = Counter(screen_cache.get(w, ["unscreened", None])[0] for w in candidate_words)
        print("\n  Screening verdicts:")
        for cat, n in verdicts.most_common():
            pct = n / len(candidate_words) * 100 if candidate_words else 0
            print(f"    {cat:14} {n:8,} ({pct:5.1f}%)")
        report["screen_verdicts"] = dict(verdicts)

    confirmed = [m for m in stage3
                 if screen_cache.get(m["word"], ["unscreened", None])[0] == "word"]
    print(f"\n  Confirmed real Czech words: {len(confirmed):,}")

    # ---- Layer 5: generation ---------------------------------------------
    if args.generate:
        # Prefer the screener's standard headword; fall back to Majka's lemma.
        form_to_lemma = {}
        for m in confirmed:
            screened_lemma = screen_cache.get(m["word"], [None, None])[1]
            lemma = (screened_lemma or m["lemma"] or m["word"]).lower()
            form_to_lemma[m["word"]] = lemma

        all_lemmas = set(form_to_lemma.values())
        have = lemmas_present(conn, all_lemmas)
        need = all_lemmas - have

        print(f"\n{'='*64}")
        print("HEADWORD ROUTING")
        print(f"{'='*64}")
        print(f"  Confirmed surface forms:        {len(form_to_lemma):,}")
        print(f"  Distinct headwords:             {len(all_lemmas):,}")
        print(f"  Already in dictionary (link only, free): {len(have):,}")
        print(f"  Need generation:                {len(need):,}")

        # One generation item per missing headword, carrying a representative
        # surface form so context lookup still works.
        rep_form = {}
        for form, lemma in form_to_lemma.items():
            if lemma in need and lemma not in rep_form:
                rep_form[lemma] = form
        pos_by_form = {m["word"]: m.get("pos_hint", "") for m in confirmed}
        gen_items = [{"target": lemma, "word": form,
                      "pos_hint": pos_by_form.get(form, "")}
                     for lemma, form in rep_form.items()]

        print(f"\n{'='*64}")
        print(f"GENERATING ENTRIES ({len(gen_items):,} headwords)")
        print(f"{'='*64}")

        gen_cache = load_cache(GEN_CACHE)
        already = sum(1 for it in gen_items if it["target"] in gen_cache)
        print(f"  Cached: {already:,}  To generate: {len(gen_items)-already:,}")
        est = (len(gen_items) - already) * 0.0002
        print(f"  Estimated cost: ${est:.2f}")

        gen_cache = asyncio.run(generate_entries(
            gen_items, contexts, api_key, args.model,
            concurrency=args.concurrency, cache=gen_cache, dry_run=args.dry_run,
            checkpoint=None if args.dry_run else (lambda c: save_cache(GEN_CACHE, c))))
        if not args.dry_run:
            save_cache(GEN_CACHE, gen_cache)

        relevant = {it["target"]: gen_cache[it["target"]]
                    for it in gen_items if it["target"] in gen_cache}
        ok = sum(1 for e in relevant.values() if "_error" not in e and "_dry_run" not in e)
        # Failures are not cached, so they show up as absent from `relevant`.
        err = len(gen_items) - len(relevant)
        print(f"\n  Generated OK: {ok:,}   Failed (will retry next run): {err:,}")

        if not args.dry_run:
            saved, save_errors = save_entries(conn, relevant)
            print(f"  Saved entries: {saved:,}   Save errors: {save_errors}")

            # Link every confirmed surface form to its headword, including the
            # forms whose headword was already present and cost nothing.
            pos_lookup = {}
            for target, entry in relevant.items():
                if isinstance(entry, dict) and "pos" in entry:
                    pos_lookup[target] = entry["pos"]
            linked = save_inflections(conn, form_to_lemma, pos_lookup)
            print(f"  Linked surface forms: {linked:,}")
            report["entries_saved"] = saved
            report["forms_linked"] = linked

            c = conn.cursor()
            c.execute(
                "INSERT INTO processed_texts (filename, total_lemmas, missing_lemmas,"
                " new_entries_added) VALUES (?, ?, ?, ?)",
                ("youtube_corpus/ (YouTube subtitle corpus)", len(stats),
                 len(missing), saved))
            conn.commit()

            # ---- Post-generation coverage -------------------------------
            print("\nRe-checking coverage after insert...")
            missing_after = find_missing(conn, word_freq, lemma_map, pos_map)
            missing_after_tokens = sum(m["freq"] for m in missing_after)
            form_cov = (len(stats) - len(missing_after)) / len(stats) * 100
            token_cov = (total_tokens - missing_after_tokens) / total_tokens * 100

            # "Real word" coverage: ignore tokens the screener judged to be
            # names, foreign words, or ASR garbage -- they are not words a
            # dictionary should ever contain.
            noise_tokens = 0
            for m in missing_after:
                cat = screen_cache.get(m["word"], [None, None])[0]
                if cat in ("proper_noun", "foreign", "asr_error"):
                    noise_tokens += m["freq"]
                elif cat is None and structural_verdict(m["word"]):
                    noise_tokens += m["freq"]
            real_total = total_tokens - noise_tokens
            real_cov = ((real_total - (missing_after_tokens - noise_tokens))
                        / real_total * 100) if real_total else 0

            print(f"\n{'='*64}")
            print("FINAL COVERAGE")
            print(f"{'='*64}")
            print(f"  Form coverage:   {baseline_form_cov:6.2f}%  ->  {form_cov:6.2f}%")
            print(f"  Token coverage:  {baseline_token_cov:6.2f}%  ->  {token_cov:6.2f}%")
            print(f"  Real-word token coverage (excluding names/foreign/ASR noise):"
                  f"  {real_cov:6.2f}%")
            report.update({
                "final_form_coverage_pct": round(form_cov, 3),
                "final_token_coverage_pct": round(token_cov, 3),
                "real_word_token_coverage_pct": round(real_cov, 3),
                "noise_tokens_excluded": noise_tokens,
            })

    save_cache(REPORT_FILE, report)
    conn.close()
    print(f"\nReport written to {REPORT_FILE}")


if __name__ == "__main__":
    main()

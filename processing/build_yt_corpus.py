#!/usr/bin/env python3
"""
Parse downloaded YouTube subtitles into a corpus + word statistics table.

The output that matters is `word_stats.tsv`, which records for every word form:
  freq        total occurrences across the whole corpus
  channels    number of DISTINCT channels the word appeared in
  videos      number of distinct videos the word appeared in

Channel dispersion is the single most powerful noise filter available here.
Auto-generated captions produce plenty of garbage -- ASR mishearings, invented
words, channel-specific in-jokes, proper nouns, and foreign words mangled into
Czech-looking shapes. Nearly all of that garbage is *local*: it shows up in one
or two channels. A genuine Czech word used by real speakers shows up across many
unrelated channels. Requiring a word to appear in N distinct channels discards
most noise while keeping real vocabulary, and no amount of frequency-only
filtering can do the same job (a single video repeating a made-up name 40 times
looks "frequent" but is still garbage).

Usage:
    python3 build_yt_corpus.py
    python3 build_yt_corpus.py --min-freq 1
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = PROJECT_ROOT / "youtube_corpus"
SUBS_DIR = CORPUS_DIR / "subs"
TEXT_DIR = CORPUS_DIR / "text"
CORPUS_FILE = CORPUS_DIR / "corpus.txt"
STATS_FILE = CORPUS_DIR / "word_stats.tsv"
SUMMARY_FILE = CORPUS_DIR / "corpus_summary.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from process_text import tokenize  # noqa: E402

# Non-speech caption markers, in Czech and English.
BRACKET_MARKER = re.compile(r"\[[^\]]{0,40}\]")
SPEAKER_ARROWS = re.compile(r"&gt;&gt;|>>")
MULTISPACE = re.compile(r"\s+")


def parse_json3(path):
    """Extract plain text from a YouTube json3 caption file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""

    parts = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs)
        text = text.replace("\n", " ").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def parse_vtt(path):
    """Extract text from WebVTT, collapsing YouTube's rolling-window duplication.

    YouTube auto-caption VTT repeats the previous cue's text in each new cue, so
    a naive line-scrape inflates token counts roughly 3x. Emitting a line only
    when it differs from the previously emitted one restores the true text.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""

    out, prev = [], None
    for line in raw.split("\n"):
        s = line.strip()
        if not s or "-->" in s:
            continue
        if s.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if s.isdigit():
            continue
        s = re.sub(r"<[^>]+>", "", s).strip()
        if s and s != prev:
            out.append(s)
            prev = s
    return " ".join(out)


def clean_text(text):
    text = SPEAKER_ARROWS.sub(" ", text)
    text = BRACKET_MARKER.sub(" ", text)
    text = text.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    text = MULTISPACE.sub(" ", text)
    return text.strip()


def main():
    ap = argparse.ArgumentParser(description="Build YouTube corpus + word stats")
    ap.add_argument("--min-freq", type=int, default=1,
                    help="Drop forms below this total frequency from word_stats.tsv")
    args = ap.parse_args()

    if not SUBS_DIR.exists():
        print(f"ERROR: {SUBS_DIR} not found. Run download_yt_subs.py first.")
        sys.exit(1)

    channel_dirs = sorted([d for d in SUBS_DIR.iterdir() if d.is_dir()])
    print(f"Channels with downloaded subs: {len(channel_dirs)}")

    TEXT_DIR.mkdir(parents=True, exist_ok=True)

    total_freq = Counter()
    channel_sets = defaultdict(set)   # word -> {channel slug}
    video_counts = Counter()          # word -> distinct videos
    cap_counts = Counter()            # word -> occurrences written capitalized

    n_videos = 0
    n_empty = 0
    total_tokens = 0
    per_channel_stats = {}

    with open(CORPUS_FILE, "w", encoding="utf-8") as corpus_out:
        for ci, cdir in enumerate(channel_dirs, 1):
            slug = cdir.name
            files = sorted(list(cdir.glob("*.json3")) + list(cdir.glob("*.vtt")))
            channel_texts = []
            ch_tokens = 0

            for path in files:
                text = parse_json3(path) if path.suffix == ".json3" else parse_vtt(path)
                text = clean_text(text)
                if not text:
                    n_empty += 1
                    continue

                n_videos += 1
                channel_texts.append(text)

                raw_tokens = [t for t in tokenize(text) if len(t) > 1]
                words = [t.lower() for t in raw_tokens]
                ch_tokens += len(words)
                total_freq.update(words)
                # Capitalization ratio feeds proper-noun detection downstream.
                cap_counts.update(t.lower() for t in raw_tokens if t[0].isupper())
                unique_here = set(words)
                for w in unique_here:
                    channel_sets[w].add(slug)
                    video_counts[w] += 1

            if channel_texts:
                joined = "\n".join(channel_texts)
                (TEXT_DIR / f"{slug}.txt").write_text(joined, encoding="utf-8")
                corpus_out.write(joined + "\n")

            total_tokens += ch_tokens
            per_channel_stats[slug] = {"videos": len(files), "tokens": ch_tokens}
            print(f"  [{ci}/{len(channel_dirs)}] {slug[:46]:46} "
                  f"{len(files):4} files  {ch_tokens:8,} tokens")

    print(f"\nWriting {STATS_FILE} ...")
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        f.write("word\tfreq\tchannels\tvideos\tcaps\n")
        for word, freq in total_freq.most_common():
            if freq < args.min_freq:
                continue
            f.write(f"{word}\t{freq}\t{len(channel_sets[word])}"
                    f"\t{video_counts[word]}\t{cap_counts[word]}\n")

    summary = {
        "channels": len(channel_dirs),
        "videos_with_text": n_videos,
        "videos_empty": n_empty,
        "total_tokens": total_tokens,
        "unique_forms": len(total_freq),
        "corpus_chars": CORPUS_FILE.stat().st_size,
        "per_channel": per_channel_stats,
    }
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # Dispersion histogram -- shows how much of the vocabulary is channel-local noise.
    disp = Counter(min(len(channel_sets[w]), 10) for w in total_freq)

    print(f"\n{'='*60}")
    print("CORPUS SUMMARY")
    print(f"{'='*60}")
    print(f"  Channels:            {len(channel_dirs):,}")
    print(f"  Videos with text:    {n_videos:,}")
    print(f"  Total tokens:        {total_tokens:,}")
    print(f"  Unique word forms:   {len(total_freq):,}")
    print(f"  Corpus size:         {CORPUS_FILE.stat().st_size/1_000_000:.1f} MB")
    print(f"\n  Forms by channel dispersion:")
    for k in sorted(disp):
        label = f"{k}+" if k == 10 else str(k)
        pct = disp[k] / len(total_freq) * 100 if total_freq else 0
        print(f"    in {label:>3} channels: {disp[k]:8,} forms ({pct:5.1f}%)")
    print(f"\n  Wrote: {STATS_FILE}")
    print(f"  Wrote: {CORPUS_FILE}")


if __name__ == "__main__":
    main()

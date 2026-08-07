#!/usr/bin/env python3
"""
Download Czech YouTube subtitles for the dictionary corpus.

One yt-dlp process per channel (not per video) -- yt-dlp enumerates the
channel's uploads, filters, and downloads subtitles in a single pass, which is
roughly an order of magnitude faster than the per-video --dump-json approach.

Two decisions matter for corpus quality:

1. `--match-filters "language ~= '(?i)^cs'"` keeps only videos whose ORIGINAL
   audio language is Czech. YouTube auto-translates captions into ~150
   languages, so a `cs` caption track on an English video is machine-translated
   text, not Czech speech. Without this filter a channel like HONEST GUIDE
   (which posts in both languages) would poison the corpus with translationese.

2. `--sub-format json3` avoids YouTube's rolling-window VTT, where each cue
   repeats the previous line. Parsing that VTT naively inflates word counts
   ~2.9x and wrecks the frequency statistics the noise filter depends on.

Usage:
    python3 download_yt_subs.py                      # all channels
    python3 download_yt_subs.py --limit 5            # first 5 channels (test)
    python3 download_yt_subs.py --workers 6
    python3 download_yt_subs.py --max-videos 100
    python3 download_yt_subs.py --retry-failed       # re-attempt failed channels
    python3 download_yt_subs.py --status             # summarize progress only
"""

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CHANNELS_FILE = PROJECT_ROOT / "data" / "youtube_channels.tsv"
CORPUS_DIR = PROJECT_ROOT / "youtube_corpus"
SUBS_DIR = CORPUS_DIR / "subs"
PROGRESS_FILE = CORPUS_DIR / "download_progress.json"
LOG_DIR = CORPUS_DIR / "logs"

MAX_VIDEOS_PER_CHANNEL = 100

# Only videos whose original audio language is Czech.
LANGUAGE_FILTER = "language ~= '(?i)^cs'"

_progress_lock = threading.Lock()
_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def slugify(name):
    name = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE).strip()
    name = re.sub(r"[\s]+", "_", name)
    return name[:80] or "channel"


def load_progress():
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"WARNING: {PROGRESS_FILE} is corrupt; starting fresh")
    return {}


def save_progress(progress):
    with _progress_lock:
        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PROGRESS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(progress, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PROGRESS_FILE)


def read_channels():
    if not CHANNELS_FILE.exists():
        log(f"ERROR: {CHANNELS_FILE} not found")
        sys.exit(1)

    channels = []
    with open(CHANNELS_FILE, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        for line in f:
            if not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            row = dict(zip(header, parts))
            if row.get("url", "").startswith("http"):
                channels.append(row)
    return channels


def count_subs(channel_dir):
    if not channel_dir.exists():
        return 0
    return len(list(channel_dir.glob("*.json3"))) + len(list(channel_dir.glob("*.vtt")))


def download_channel(channel, max_videos, timeout):
    """Run yt-dlp for one channel. Returns a result dict."""
    title = channel["title"]
    url = channel["url"]
    slug = slugify(title)
    channel_dir = SUBS_DIR / slug
    channel_dir.mkdir(parents=True, exist_ok=True)

    before = count_subs(channel_dir)

    cmd = [
        "yt-dlp",
        "--no-update",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "cs",
        "--sub-format", "json3/vtt/best",
        "--match-filters", LANGUAGE_FILTER,
        "--playlist-end", str(max_videos),
        "--no-overwrites",
        "--ignore-errors",
        "--no-warnings",
        "--retries", "3",
        "--fragment-retries", "3",
        "--socket-timeout", "30",
        "--sleep-requests", "0.75",
        "-o", str(channel_dir / "%(id)s.%(ext)s"),
        url,
    ]

    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        stderr, rc = proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        stderr, rc = f"TIMEOUT after {timeout}s", -1
    except FileNotFoundError:
        log("ERROR: yt-dlp not found. Install with: pip install yt-dlp")
        sys.exit(1)

    elapsed = time.time() - started
    after = count_subs(channel_dir)
    new = after - before

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if stderr.strip():
        (LOG_DIR / f"{slug}.log").write_text(stderr[-40000:], encoding="utf-8")

    # yt-dlp exits non-zero when --ignore-errors skipped something, so file
    # count is the real success signal, not the return code.
    if after > 0:
        status = "ok"
    elif rc == -1:
        status = "timeout"
    else:
        status = "no_czech_videos" if "does not pass filter" in stderr else "failed"

    return {
        "title": title,
        "url": url,
        "slug": slug,
        "status": status,
        "subs_total": after,
        "subs_new": new,
        "elapsed_sec": round(elapsed, 1),
        "returncode": rc,
        "error_tail": "" if status == "ok" else stderr.strip()[-500:],
    }


def print_status(progress, channels):
    done = [c for c in channels if progress.get(c["url"], {}).get("status") == "ok"]
    total_subs = sum(progress.get(c["url"], {}).get("subs_total", 0) for c in channels)
    by_status = {}
    for c in channels:
        st = progress.get(c["url"], {}).get("status", "pending")
        by_status[st] = by_status.get(st, 0) + 1

    print(f"\n{'='*66}")
    print("DOWNLOAD STATUS")
    print(f"{'='*66}")
    for st, n in sorted(by_status.items(), key=lambda x: -x[1]):
        print(f"  {st:20} {n:4} channels")
    print(f"  {'-'*40}")
    print(f"  {'subtitle files':20} {total_subs:4}")
    print(f"  {'channels with subs':20} {len(done):4}/{len(channels)}")

    failures = [(c["title"], progress[c["url"]])
                for c in channels
                if progress.get(c["url"], {}).get("status") not in (None, "ok")]
    if failures:
        print(f"\n  Problem channels ({len(failures)}):")
        for t, info in failures[:25]:
            print(f"    [{info['status']}] {t}")
            if info.get("error_tail"):
                first = info["error_tail"].split("\n")[0][:110]
                print(f"        {first}")


def main():
    ap = argparse.ArgumentParser(description="Download Czech YouTube subtitles")
    ap.add_argument("--limit", type=int, default=0, help="Only first N channels (0=all)")
    ap.add_argument("--workers", type=int, default=4, help="Parallel channels (default 4)")
    ap.add_argument("--max-videos", type=int, default=MAX_VIDEOS_PER_CHANNEL,
                    help=f"Videos per channel (default {MAX_VIDEOS_PER_CHANNEL})")
    ap.add_argument("--timeout", type=int, default=5400, help="Per-channel timeout seconds")
    ap.add_argument("--retry-failed", action="store_true", help="Re-attempt non-ok channels")
    ap.add_argument("--force", action="store_true", help="Re-attempt every channel")
    ap.add_argument("--status", action="store_true", help="Print progress and exit")
    args = ap.parse_args()

    SUBS_DIR.mkdir(parents=True, exist_ok=True)
    channels = read_channels()
    progress = load_progress()

    if args.limit:
        channels = channels[: args.limit]

    if args.status:
        print_status(progress, channels)
        return

    def needs_work(ch):
        if args.force:
            return True
        rec = progress.get(ch["url"])
        if rec is None:
            return True
        if rec.get("status") == "ok":
            return False
        # Retry transient failures; leave genuinely Czech-free channels alone.
        if args.retry_failed:
            return rec.get("status") != "no_czech_videos"
        return False

    todo = [c for c in channels if needs_work(c)]

    log(f"Channels: {len(channels)} total, {len(todo)} to process")
    log(f"Workers: {args.workers}, max {args.max_videos} videos/channel")
    log(f"Output: {SUBS_DIR}")
    if not todo:
        log("Nothing to do.")
        print_status(progress, channels)
        return

    completed = 0
    started_all = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_channel, c, args.max_videos, args.timeout): c
                   for c in todo}
        for fut in as_completed(futures):
            ch = futures[fut]
            completed += 1
            try:
                result = fut.result()
            except Exception as e:
                result = {"title": ch["title"], "url": ch["url"], "slug": slugify(ch["title"]),
                          "status": "error", "subs_total": 0, "subs_new": 0,
                          "elapsed_sec": 0, "returncode": -2, "error_tail": str(e)[:500]}

            progress[ch["url"]] = result
            save_progress(progress)

            mark = "OK " if result["status"] == "ok" else "!! "
            log(f"[{completed}/{len(todo)}] {mark}{result['title'][:44]:44} "
                f"{result['subs_total']:3} subs (+{result['subs_new']:3}) "
                f"{result['elapsed_sec']:6.0f}s  {result['status']}")

    log(f"\nTotal wall time: {(time.time()-started_all)/60:.1f} min")
    print_status(progress, channels)


if __name__ == "__main__":
    main()

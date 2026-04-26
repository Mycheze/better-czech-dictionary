#!/usr/bin/env python3
"""
Czech YouTube subtitle scraper for dictionary coverage.

Scrapes Czech subtitles from YouTube channels/playlists, saves them,
and processes them through the dictionary pipeline to find and fill gaps.

Usage:
    # Scrape subs from a channel/playlist (subs only, no video download)
    python3 scrape_czech_subs.py scrape <youtube_url> [--limit N]

    # Scrape from a list of URLs in a file
    python3 scrape_czech_subs.py scrape-list <url_file> [--limit N]

    # Process all scraped subs for dictionary coverage
    python3 scrape_czech_subs.py process [--generate] [--min-freq N]

    # Full pipeline: scrape then process
    python3 scrape_czech_subs.py full <youtube_url> [--generate] [--limit N]

    # List known Czech YouTube channels
    python3 scrape_czech_subs.py channels

Examples:
    python3 scrape_czech_subs.py scrape "https://www.youtube.com/@SlowCzech"
    python3 scrape_czech_subs.py scrape "https://www.youtube.com/playlist?list=PLxxx" --limit 50
    python3 scrape_czech_subs.py process --generate --min-freq 3
"""

import subprocess
import sys
import os
import json
import argparse
import re
from pathlib import Path
from datetime import datetime

SUBS_DIR = Path(__file__).parent / "1k_sub_files"
SCRAPED_DIR = Path(__file__).parent / "scraped_subs"
CHANNELS_FILE = Path(__file__).parent / "czech_channels.json"

# Default list of Czech YouTube channels good for language learning
DEFAULT_CHANNELS = [
    {
        "name": "Slow Czech",
        "url": "https://www.youtube.com/@SlowCzech",
        "description": "Czech for intermediate learners, slow speech",
    },
    {
        "name": "Čeština s Michalem",
        "url": "https://www.youtube.com/@CestinaSMichalem",
        "description": "Czech podcast for learners",
    },
    {
        "name": "Czech with Eliška",
        "url": "https://www.youtube.com/@CzechWithEliska",
        "description": "Czech lessons and cultural content",
    },
    {
        "name": "Easy Czech",
        "url": "https://www.youtube.com/@EasyCzech",
        "description": "Easy Czech street interviews",
    },
    {
        "name": "Honest Guide",
        "url": "https://www.youtube.com/@HONESTGUIDE",
        "description": "Prague travel guides in Czech",
    },
    {
        "name": "Kluci z prahy",
        "url": "https://www.youtube.com/@Klucizprahy",
        "description": "Native Czech YouTube content",
    },
]


def ensure_dirs():
    SCRAPED_DIR.mkdir(exist_ok=True)


def scrape_subs(url, limit=None, output_dir=None):
    """Scrape Czech subtitles from a YouTube URL using yt-dlp."""
    if output_dir is None:
        output_dir = SCRAPED_DIR

    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    cmd = [
        "yt-dlp",
        "--skip-download",           # Don't download video
        "--write-subs",              # Write subtitle files
        "--write-auto-subs",         # Include auto-generated subs
        "--sub-langs", "cs",         # Czech only
        "--sub-format", "vtt",       # VTT format
        "--convert-subs", "vtt",     # Convert to VTT
        "-o", str(output_dir / "%(title)s.%(ext)s"),
        "--no-overwrites",           # Don't re-download existing
        "--ignore-errors",           # Skip failed videos
        "--sleep-interval", "2",     # Be polite to YouTube
        "--max-sleep-interval", "5",
    ]

    if limit:
        cmd.extend(["--playlist-end", str(limit)])

    cmd.append(url)

    print(f"Scraping Czech subtitles from: {url}")
    if limit:
        print(f"  Limit: {limit} videos")
    print(f"  Output: {output_dir}")
    print(f"  Command: {' '.join(cmd[:8])}...")
    print()

    result = subprocess.run(cmd, capture_output=False)

    # Count downloaded subtitle files
    vtt_files = list(output_dir.glob("*.cs.vtt"))
    print(f"\nDownloaded {len(vtt_files)} Czech subtitle files")

    return vtt_files


def vtt_to_text(vtt_path):
    """Convert a VTT subtitle file to clean text."""
    with open(vtt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')
    text_lines = []

    for line in lines:
        stripped = line.strip()
        # Skip VTT headers
        if stripped.startswith('WEBVTT') or stripped.startswith('\ufeffWEBVTT'):
            continue
        if stripped.startswith('Kind:') or stripped.startswith('Language:'):
            continue
        if stripped.startswith('NOTE'):
            continue
        # Skip timestamps
        if '-->' in stripped:
            continue
        # Skip sequence numbers (pure digits)
        if stripped.isdigit():
            continue
        # Skip empty lines
        if not stripped:
            continue
        # Remove VTT formatting tags like <c> </c> <00:00:00.000>
        cleaned = re.sub(r'<[^>]+>', '', stripped)
        cleaned = cleaned.strip()
        if cleaned:
            text_lines.append(cleaned)

    return '\n'.join(text_lines)


def convert_all_vtt(source_dir=None):
    """Convert all VTT files in scraped_subs to text files in 1k_sub_files."""
    if source_dir is None:
        source_dir = SCRAPED_DIR

    source_dir = Path(source_dir)
    vtt_files = list(source_dir.glob("*.cs.vtt"))

    if not vtt_files:
        print(f"No .cs.vtt files found in {source_dir}")
        return 0

    converted = 0
    for vtt_path in vtt_files:
        # Output filename: strip .cs.vtt, add .cs.txt
        stem = vtt_path.name
        if stem.endswith('.cs.vtt'):
            stem = stem[:-7]
        out_name = f"{stem}.cs.txt"
        out_path = SUBS_DIR / out_name

        if out_path.exists():
            continue

        text = vtt_to_text(vtt_path)
        if text.strip():
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(text)
            converted += 1

    print(f"Converted {converted} new subtitle files to {SUBS_DIR}")
    return converted


def process_subs(generate=False, min_freq=2, dry_run=False, batch_size=10, max_words=0):
    """Process all subtitle files through the dictionary pipeline."""
    # Import process_subs module
    cmd = [sys.executable, str(Path(__file__).parent / "process_subs.py")]

    if generate:
        cmd.append("--generate")
    if dry_run:
        cmd.append("--dry-run")

    cmd.extend(["--min-freq", str(min_freq)])
    cmd.extend(["--batch-size", str(batch_size)])

    if max_words > 0:
        cmd.extend(["--max-words", str(max_words)])

    # Pass through API key
    env = os.environ.copy()
    key_file = Path(__file__).parent / "deepseek_key.txt"
    if key_file.exists() and "DEEPSEEK_API_KEY" not in env:
        env["DEEPSEEK_API_KEY"] = key_file.read_text().strip()

    subprocess.run(cmd, env=env)


def list_channels():
    """List known Czech YouTube channels."""
    # Load custom channels if file exists
    channels = DEFAULT_CHANNELS.copy()
    if CHANNELS_FILE.exists():
        with open(CHANNELS_FILE, 'r') as f:
            custom = json.load(f)
            channels.extend(custom)

    print(f"\nKnown Czech YouTube channels ({len(channels)}):")
    print(f"{'='*70}")
    for i, ch in enumerate(channels, 1):
        print(f"  {i}. {ch['name']}")
        print(f"     {ch['url']}")
        print(f"     {ch['description']}")
        print()

    print("Add custom channels to czech_channels.json")
    print("Or scrape any URL directly with: python3 scrape_czech_subs.py scrape <url>")


def main():
    parser = argparse.ArgumentParser(description="Czech YouTube subtitle scraper")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # scrape command
    scrape_parser = subparsers.add_parser("scrape", help="Scrape subtitles from a YouTube URL")
    scrape_parser.add_argument("url", help="YouTube channel, playlist, or video URL")
    scrape_parser.add_argument("--limit", type=int, help="Max videos to scrape")

    # scrape-list command
    list_parser = subparsers.add_parser("scrape-list", help="Scrape from a list of URLs")
    list_parser.add_argument("url_file", help="File with one URL per line")
    list_parser.add_argument("--limit", type=int, help="Max videos per URL")

    # process command
    proc_parser = subparsers.add_parser("process", help="Process scraped subs for dictionary coverage")
    proc_parser.add_argument("--generate", action="store_true", help="Generate missing definitions")
    proc_parser.add_argument("--dry-run", action="store_true", help="Dry run")
    proc_parser.add_argument("--min-freq", type=int, default=2, help="Min word frequency")
    proc_parser.add_argument("--batch-size", type=int, default=10, help="API batch size")
    proc_parser.add_argument("--max-words", type=int, default=0, help="Max words to generate")

    # full command
    full_parser = subparsers.add_parser("full", help="Scrape + process in one step")
    full_parser.add_argument("url", help="YouTube URL")
    full_parser.add_argument("--limit", type=int, help="Max videos")
    full_parser.add_argument("--generate", action="store_true", help="Generate missing definitions")
    full_parser.add_argument("--min-freq", type=int, default=2, help="Min word frequency")
    full_parser.add_argument("--batch-size", type=int, default=10, help="API batch size")

    # scrape-all command
    subparsers.add_parser("scrape-all", help="Scrape all known Czech channels")

    # channels command
    subparsers.add_parser("channels", help="List known Czech YouTube channels")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    ensure_dirs()

    if args.command == "scrape":
        vtt_files = scrape_subs(args.url, limit=args.limit)
        if vtt_files:
            converted = convert_all_vtt()
            print(f"\nSubtitles ready for processing. Run:")
            print(f"  python3 scrape_czech_subs.py process --generate")

    elif args.command == "scrape-list":
        with open(args.url_file, 'r') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        total_files = []
        for url in urls:
            files = scrape_subs(url, limit=args.limit)
            total_files.extend(files)
        if total_files:
            convert_all_vtt()
            print(f"\nTotal: {len(total_files)} subtitle files scraped")

    elif args.command == "process":
        process_subs(
            generate=args.generate,
            min_freq=args.min_freq,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            max_words=args.max_words,
        )

    elif args.command == "full":
        vtt_files = scrape_subs(args.url, limit=args.limit)
        if vtt_files:
            convert_all_vtt()
        process_subs(
            generate=args.generate,
            min_freq=args.min_freq,
        )

    elif args.command == "scrape-all":
        channels = DEFAULT_CHANNELS.copy()
        if CHANNELS_FILE.exists():
            with open(CHANNELS_FILE, 'r') as f:
                channels.extend(json.load(f))

        for ch in channels:
            print(f"\n{'='*60}")
            print(f"Scraping: {ch['name']}")
            print(f"{'='*60}")
            scrape_subs(ch["url"])

        convert_all_vtt()
        print(f"\nAll channels scraped. Run:")
        print(f"  python3 scrape_czech_subs.py process --generate")

    elif args.command == "channels":
        list_channels()


if __name__ == "__main__":
    main()

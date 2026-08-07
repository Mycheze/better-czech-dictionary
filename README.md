# BetterOfflineDict

A comprehensive Czech-English offline dictionary with full morphological coverage. Look up **any inflected Czech word form** -- not just the base dictionary form.

Built for Czech learners who read on Kindle, KOReader, or in the browser with Yomitan.

## Why this exists

Most Czech-English dictionaries only let you look up the base form (lemma) of a word. But Czech has rich morphology -- a single noun can have 14 forms, a verb dozens. If you're reading a Czech book and encounter "městech", you need to know it's a form of "město" (city). This dictionary handles that automatically.

## What's inside

- **141,000+ lemmas** with English definitions
- **16.8 million inflection mappings** (via MorfFlex CZ 2.1) so every word form resolves to its definition
- **2.4 million unique lookupable forms**
- **41,000+ Czech-English sentence pairs** from Tatoeba

Data sources:
| Source | Entries | What it provides |
|--------|---------|-----------------|
| English Wiktionary (via kaikki.org) | 69,770 | Rich definitions, examples, etymology, pronunciation |
| Svobodne Slovniky | 67,999 | Broad vocabulary coverage |
| DeepSeek-generated | 11,407 | Gap-filling for words found in real Czech texts |
| MorfFlex CZ 2.1 | 16.8M mappings | Inflected form -> lemma resolution |
| Tatoeba | 41,869 pairs | Czech-English sentence examples |

## Download

Go to the [**Releases**](../../releases) page and download the format you need:

### StarDict (for KOReader / GoldenDict)
Download the `.zip` file containing `Czech-English.dict.dz`, `Czech-English.idx`, and `Czech-English.ifo`. Copy the three files to your device's StarDict dictionary directory:
- **KOReader**: `/koreader/data/dict/` or use the dictionary manager in Settings
- **GoldenDict**: Add the directory in Edit > Dictionaries

### Kindle
Download the MOBI file and transfer it to your Kindle. The dictionary uses Kindle's native `<idx:infl>` markup, so all inflected forms resolve to the correct entry without needing separate entries per form.

To sideload: connect your Kindle via USB and copy the `.mobi` file to the `documents/dictionaries/` folder. Then select it as your default Czech dictionary in Settings > Language & Dictionaries.

> **Note**: Building MOBI from source requires [kindlegen](https://archive.org/details/kindlegen-2.9) (Amazon's Kindle dictionary compiler).

### Yomitan (browser extension)
Download the `.zip` file and import it directly in Yomitan (Settings > Dictionaries > Import). Works in any browser where Yomitan is installed -- hover over Czech words on any webpage to see definitions.

## Build from source

If you want to build the dictionary yourself (e.g., to customize it or add your own texts):

### Prerequisites

- Python 3.10+
- `dictzip` for StarDict compression: `sudo apt install dictzip`
- `ebook-convert` from [Calibre](https://calibre-ebook.com/) (only needed for processing ebooks)
- [Majka](https://nlp.fi.muni.cz/ma/) morphological analyzer binary + `majka.w-lt` dictionary (for text processing)

### 1. Download data sources

```bash
# Kaikki.org Wiktionary Czech data
mkdir -p data/kaikki
wget -O data/kaikki/kaikki-czech-en.jsonl.gz "https://kaikki.org/dictionary/Czech/kaikki.org-dictionary-Czech.jsonl.gz"
gunzip -k data/kaikki/kaikki-czech-en.jsonl.gz

# Svobodne Slovniky English-Czech
mkdir -p data/svobodne
wget -O data/svobodne/en-cs-source.tar.gz "https://dl.cihar.com/slovnik/stardict-english-czech-latest-source.tar.gz"
tar xzf data/svobodne/en-cs-source.tar.gz -C data/svobodne/

# Tatoeba Czech-English sentence pairs
mkdir -p data/tatoeba
wget -O data/tatoeba/ces-eng.zip "https://www.manythings.org/anki/ces-eng.zip"
unzip -o data/tatoeba/ces-eng.zip -d data/tatoeba/

# MorfFlex CZ 2.1 (requires accepting CC BY-NC-SA 4.0 license)
# Download manually from: https://lindat.mff.cuni.cz/repository/xmlui/handle/11234/1-5833
mkdir -p data/morfflex
# Place czech-morfflex-2.1.tsv.xz in data/morfflex/
```

### 2. Build the database

```bash
python3 build_dictionary.py          # Import all sources
python3 build_dictionary.py --stats  # Check statistics
```

### 3. Export

```bash
# StarDict (KOReader / GoldenDict)
python3 exporters/export_stardict.py
# Output: output/stardict/Czech-English.{dict.dz,idx,ifo}

# Kindle MOBI (requires kindlegen)
python3 exporters/export_kindle.py
# Output: output/kindle/Czech-English.mobi

# Yomitan (browser extension)
python3 exporters/export_yomitan.py
# Output: output/yomitan/Czech-English.zip
```

### 4. (Optional) Expand coverage with your own texts

The pipeline can process Czech texts to find words missing from the dictionary and generate definitions using the DeepSeek API:

```bash
pip install openai
export DEEPSEEK_API_KEY="your-key-here"

# Process a single text file
python3 processing/process_text.py my_book.txt --generate

# Process all ebooks in books/ directory
python3 processing/process_books.py --generate --reimport-morfflex

# Re-export after adding new entries
python3 exporters/export_stardict.py
```

### 5. (Optional) Expand coverage with the YouTube corpus

To cover casual internet Czech, the pipeline can harvest subtitles from a wide
set of Czech YouTube channels and fold the vocabulary into the dictionary. The
curated channel list lives in `data/youtube_channels.tsv` (100 channels balanced
across vlogs, gaming, travel, news, food, tech, history and learner content);
downloaded subtitles land in `youtube_corpus/`, which is gitignored.

```bash
pip install yt-dlp openai

# 1. Download subtitles (100 videos/channel, resumable, ~1.5 h with 5 workers)
python3 processing/download_yt_subs.py --workers 5
python3 processing/download_yt_subs.py --status        # progress summary
python3 processing/download_yt_subs.py --retry-failed  # re-attempt failures

# 2. Parse subtitles into a corpus + word statistics
python3 processing/build_yt_corpus.py

# 3. Inspect the coverage gap without spending anything
python3 processing/process_yt_corpus.py --report

# 4a. Screen and generate through the DeepSeek API
python3 processing/process_yt_corpus.py --screen --generate

# 4b. ...or hand the same work to a swarm of Claude agents
python3 processing/dump_yt_candidates.py          # candidates + contexts
python3 processing/yt_swarm.py shard-screen       # -> shards to classify
#   run one agent per shard, writing shard_NNN.out.json
python3 processing/yt_swarm.py merge-screen
python3 processing/yt_swarm.py shard-gen          # -> shards to write entries for
#   run one agent per shard (see shards/gen/INSTRUCTIONS.md)
python3 processing/yt_swarm.py merge-gen
python3 processing/apply_yt_results.py            # write to DB + measure
```

Both paths write to the same two caches (`screen_results.json`,
`generated_entries.json`), so they are interchangeable and resumable. The swarm
path exists because reasoning-model APIs charge for hidden reasoning tokens,
which dominated the cost of screening ~20k words.

Two details make this work on auto-generated captions:

- **Only genuinely Czech videos are used.** YouTube auto-translates captions
  into ~150 languages, so a `cs` caption track on an English video is machine
  translation, not Czech speech. The downloader passes
  `--match-filters "language ~= '^cs'"` so only videos whose original audio is
  Czech are kept.
- **Captions are read as `json3`, not VTT.** YouTube's auto-caption VTT repeats
  the previous line in every cue; scraping it naively inflates word counts about
  3x and corrupts the frequency statistics the filters depend on.

Noise is removed in layers, cheapest first (see `processing/yt_word_filter.py`):
Czech orthography rules, then **cross-channel dispersion** (a real word appears
across many unrelated channels; ASR garbage and in-jokes stay local), then a
capitalization test for proper nouns, then a DeepSeek screening pass that sorts
survivors into real word / proper noun / foreign / ASR error.

Confirmed colloquial forms are linked to their standard headword rather than
given their own entry, so `cejtím` resolves to `cítit` and `tohodle` to `tenhle`
without duplicating the dictionary.

## Project structure

```
build_dictionary.py          - Import data sources into SQLite database
exporters/
    export_stardict.py       - Export to StarDict format (KOReader / GoldenDict)
    export_kindle.py         - Export to Kindle MOBI format
    export_yomitan.py        - Export to Yomitan format (browser extension)
processing/
    process_text.py          - Process Czech text, find gaps, generate definitions
    process_books.py         - Batch-process ebook files
    process_subs.py          - Batch-process subtitle files
    scrape_czech_subs.py     - Scrape Czech YouTube subtitles
    download_yt_subs.py      - Download subs for the curated channel list
    build_yt_corpus.py       - Parse subs into corpus + word/dispersion stats
    yt_word_filter.py        - Orthography filters + screening prompt
    dump_yt_candidates.py    - Dump candidate words with contexts
    process_yt_corpus.py     - Screen + generate via the DeepSeek API
    yt_swarm.py              - Shard/merge the same work for agent workers
    apply_yt_results.py      - Write screened/generated results to the DB
tools/
    test_dictionary.py       - Look up words and test coverage
    build_channel_list.py    - Rebuild data/youtube_channels.tsv from the sheet
    parse_cs_txt.py          - Czech text tokenizer / frequency analyzer
    known_analyzer.py        - Vocabulary coverage analyzer
    sentence_coverage.py     - Sentence-level coverage analysis
docs/
    ARCHITECTURE.md          - Technical design and schema
    PIPELINE.md              - Text processing pipeline design
    PLAN.md                  - Implementation plan
    RESEARCH.md              - Data source research
```

## How it works

1. **Import** definitions from Wiktionary (via kaikki.org) and Svobodne Slovniky into a SQLite database
2. **Import** 16.8M inflection mappings from MorfFlex CZ 2.1, linking every word form to its lemma
3. **Gap-fill** by processing real Czech texts (books, subtitles) to find missing words, then generating definitions via LLM
4. **Export** to multiple formats (StarDict, Kindle MOBI, Yomitan), each using the optimal strategy for that platform

The cross-reference resolver handles Wiktionary entries like "inflection of X" by inlining the actual definition from X, so you get real definitions instead of grammatical labels.

## License

This project combines data under different licenses:

- **Code**: MIT
- **Wiktionary data** (via kaikki.org): CC BY-SA 3.0
- **Svobodne Slovniky**: GPL
- **MorfFlex CZ 2.1**: CC BY-NC-SA 4.0 (non-commercial use only)
- **Tatoeba**: CC BY 2.0
- **DeepSeek-generated entries**: No additional restrictions

Because MorfFlex is CC BY-NC-SA 4.0, **pre-built dictionary files that include MorfFlex inflection data are for non-commercial use only**.

## Credits

- [MorfFlex CZ](https://ufal.mff.cuni.cz/morfflex) by the Institute of Formal and Applied Linguistics, Charles University
- [Kaikki.org](https://kaikki.org/) for structured Wiktionary data extracts
- [Svobodne Slovniky](https://www.slovnik.cz/) by Michal Cihar
- [Tatoeba](https://tatoeba.org/) community for sentence pairs
- [Majka](https://nlp.fi.muni.cz/ma/) morphological analyzer by NLP Centre, Masaryk University
- [DeepSeek](https://www.deepseek.com/) for LLM-generated gap-filling definitions

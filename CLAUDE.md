# BetterOfflineDict

A comprehensive Czech-English offline dictionary with full morphological coverage. Any inflected Czech word form can be looked up.

## Core Scripts

- `build_dictionary.py` - Imports all data sources into SQLite (`dictionary.db`)
- `exporters/export_stardict.py` - Exports database to StarDict format (`output/stardict/`)
- `exporters/export_kindle.py` - Exports database to Kindle MOBI format (`output/kindle/`)
- `exporters/export_yomitan.py` - Exports database to Yomitan ZIP format (`output/yomitan/`)
- `processing/process_text.py` - Processes Czech text files, finds missing words, generates definitions via DeepSeek
- `processing/process_books.py` - Batch-processes ebooks in `books/` directory
- `processing/process_subs.py` - Batch-processes subtitle files in `1k_sub_files/`
- `processing/download_yt_subs.py` - Downloads Czech subs from the channels in `data/youtube_channels.tsv`
- `processing/build_yt_corpus.py` - Parses subs into `youtube_corpus/` text + word statistics
- `processing/yt_word_filter.py` - Noise filters + LLM screening prompt for auto-caption words
- `processing/process_yt_corpus.py` - Folds the YouTube corpus into the database
- `tools/test_dictionary.py` - Word lookup and coverage testing

## Key Design Decisions

- **Canonical format**: SQLite database (`dictionary.db`) as single source of truth
- **Morphology**: MorfFlex CZ 2.1 for inflection mapping (16.8M form-lemma pairs)
- **Runtime lemmatizer**: Majka binary (`./majka -f ./majka.w-lt`)
- **LLM gap-filling**: DeepSeek V3 via OpenAI-compatible API
- **DeepSeek models are reasoning models**: hidden reasoning tokens consume most
  of `max_tokens`. Too small a budget returns EMPTY content or truncated JSON
  rather than an error -- budget generously (8k-16k) and retry on empty.
- **YouTube corpus**: auto-captions are read as `json3` (YouTube's VTT repeats
  each line, inflating counts ~3x) and restricted to videos whose original audio
  is Czech (`--match-filters "language ~= '^cs'"`), since a `cs` track on a
  foreign video is machine translation. Word noise is filtered by cross-channel
  dispersion before any LLM call. Colloquial forms are linked to standard
  headwords via the `inflections` table instead of getting duplicate entries.
- **Export targets**: StarDict (KOReader/GoldenDict), Kindle MOBI (via kindlegen), Yomitan ZIP (browser extension)
- **License**: Mixed -- MorfFlex is CC BY-NC-SA 4.0, Wiktionary is CC BY-SA, code is MIT

## Data Sources (not in repo, see README for download instructions)

- `data/kaikki/kaikki-czech-en.jsonl` - Wiktionary Czech entries
- `data/svobodne/stardict-english-czech-*/en-cs.txt` - Svobodne Slovniky
- `data/tatoeba/ces.txt` - Tatoeba sentence pairs
- `data/morfflex/czech-morfflex-2.1.tsv.xz` - MorfFlex inflections

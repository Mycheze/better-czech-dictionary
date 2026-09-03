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
- `processing/process_show_subs.py` - Folds a directory of TV-show subtitles into the database; exports candidates to / imports verdicts+entries from JSON so screening/generation can run on any model (used with Claude agent swarms)
- `tools/test_dictionary.py` - Word lookup + coverage testing; also reports a
  *quality* figure (share of tokens landing on a non-thin entry) beside raw coverage
- `tools/build_phrase_links.py` - Builds the `phrase_links` table: component word ->
  multi-word entry, so looking up `plot` offers `živý plot` and `vrátit` offers `vrátit se`
- `tools/merge_svobodne_senses.py` - Recovers Svobodné translations the original
  import discarded, into `entry_json.also_en`
- `tools/audit_entries.py` - Finds entries that exist but are too thin, ranked by
  corpus frequency; emits a swarm-ready candidate shard
- `tools/audit_swarm.py` - Shards those candidates for agent workers and merges the
  results back; import them with `process_show_subs.py --refresh`, never plain import
- `tools/probe_stardict.py` - Asserts specific lookups in the BUILT StarDict index
- `processing/prompts/*.md` - Worker briefs for the screening/generation agent swarm; `yt_swarm.py` copies them into each shard directory as `INSTRUCTIONS.md`
- `docs/PORTING.md` - What to swap when rebuilding this for another language (worked build prompts in `docs/porting/`)

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
- **Coverage != quality**: the historical gap check (`process_text.find_missing`,
  `test_dictionary.test_coverage`) is an *existence* test, and `INSERT OR IGNORE`
  on `UNIQUE(lemma,pos)` means nothing could ever improve an entry that already
  existed. So a word could sit at "diminutive of houba" forever while coverage
  read 99.99%. `tools/audit_entries.py` flags thin entries and the `--refresh`
  flag on the importers merges better senses into an existing row. `--refresh`
  treats regenerated senses as AUTHORITATIVE (replacing near-duplicates and
  dropping glosses the audit found wrong), and retags a Svobodne `unknown`-POS
  row rather than inserting a second row beside it -- both are needed or a
  re-audit doubles every entry and preserves the errors it was meant to fix.
- **Multi-word entries need back-links**: ~33k headwords contain a space, but a
  reader taps one word, so they were unreachable. `phrase_links` maps each
  component lemma (via Majka) to the phrases it appears in; reflexive `se`/`si`
  entries are ranked first because that is where a wrong answer is most likely.
- **Redirects show every sense**: inflected-form lookups render all senses
  (capped), not `senses[0]`. Rendering only the first sense is what hid the
  "sponge" reading of `houbička` behind "mushroom".
- **Name-only entries are demoted**: ~9.9k `proper_noun` "a male surname" entries
  are collapsed to a `(surname)` tag, sorted last, and excluded from MorfFlex
  paradigm expansion so they stop shadowing ordinary words.
- **MorfFlex must be RE-RUN after adding entries.** `import_morfflex` only expands
  lemmas that exist when it runs, and it runs last in `build_dictionary.py`, so
  anything added afterwards gets no paradigm and is findable only in its exact
  dictionary form. That is what happened to the 2026-08 show-subs batch: those
  entries averaged 2-4 inflected forms where Wiktionary nouns average 32 and
  adjectives 571. It takes `target_lemmas`, so the fix is incremental:
  pass the lemmas with no `source='morfflex'` rows (minus the name-only ones).
- **`inflections` needs its UNIQUE index.** Without `idx_inflections_unique`,
  every `INSERT OR IGNORE INTO inflections` silently degrades to a plain INSERT.
  42% of the table (7.2M of 16.9M rows) was exact duplicates before it existed.
- **Svobodne stores non-headwords as headwords.** Its Czech column sometimes
  holds an English gloss or a whole definition, which lands in `entries.lemma`
  ("24 hours a day, 7 days a week", "= subprime mortgage", a 293-char DRM
  explanation that broke StarDict's 255-byte key limit). `junk_headwords()` in
  `export_stardict.py` drops these at export time, using Majka to test whether a
  multi-word Svobodne headword is actually made of Czech words. It is deliberately
  NOT done at import: structural rules alone (length, word count, punctuation)
  also flag real Czech idioms like "jako by mu z oka vypadl", so the rows stay in
  the DB as the archive and the exporters are the gate.
- **Export targets**: StarDict (KOReader/GoldenDict), Kindle MOBI (via kindlegen), Yomitan ZIP (browser extension)
- **License**: Mixed -- MorfFlex is CC BY-NC-SA 4.0, Wiktionary is CC BY-SA, code is MIT

## Data Sources (not in repo, see README for download instructions)

- `data/kaikki/kaikki-czech-en.jsonl` - Wiktionary Czech entries
- `data/svobodne/stardict-english-czech-*/en-cs.txt` - Svobodne Slovniky
- `data/tatoeba/ces.txt` - Tatoeba sentence pairs
- `data/morfflex/czech-morfflex-2.1.tsv.xz` - MorfFlex inflections

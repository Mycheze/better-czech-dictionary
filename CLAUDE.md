# BetterOfflineDict

A comprehensive Czech-English offline dictionary with full morphological coverage. Any inflected Czech word form can be looked up.

## Core Scripts

- `build_dictionary.py` - Imports all data sources into SQLite (`dictionary.db`)
- `export_stardict.py` - Exports database to StarDict format (`output/stardict/`)
- `process_text.py` - Processes Czech text files, finds missing words, generates definitions via DeepSeek
- `process_books.py` - Batch-processes ebooks in `books/` directory
- `process_subs.py` - Batch-processes subtitle files in `1k_sub_files/`
- `test_dictionary.py` - Word lookup and coverage testing

## Key Design Decisions

- **Canonical format**: SQLite database (`dictionary.db`) as single source of truth
- **Morphology**: MorfFlex CZ 2.1 for inflection mapping (16.8M form-lemma pairs)
- **Runtime lemmatizer**: Majka binary (`./majka -f ./majka.w-lt`)
- **LLM gap-filling**: DeepSeek V3 via OpenAI-compatible API
- **Export targets**: StarDict (KOReader/GoldenDict), Kindle MOBI (planned), Yomitan (planned)
- **License**: Mixed -- MorfFlex is CC BY-NC-SA 4.0, Wiktionary is CC BY-SA, code is MIT

## Data Sources (not in repo, see README for download instructions)

- `data/kaikki/kaikki-czech-en.jsonl` - Wiktionary Czech entries
- `data/svobodne/stardict-english-czech-*/en-cs.txt` - Svobodne Slovniky
- `data/tatoeba/ces.txt` - Tatoeba sentence pairs
- `data/morfflex/czech-morfflex-2.1.tsv.xz` - MorfFlex inflections

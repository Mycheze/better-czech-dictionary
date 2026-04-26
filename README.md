# BetterOfflineDict

A comprehensive Czech-English offline dictionary with full morphological coverage. Look up **any inflected Czech word form** -- not just the base dictionary form.

Built for Czech learners who read on Kindle, KOReader, or other devices with StarDict/GoldenDict support.

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

### Kindle (coming soon)
MOBI format export is planned.

### Yomitan (coming soon)
Yomitan ZIP format export is planned.

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

### 3. Export to StarDict

```bash
python3 export_stardict.py
# Output: output/stardict/Czech-English.{dict.dz,idx,ifo}
```

### 4. (Optional) Expand coverage with your own texts

The pipeline can process Czech texts to find words missing from the dictionary and generate definitions using the DeepSeek API:

```bash
pip install openai
export DEEPSEEK_API_KEY="your-key-here"

# Process a single text file
python3 process_text.py my_book.txt --generate

# Process all ebooks in books/ directory
python3 process_books.py --generate --reimport-morfflex

# Re-export after adding new entries
python3 export_stardict.py
```

## Project structure

```
build_dictionary.py    - Import data sources into SQLite database
export_stardict.py     - Export database to StarDict format
process_text.py        - Process Czech text, find gaps, generate definitions
process_books.py       - Batch-process ebook files
process_subs.py        - Batch-process subtitle files
scrape_czech_subs.py   - Scrape Czech YouTube subtitles
test_dictionary.py     - Look up words and test coverage
parse_cs_txt.py        - Czech text tokenizer / frequency analyzer
known_analyzer.py      - Vocabulary coverage analyzer
sentence_coverage.py   - Sentence-level coverage analysis
```

## How it works

1. **Import** definitions from Wiktionary (via kaikki.org) and Svobodne Slovniky into a SQLite database
2. **Import** 16.8M inflection mappings from MorfFlex CZ 2.1, linking every word form to its lemma
3. **Gap-fill** by processing real Czech texts (books, subtitles) to find missing words, then generating definitions via LLM
4. **Export** to StarDict with one entry per inflected form, so any word you tap on resolves to a definition

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

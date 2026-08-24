# German-English Offline Dictionary — Build Prompt

## What you're building

A comprehensive German-English offline dictionary with full morphological coverage, so any inflected German word form can be looked up — not just base dictionary forms. This is for a language learner who reads German books on Kindle and KOReader.

This project is modeled on an existing Czech-English dictionary I built. I'll describe the full architecture, data sources, design decisions, and all the code patterns so you can recreate it for German. The project folder is fresh — build everything from scratch.

## Project structure to create

```
build_dictionary.py          - Import data sources into SQLite database
exporters/
    export_stardict.py       - Export to StarDict format (KOReader / GoldenDict)
    export_kindle.py         - Export to Kindle MOBI format
    export_yomitan.py        - Export to Yomitan format (browser extension)
processing/
    process_text.py          - Process German text, find gaps, generate definitions
    process_books.py         - Batch-process ebook files
tools/
    test_dictionary.py       - Look up words and test coverage
requirements.txt             - openai>=1.0 (for DeepSeek API)
README.md                    - Project documentation
CLAUDE.md                    - Project instructions for Claude Code
```

## Pre-existing files in this project folder

- `deepseek_key.txt` — Contains the DeepSeek API key (read this file for the key; also support DEEPSEEK_API_KEY env var)
- `books/` — Contains German .epub files to process

---

## PHASE 1: Research German data sources and morphology tools

Before writing any code, research what's available for German and make a concrete plan. Here's what I know and what needs investigation:

### Data source 1: Kaikki.org Wiktionary (CONFIRMED — use this)

Kaikki.org provides structured Wiktionary dumps for every language. The German file is at:
```
https://kaikki.org/dictionary/German/kaikki.org-dictionary-German.jsonl.gz
```

The JSONL format is the same as Czech — one JSON object per line with fields: `word`, `pos`, `senses` (with `glosses`, `tags`, `examples`, `synonyms`), `forms` (inflected forms with tags), `sounds` (IPA), `etymology_text`, `head_templates` (with gender in `args.g`).

German-specific parsing needed:
- **Gender**: German has 3 genders (m, f, n) — no animate/inanimate distinction like Czech. Look for `"m"`, `"f"`, `"n"` in head_templates args.
- **No aspect**: German verbs don't have perfective/imperfective aspect. Drop the `aspect` and `aspect_pair` fields entirely from the schema.
- **Verb features**: German has separable prefixes (trennbare Verben), auxiliary verb selection (haben/sein), and strong/weak/mixed conjugation. Consider capturing `auxiliary` (haben/sein) and whether a verb is separable — both are useful for learners. Check if these are available in head_templates or tags.
- **Inflections from Wiktionary**: The `forms` field contains inflected forms. Parse these the same way as Czech: skip `table-tags` and `inflection-template` entries, collect `(form_text, tag_string)` pairs.

### Data source 2: Replacement for Svobodne Slovniky

The Czech project used Svobodne Slovniky (an English-Czech word list) as a second definition source. For German, research what's available:

- **FreeDict** (freedict.org) — has an English-German and German-English dictionary in TEI format. Check if this exists and is usable.
- **dict.cc** — large German-English dictionary but may not have a downloadable dump.
- **Other open German-English word lists** — search for what's available.

Pick the best open-source German-English word list you can find. It doesn't need to be fancy — just broad vocabulary coverage to complement Wiktionary. The Czech project's Svobodne import was simple: tab-separated `english\tgerman\tPOS\textra`, reversed to German→English.

### Data source 3: Tatoeba sentence pairs (CONFIRMED — use this)

Same as Czech but for German:
```
https://www.manythings.org/anki/deu-eng.zip
```
Format: `English\tGerman\tAttribution` (tab-separated)

### Data source 4: German morphological inflections (CRITICAL — needs research)

This is the hardest part. The Czech project used MorfFlex CZ 2.1 (16.8 million form→lemma mappings from Charles University). For German, research these options:

1. **Kaikki.org's own `forms` data** — The Wiktionary dump already contains inflected forms per entry. How many form→lemma pairs can we extract from this alone? This might provide decent coverage without any external tool.

2. **DEMorphy** (https://github.com/Wikipedia2Vec/DEMorphy or similar) — A Python library for German morphological analysis. Check if it exists and works.

3. **Zmorge / SMOR** — Open-source German morphological analyzers from Stuttgart. Check availability.

4. **spaCy German model** (`de_core_news_sm` or `de_core_news_lg`) — Can do lemmatization. Could be used both as a Majka replacement (runtime lemmatizer for text processing) AND to generate form→lemma pairs by processing a large German word list.

5. **German HunSpell dictionary** — The `de_DE` HunSpell dictionary contains morphological rules. There may be tools to expand it into a full form→lemma list.

6. **Other German morphological databases** — Search for open-source German inflection databases.

The goal is to build a comprehensive form→lemma mapping table. For Czech, MorfFlex gave us 16.8M pairs covering every noun declension, verb conjugation, adjective form, etc. German morphology is somewhat simpler (4 cases vs Czech's 7, no aspect), but still substantial (noun plurals + case forms, adjective declension with strong/weak/mixed, verb conjugations across 6 tenses + subjunctive).

**Research all of these and recommend the best approach.** It's fine to combine multiple sources (e.g., Wiktionary forms + spaCy lemmatization).

### Runtime lemmatizer (replacement for Majka)

The Czech project used `majka` (a binary morphological analyzer from Masaryk University) as a runtime lemmatizer in the text processing pipeline. For German, we need a replacement. The most practical option is probably **spaCy** with a German model:

```python
import spacy
nlp = spacy.load("de_core_news_sm")
doc = nlp("Die Kinder spielten im Garten")
for token in doc:
    print(token.text, token.lemma_, token.pos_)
```

Research whether spaCy's German lemmatizer is good enough, or if there's a better option.

---

## PHASE 2: Architecture (replicate from Czech project)

### Database schema

```sql
CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma TEXT NOT NULL,
    pos TEXT NOT NULL,
    gender TEXT,                -- 'm', 'f', 'n' for nouns (German 3-gender system)
    frequency TEXT,
    entry_json TEXT NOT NULL,   -- Full entry as JSON (see format below)
    source TEXT NOT NULL,       -- 'wiktionary', 'freedict', 'deepseek', etc.
    confidence REAL DEFAULT 1.0,
    validated BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE inflections (
    form TEXT NOT NULL,         -- inflected form (lowercase)
    lemma TEXT NOT NULL,        -- dictionary form (lowercase)
    pos TEXT NOT NULL,
    tag TEXT,                   -- morphological tag from source
    source TEXT DEFAULT 'wiktionary'
);

CREATE TABLE processed_texts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_lemmas INTEGER,
    missing_lemmas INTEGER,
    new_entries_added INTEGER
);

CREATE TABLE word_encounters (
    lemma TEXT NOT NULL,
    text_id INTEGER NOT NULL,
    frequency INTEGER NOT NULL,
    FOREIGN KEY (text_id) REFERENCES processed_texts(id),
    PRIMARY KEY (lemma, text_id)
);

CREATE TABLE review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    reason TEXT,
    status TEXT DEFAULT 'pending',
    reviewer_notes TEXT,
    FOREIGN KEY (entry_id) REFERENCES entries(id)
);

CREATE TABLE tatoeba_sentences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    german TEXT NOT NULL,
    english TEXT NOT NULL,
    attribution TEXT
);

-- Indexes
CREATE UNIQUE INDEX idx_entries_lemma_pos ON entries(lemma, pos);
CREATE INDEX idx_entries_lemma ON entries(lemma);
CREATE INDEX idx_entries_source ON entries(source);
CREATE INDEX idx_inflections_form ON inflections(form);
CREATE INDEX idx_inflections_lemma ON inflections(lemma);
CREATE INDEX idx_tatoeba_german ON tatoeba_sentences(german);
```

Note: No `aspect` or `aspect_pair` columns — German doesn't have grammatical aspect.

### Entry JSON format

```json
{
  "lemma": "spielen",
  "pos": "verb",
  "auxiliary": "haben",
  "senses": [
    {
      "definition_en": "to play",
      "register": "neutral",
      "examples": [
        {"de": "Die Kinder spielen im Garten.", "en": "The children play in the garden."}
      ]
    }
  ],
  "pronunciation": "ˈʃpiːlən",
  "etymology": "From Middle High German spilen...",
  "frequency": "common",
  "notes": "Separable prefix forms: abspielen, anspielen, ausspielen, etc."
}
```

For nouns:
```json
{
  "lemma": "Haus",
  "pos": "noun",
  "gender": "n",
  "senses": [
    {
      "definition_en": "house, home",
      "examples": [
        {"de": "Das Haus ist groß.", "en": "The house is big."}
      ]
    }
  ]
}
```

Note: examples use `"de"` and `"en"` keys (not `"cs"` and `"en"` like Czech).

### POS mapping (same as Czech, works for German)

```python
POS_MAP = {
    "noun": "noun", "verb": "verb", "adj": "adjective",
    "adv": "adverb", "pron": "pronoun", "prep": "preposition",
    "conj": "conjunction", "intj": "interjection", "num": "numeral",
    "particle": "particle", "det": "determiner", "name": "proper_noun",
    "phrase": "phrase", "prefix": "prefix", "suffix": "suffix",
    "character": "character", "proverb": "proverb",
    "prep_phrase": "prepositional_phrase",
    "contraction": "contraction", "combining_form": "combining_form",
    "interfix": "interfix", "symbol": "symbol",
}
```

---

## PHASE 3: build_dictionary.py — Import pipeline

### Kaikki/Wiktionary import

Follow the Czech pattern exactly, with these German adaptations:

**Gender parsing** — Look in `head_templates[].args.g` for `"m"`, `"f"`, `"n"`. Also check `forms` tags for `"masculine"`, `"feminine"`, `"neuter"`. Map simply:
```python
gender_map = {"m": "m", "f": "f", "n": "n"}
```

**No aspect parsing** — Skip the `parse_kaikki_aspect()` function entirely.

**Auxiliary verb detection** — For verbs, check if head_templates or tags contain "haben" or "sein" to identify the auxiliary verb used in perfect tenses. This is useful learner info.

**Inflection parsing** — Same as Czech: iterate `entry.get("forms", [])`, skip metadata entries (table-tags, inflection-template), collect `(form_text, tag_string)` pairs.

**Sense merging** — Same as Czech: group entries by `(word.lower(), pos)`, merge all senses, take first non-null gender/pronunciation/etymology.

**Cross-reference detection** — Same pattern. Wiktionary has entries like "plural of Haus" or "past tense of spielen". These need to be detected and resolved later.

### Second dictionary source import

Adapt based on whatever source you find in research. Follow the Svobodne pattern:
- Group by German word
- Determine most common POS
- Skip if already exists from Wiktionary
- Build entry JSON with senses
- Insert with lower confidence (0.9)

### Tatoeba import

Same as Czech, just different file/field names:
```python
def import_tatoeba(conn, filepath):
    # Format: English\tGerman\tAttribution
    # Insert into tatoeba_sentences(english, german, attribution)
```

### Morphological inflection import

This depends on what you find in research. The key pattern from Czech:

```python
def import_inflections(conn, filepath, target_lemmas=None):
    """Import form→lemma inflection mappings."""
    # Get known lemmas from entries table (only import forms for words we have)
    known_lemmas = set(...)
    
    # Read the inflection source file
    for form, lemma in source:
        if lemma.lower() not in known_lemmas:
            continue
        if form.lower() == lemma.lower():
            continue  # Skip identity mappings
        batch.append((form.lower(), lemma.lower(), pos, tag, source_name))
    
    # Batch insert (100k at a time for performance)
    c.executemany("INSERT OR IGNORE INTO inflections (...) VALUES (...)", batch)
```

### Stats function

Same pattern as Czech — show entry counts by source, POS distribution, inflection counts, and optionally test coverage against a known_words.txt file.

---

## PHASE 4: Exporters

### export_stardict.py

The StarDict exporter is the most complex. Key design decision: **one entry per inflected form**, because KOReader has no morphological analysis — if you look up "Häuser", there must be a StarDict entry for "Häuser" that shows the definition of "Haus".

Architecture:
1. Load all entries and inflections from DB
2. Format each entry as compact HTML
3. Build cross-reference resolution (detect "plural of X" senses, follow to real definition)
4. Create word_list: first all lemma entries, then all inflection entries (compact: just "→ Lemma: first definition")
5. Deduplicate: if a form is also a lemma, keep the lemma entry
6. Sort case-insensitively
7. Write binary .dict file (definitions as UTF-8 with null terminators)
8. Write binary .idx file (word + offset + size, big-endian uint32)
9. Write .ifo metadata file
10. Compress .dict with `dictzip` if available

**Cross-reference resolution pattern** (reuse for all exporters):

```python
_XREF_KEYWORDS = {
    "inflection", "form", "plural", "singular", "diminutive", "augmentative",
    "abbreviation", "clipping", "participle", "imperative",
    "present", "conditional", "possessive", "accusative", "genitive", "dative",
    "nominative", "vocative", "feminine", "masculine", "neuter",
    "comparative", "superlative", "alternative", "obsolete", "archaic",
    "dated", "short", "verbal", "noun", "past", "active", "passive",
    "degree", "spelling",
}

_XREF_RE = re.compile(r'^([\w\s/,-]+?)\s+of\s+(.+)$', re.IGNORECASE)
```

A sense is a cross-reference if it matches `"<grammatical terms> of <target>"`.

**HTML formatting for entries:**
```
<b>Haus</b> <i>noun</i> <small>(n)</small>
house, home
<small><i>Das Haus ist groß.</i> — The house is big.</small>
```

Gender display for German: `{"m": "m", "f": "f", "n": "n"}` — much simpler than Czech.

No aspect display (remove that section entirely).

**HTML formatting for inflection entries:**
```
<b>Haus</b>: house, home
```
Just the lemma and first definition — keep it compact.

### export_kindle.py

Kindle dictionaries use `<idx:entry>`, `<idx:orth>`, `<idx:infl>`, `<idx:iform>` markup. Key advantage over StarDict: all inflected forms can be listed inside a single entry via `<idx:iform>` tags, so no duplication.

Architecture:
1. Load entries and inflections
2. Resolve cross-references (reuse functions from export_stardict)
3. For each entry: (lemma, pos, definition_html, list_of_inflected_forms)
4. Chunk into ~10,000 entries per XHTML file
5. Write OPF manifest with language codes:
   ```xml
   <dc:language>de</dc:language>
   <DictionaryInLanguage>de</DictionaryInLanguage>
   <DictionaryOutLanguage>en</DictionaryOutLanguage>
   ```
6. Write cover page
7. Compile with `kindlegen` (user must download separately)

The export_kindle imports cross-ref resolution functions from export_stardict.

### export_yomitan.py

Yomitan uses a ZIP file containing JSON files. Architecture:
1. Load and resolve cross-references (reuse from export_stardict)
2. Build structured-content definitions (Yomitan's rich format)
3. Lemma entries get score=0, inflection entries get score=-1
4. Write index.json, tag_bank_1.json, term_bank_*.json (50k terms per bank)
5. ZIP everything with max compression

Term entry format: `[expression, reading, definitionTags, rules, score, definitions, sequence, termTags]`
- `reading` is empty for German (it's used for Japanese furigana)
- `definitionTags` gets the POS abbreviation

---

## PHASE 5: Text processing pipeline

### process_text.py

Processes a German text file to find words missing from the dictionary and optionally generates definitions via DeepSeek.

**Tokenization** — Same simple approach: split on whitespace and non-alpha characters. `char.isalpha()` handles German umlauts and ß correctly.

**Lemmatization** — Replace Majka with spaCy (or whatever you determined is best in research):
```python
def lemmatize_with_spacy(words):
    """Lemmatize words using spaCy German model."""
    import spacy
    nlp = spacy.load("de_core_news_sm")
    # Process in batches for efficiency
    lemma_map = {}
    pos_map = {}
    for doc in nlp.pipe(words, batch_size=1000):
        for token in doc:
            lemma_map[token.text.lower()] = token.lemma_.lower()
            pos_map[token.text.lower()] = token.pos_
    return lemma_map, pos_map
```

**Finding missing words** — Same batch SQL query pattern: check entries table, then inflections table, then try the lemmatized form.

**DeepSeek system prompt for German:**

```
You are a German-English lexicographer creating dictionary entries.
For each German word, produce a JSON entry with these fields:

{
  "lemma": "the dictionary form (Grundform)",
  "pos": "noun|verb|adjective|adverb|preposition|conjunction|pronoun|numeral|particle|interjection",
  "gender": "m|f|n" (nouns only, omit for other POS),
  "auxiliary": "haben|sein" (verbs only, omit if unsure),
  "senses": [
    {
      "definition_en": "English definition/translation",
      "register": "neutral|formal|informal|colloquial|vulgar|archaic|literary|technical" (omit if neutral),
      "examples": [
        {"de": "German example sentence", "en": "English translation"}
      ]
    }
  ],
  "frequency": "common|moderate|uncommon|rare" (omit if unknown),
  "notes": "usage notes, false friends, learner pitfalls" (omit if none)
}

Rules:
1. Provide at least one natural example sentence per sense.
2. For nouns, ALWAYS specify gender (m/f/n).
3. For verbs, identify the auxiliary (haben/sein) and note if the verb has separable prefixes.
4. List senses from most common to least common.
5. Mark register accurately - most words are neutral, omit the field if neutral.
6. Flag false friends with English if applicable (e.g., "Gift" means "poison", not "gift").
7. If unsure about any optional field, omit it rather than guessing.
8. Output ONLY valid JSON, no markdown or commentary.
```

**Few-shot examples for German:**

Example 1 (verb):
```json
{
  "lemma": "spielen",
  "pos": "verb",
  "auxiliary": "haben",
  "senses": [
    {
      "definition_en": "to play (a game, sport, instrument)",
      "examples": [
        {"de": "Die Kinder spielen im Garten.", "en": "The children are playing in the garden."},
        {"de": "Sie spielt Klavier.", "en": "She plays the piano."}
      ]
    },
    {
      "definition_en": "to act, to perform (in theater/film)",
      "examples": [
        {"de": "Er spielt die Hauptrolle.", "en": "He plays the lead role."}
      ]
    }
  ],
  "frequency": "common"
}
```

Example 2 (noun):
```json
{
  "lemma": "Schlüssel",
  "pos": "noun",
  "gender": "m",
  "senses": [
    {
      "definition_en": "key (for a lock)",
      "examples": [
        {"de": "Ich habe meinen Schlüssel verloren.", "en": "I lost my key."}
      ]
    },
    {
      "definition_en": "key, clue (figurative: solution to a problem)",
      "examples": [
        {"de": "Das ist der Schlüssel zum Erfolg.", "en": "That is the key to success."}
      ]
    }
  ],
  "frequency": "common"
}
```

**Validation** — Same pattern as Czech but adapted:
- Required fields: lemma, pos, senses
- Nouns must have gender
- Check that English definitions don't contain German-specific characters (umlauts in definitions are fine since many English texts discuss German words, but check for suspicious patterns)
- Confidence: 0.8 for valid entries, 0.5 for entries with issues

**German character detection** (for filtering non-German words):
```python
german_chars = set("äöüßÄÖÜ")
```
A word containing these is definitely German. But many German words are pure ASCII ("Haus", "Buch", "spielen"), so also use the lemmatizer to verify.

**DeepSeek API call:**
```python
client = AsyncOpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com/v1"
)
response = await client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[system_prompt + few_shot + user_message],
    response_format={"type": "json_object"},
    temperature=0.3,
    max_tokens=1000,
)
```

### process_books.py

Batch-processes all .epub files in `books/` directory:
1. Extract text with `ebook-convert` (from Calibre)
2. Combine all text
3. Tokenize and lemmatize
4. Find missing words
5. Filter: remove proper nouns, non-German words, low-frequency words
6. Deduplicate by lemma (so "spielte" and "gespielt" both map to "spielen" — only generate once)
7. Generate definitions via DeepSeek
8. Optionally re-import inflections for new lemmas

**is_likely_german() function:**
```python
def is_likely_german(word, pos_map):
    german_chars = set("äöüßÄÖÜ")
    skip_words = {
        "www", "com", "http", "https", "html", "url", "email", "epub",
        "isbn", "pdf", "txt", "jpg", "png", "gif", "xml", "css",
        "copyright", "ebook", "kindle", "calibre",
        "the", "you", "for", "and", "this", "that", "with", "are",
        "not", "but", "was", "have", "has", "will", "can", "your",
        "all", "from", "they", "been", "would", "there", "their",
        "what", "about", "which", "when", "one", "she", "her",
        "his", "how", "out", "its", "than", "into", "some",
    }
    w = word.lower()
    if w in skip_words:
        return False
    if len(w) <= 1:
        return False
    if any(c in german_chars for c in w):
        return True
    if w.isascii() and len(w) <= 3:
        return False
    if w.isascii():
        # Use lemmatizer POS to check if it's recognized as German
        if pos_map.get(w, '') != '':
            return True
        return False
    return True
```

### API key loading

The Czech project supports both env var and file:
```python
api_key = os.environ.get("DEEPSEEK_API_KEY", "")
if not api_key:
    key_file = PROJECT_ROOT / "deepseek_key.txt"
    if key_file.exists():
        api_key = key_file.read_text().strip()
```

---

## PHASE 6: Testing tools

### tools/test_dictionary.py

Two commands:
1. `lookup <word>` — Look up a word: check entries table, then inflections table, show full entry JSON
2. `coverage <text_file>` — Analyze word coverage of a text file: tokenize, check each word against DB, report coverage %, list top 50 missing words

---

## Implementation order

1. **Research first** — Investigate German morphology options, find best inflection source and runtime lemmatizer. Report findings before writing code.
2. **build_dictionary.py** — Core import pipeline. Get data downloaded and imported.
3. **tools/test_dictionary.py** — So we can verify lookups work.
4. **exporters/** — All three export formats.
5. **processing/process_text.py** — Text processing with DeepSeek gap-filling.
6. **processing/process_books.py** — Batch book processing.
7. **Test end-to-end** — Download data, build DB, export, verify lookups.

## Important implementation notes

- All paths use `Path(__file__).resolve().parent` patterns — no hardcoded absolute paths.
- Batch SQL operations (500 items per query for lookups, 100k for inserts) — don't do one-at-a-time queries.
- The exporters import shared functions from export_stardict.py (cross-reference resolution, HTML formatting).
- process_books.py imports shared functions from process_text.py (tokenize, lemmatize, find_missing, generate_definitions, etc.).
- Use `INSERT OR IGNORE` for inflections to handle duplicates gracefully.
- The `.dict` file format is binary: UTF-8 definitions separated by null bytes. The `.idx` file is binary: UTF-8 word + null + 4-byte big-endian offset + 4-byte big-endian size.
- Kindle OPF needs `<DictionaryInLanguage>de</DictionaryInLanguage>` and `<DictionaryOutLanguage>en</DictionaryOutLanguage>`.
- Default dict name: "German-English" everywhere.
- German nouns are capitalized in standard orthography. The DB stores lemmas lowercase for consistency, but display them with original capitalization where possible. The inflections table stores everything lowercase for lookup.

## Start with the research phase

Don't write code yet. First:
1. Download and examine the Kaikki German JSONL (just the first 100 lines) to understand the actual structure — confirm gender, forms, etc. are present as expected.
2. Research German inflection/morphology options and recommend the best approach.
3. Research a second dictionary source (FreeDict, etc.).
4. Report your findings and proposed plan. Then we'll proceed to implementation.

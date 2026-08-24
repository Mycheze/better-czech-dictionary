# Dutch-English Offline Dictionary — Build Prompt

## What you're building

A comprehensive Dutch-English offline dictionary with full morphological coverage, so any inflected Dutch word form can be looked up — not just base dictionary forms. This is for a language learner who reads Dutch books on Kindle and KOReader.

This project is modeled on an existing Czech-English dictionary I built (and a German variant). I'll describe the full architecture, data sources, design decisions, and all the code patterns so you can recreate it for Dutch. The project folder is fresh — build everything from scratch.

The motivation: existing free Dutch-English dictionaries are mediocre — coverage is patchy, morphology is poor (so inflected forms like *gelopen*, *huizen*, *mooiere* don't resolve), and example sentences are rare. With a bit of research and effort we can do substantially better by combining Wiktionary + a runtime lemmatizer + LLM gap-filling.

## Project structure to create

```
build_dictionary.py          - Import data sources into SQLite database
exporters/
    export_stardict.py       - Export to StarDict format (KOReader / GoldenDict)
    export_kindle.py         - Export to Kindle MOBI format
    export_yomitan.py        - Export to Yomitan format (browser extension)
processing/
    process_text.py          - Process Dutch text, find gaps, generate definitions
    process_books.py         - Batch-process ebook files
tools/
    test_dictionary.py       - Look up words and test coverage
requirements.txt             - openai>=1.0 (for DeepSeek API)
README.md                    - Project documentation
CLAUDE.md                    - Project instructions for Claude Code
```

## Pre-existing files in this project folder

- `deepseek_key.txt` — Contains the DeepSeek API key (read this file for the key; also support DEEPSEEK_API_KEY env var)
- `books/` — Contains Dutch .epub files to process

---

## PHASE 1: Research Dutch data sources and morphology tools

Before writing any code, research what's available for Dutch and make a concrete plan. Here's what I know and what needs investigation:

### Data source 1: Kaikki.org Wiktionary (CONFIRMED — use this)

Kaikki.org provides structured Wiktionary dumps for every language. The Dutch file is at:
```
https://kaikki.org/dictionary/Dutch/kaikki.org-dictionary-Dutch.jsonl.gz
```

The JSONL format is the same as Czech/German — one JSON object per line with fields: `word`, `pos`, `senses` (with `glosses`, `tags`, `examples`, `synonyms`), `forms` (inflected forms with tags), `sounds` (IPA), `etymology_text`, `head_templates` (with gender in `args.g`).

Dutch-specific parsing needed:
- **Gender**: This is the key Dutch quirk. Historically Dutch has 3 genders (m, f, n), but modern usage collapses masculine + feminine into **common gender** with the article **`de`**, while neuter takes **`het`**. Wiktionary still tags `m`, `f`, `n` (and sometimes `mf`). Capture **both**:
  - `gender`: `m` / `f` / `n` (raw Wiktionary value, for completeness)
  - `article`: `de` / `het` (derived: `n` → `het`, anything else → `de`). This is what learners actually need — knowing whether a noun is a *de*-word or a *het*-word.
- **No aspect**: Dutch verbs don't have perfective/imperfective aspect. Drop the `aspect` and `aspect_pair` fields entirely from the schema.
- **Verb features**: Dutch has separable verbs (*scheidbare werkwoorden*, e.g. *opbellen* → *belt op*), auxiliary verb selection (*hebben* / *zijn* for the perfect tense), and strong/weak/irregular conjugation. Capture `auxiliary` (hebben/zijn) and whether a verb is separable — both are very useful for learners. Check if these are in head_templates, tags, or derivable from the conjugation forms.
- **Diminutives**: Dutch diminutives are extremely productive (`-je`, `-tje`, `-pje`, `-kje`, `-etje`) and **always take `het`** regardless of the base noun's gender (*de man* → *het mannetje*). Wiktionary often lists the diminutive in `forms`. Worth capturing as a note.
- **Inflections from Wiktionary**: The `forms` field contains inflected forms. Parse these the same way as Czech: skip `table-tags` and `inflection-template` entries, collect `(form_text, tag_string)` pairs.

### Data source 2: Replacement for Svobodne Slovniky

The Czech project used Svobodne Slovniky (an English-Czech word list) as a second definition source. For Dutch, research what's available:

- **FreeDict** (freedict.org) — has `nld-eng` (Dutch→English) and `eng-nld` dictionaries in TEI XML format. This is the most likely candidate; check size and quality.
- **OpenTaal** (opentaal.org) — primarily a spelling/wordlist project (the official Dutch Hunspell source), not bilingual, but useful for the morphology phase (see below).
- **Other open Dutch-English word lists** — search GitHub/SourceForge for downloadable dumps. dict.cc has Dutch-English but no clean public dump.

Pick the best open-source Dutch-English word list you can find. It doesn't need to be fancy — just broad vocabulary coverage to complement Wiktionary. The Czech project's second-source import was simple: tab-separated `english\tdutch\tPOS\textra`, reversed to Dutch→English. FreeDict TEI will need an XML parser instead of tab-splitting — adapt accordingly.

### Data source 3: Tatoeba sentence pairs (CONFIRMED — use this)

Same as Czech but for Dutch:
```
https://www.manythings.org/anki/nld-eng.zip
```
Format: `English\tDutch\tAttribution` (tab-separated)

### Data source 4: Dutch morphological inflections (CRITICAL — needs research)

This is the hardest part. The Czech project used MorfFlex CZ 2.1 (16.8 million form→lemma mappings from Charles University). Dutch morphology is considerably simpler than Czech (no noun case system, only number + diminutive on nouns; adjectives mainly add `-e` plus comparative/superlative; verbs conjugate but with far fewer forms than Czech), but you still need broad form→lemma coverage. Research these options:

1. **Kaikki.org's own `forms` data** — The Wiktionary dump already contains inflected forms per entry (plurals, diminutives, verb conjugation tables, adjective degrees). For a language as morphologically light as Dutch, this alone may give very strong coverage. Measure how many form→lemma pairs come out of it before reaching for anything heavier.

2. **spaCy Dutch model** (`nl_core_news_lg` preferred, `nl_core_news_sm` minimal) — Does lemmatization and POS tagging. Use it both as the Majka replacement (runtime lemmatizer in the text pipeline) AND, by running it over a large Dutch frequency word list, to generate additional form→lemma pairs in bulk.

3. **OpenTaal Hunspell** (`nl_NL` dictionary, from opentaal.org or LibreOffice) — The `.dic`/`.aff` pair encodes Dutch inflection via affix flags. Tools like `unmunch` / `hunspell -G` / the `hunspell` Python bindings can expand it into a large surface-form list; pair each surface form with its stem to get form→lemma pairs.

4. **Frog** (https://languagemachines.github.io/frog/) — Tilburg/Radboud Dutch NLP suite (lemmatizer, POS tagger, morphological analyzer based on MBT/Timbl). High quality but heavier to install (C++/LaMachine). Consider as a quality benchmark or bulk generator if spaCy proves weak.

5. **Alpino** — University of Groningen Dutch parser with a morphological component. Powerful but heavy; likely overkill for form→lemma generation.

6. **CELEX2 (Dutch)** — gold-standard Dutch morphological lexicon but distributed via LDC and **not free** — note it as the gold standard, not a usable source here.

7. **Universal Dependencies Dutch treebanks** (UD_Dutch-Alpino, UD_Dutch-LassySmall) — free CoNLL-U with `form`/`lemma`/`upos` columns. Limited vocabulary but high precision; useful as a supplement and as a validation set.

The goal is a comprehensive form→lemma mapping table. **Research all of these and recommend the best approach.** My expectation: **Kaikki `forms` + spaCy-generated pairs over a large frequency list** will be the pragmatic backbone, with OpenTaal Hunspell expansion as a strong supplement. Confirm or revise this with actual numbers.

### Runtime lemmatizer (replacement for Majka)

The Czech project used `majka` as a runtime lemmatizer in the text processing pipeline. For Dutch, the most practical replacement is **spaCy** with a Dutch model:

```python
import spacy
nlp = spacy.load("nl_core_news_lg")
doc = nlp("De kinderen speelden in de tuin")
for token in doc:
    print(token.text, token.lemma_, token.pos_)
```

Research whether spaCy's Dutch lemmatizer is good enough (it's lookup+rule based for `nl`; check separable-verb and strong-verb handling specifically — e.g. *liep* → *lopen*, *belt op* → *opbellen*), or whether Frog gives materially better results for the runtime path.

---

## PHASE 2: Architecture (replicate from Czech project)

### Database schema

```sql
CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma TEXT NOT NULL,
    pos TEXT NOT NULL,
    gender TEXT,                -- 'm', 'f', 'n' for nouns (raw Wiktionary value)
    article TEXT,               -- 'de' or 'het' for nouns (what learners need)
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
    dutch TEXT NOT NULL,
    english TEXT NOT NULL,
    attribution TEXT
);

-- Indexes
CREATE UNIQUE INDEX idx_entries_lemma_pos ON entries(lemma, pos);
CREATE INDEX idx_entries_lemma ON entries(lemma);
CREATE INDEX idx_entries_source ON entries(source);
CREATE INDEX idx_inflections_form ON inflections(form);
CREATE INDEX idx_inflections_lemma ON inflections(lemma);
CREATE INDEX idx_tatoeba_dutch ON tatoeba_sentences(dutch);
```

Note: No `aspect` or `aspect_pair` columns — Dutch doesn't have grammatical aspect. The `article` column is new vs. the German schema and is the single most useful field for a Dutch learner.

### Entry JSON format

```json
{
  "lemma": "spelen",
  "pos": "verb",
  "auxiliary": "hebben",
  "separable": false,
  "senses": [
    {
      "definition_en": "to play",
      "register": "neutral",
      "examples": [
        {"nl": "De kinderen spelen in de tuin.", "en": "The children play in the garden."}
      ]
    }
  ],
  "pronunciation": "ˈspeːlə(n)",
  "etymology": "From Middle Dutch spelen...",
  "frequency": "common",
  "notes": "Separable compounds: meespelen, afspelen, inspelen, etc."
}
```

For nouns:
```json
{
  "lemma": "huis",
  "pos": "noun",
  "gender": "n",
  "article": "het",
  "senses": [
    {
      "definition_en": "house, home",
      "examples": [
        {"nl": "Het huis is groot.", "en": "The house is big."}
      ]
    }
  ],
  "notes": "Plural: huizen. Diminutive: huisje (het)."
}
```

Note: examples use `"nl"` and `"en"` keys (not `"cs"` / `"de"`).

### POS mapping (same as Czech/German, works for Dutch)

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

Follow the Czech pattern exactly, with these Dutch adaptations:

**Gender + article parsing** — Look in `head_templates[].args.g` for `"m"`, `"f"`, `"n"`, `"mf"`. Also check `forms` tags for `"masculine"`, `"feminine"`, `"neuter"`, `"common-gender"`. Then derive the article:
```python
def derive_article(gender):
    # Dutch: neuter -> het, everything else (m/f/mf/common) -> de
    if gender == "n":
        return "het"
    if gender in ("m", "f", "mf", "c", "common"):
        return "de"
    return None  # unknown — let DeepSeek fill later
```
Store both `gender` (raw) and `article` (derived).

**No aspect parsing** — Skip the `parse_kaikki_aspect()` function entirely.

**Auxiliary + separable detection** — For verbs, check head_templates/tags/conjugation forms for the perfect auxiliary (*hebben* vs *zijn*; motion/change-of-state verbs often take *zijn*). Detect separability: Wiktionary conjugation tables for separable verbs show split forms (e.g. *belt op*, *opgebeld*) and the lemma often contains a recognizable prefix (*op-, mee-, af-, in-, uit-, aan-, door-, over-,* …). Capture `auxiliary` and `separable` (bool) — both are high-value learner info.

**Inflection parsing** — Same as Czech: iterate `entry.get("forms", [])`, skip metadata entries (table-tags, inflection-template), collect `(form_text, tag_string)` pairs. Dutch noun forms are mostly plural + diminutive; adjective forms are inflected-`e` / comparative / superlative; verb forms are the conjugation table — all flow through the same generic collector.

**Sense merging** — Same as Czech: group entries by `(word.lower(), pos)`, merge all senses, take first non-null gender/article/pronunciation/etymology.

**Cross-reference detection** — Same pattern. Wiktionary has Dutch entries like "plural of huis", "diminutive of huis", "past participle of lopen", "inflection of mooi". These need to be detected and resolved later (see Phase 4).

### Second dictionary source import

Adapt based on whatever source you find in research (likely FreeDict `nld-eng` TEI XML). Follow the Svobodne pattern:
- Parse the source (TEI XML → iterate `<entry>` elements: `<form><orth>` = headword, `<sense><cit><quote>` = translation, `<gramGrp>` = POS/gender)
- Group by Dutch word
- Determine most common POS
- Skip if already exists from Wiktionary
- Build entry JSON with senses
- Insert with lower confidence (0.9)

### Tatoeba import

Same as Czech, just different file/field names:
```python
def import_tatoeba(conn, filepath):
    # Format: English\tDutch\tAttribution
    # Insert into tatoeba_sentences(english, dutch, attribution)
```

### Morphological inflection import

This depends on what you find in research. The key pattern from Czech:

```python
def import_inflections(conn, filepath, target_lemmas=None):
    """Import form→lemma inflection mappings."""
    # Get known lemmas from entries table (only import forms for words we have)
    known_lemmas = set(...)

    # Read the inflection source file/generator
    for form, lemma in source:
        if lemma.lower() not in known_lemmas:
            continue
        if form.lower() == lemma.lower():
            continue  # Skip identity mappings
        batch.append((form.lower(), lemma.lower(), pos, tag, source_name))

    # Batch insert (100k at a time for performance)
    c.executemany("INSERT OR IGNORE INTO inflections (...) VALUES (...)", batch)
```

If you generate pairs via spaCy/Hunspell rather than reading a single file, wrap the generator behind the same interface so the import logic is unchanged.

### Stats function

Same pattern as Czech — show entry counts by source, POS distribution, inflection counts, `de`/`het` split for nouns, and optionally test coverage against a known_words.txt file.

---

## PHASE 4: Exporters

### export_stardict.py

The StarDict exporter is the most complex. Key design decision: **one entry per inflected form**, because KOReader has no morphological analysis — if you look up "huizen", there must be a StarDict entry for "huizen" that shows the definition of "huis".

Architecture:
1. Load all entries and inflections from DB
2. Format each entry as compact HTML
3. Build cross-reference resolution (detect "plural of X" / "diminutive of X" senses, follow to real definition)
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
    "degree", "spelling", "gerund", "infinitive",
}

_XREF_RE = re.compile(r'^([\w\s/,-]+?)\s+of\s+(.+)$', re.IGNORECASE)
```

A sense is a cross-reference if it matches `"<grammatical terms> of <target>"`.

**HTML formatting for entries:**
```
<b>huis</b> <i>noun</i> <small>(het)</small>
house, home
<small><i>Het huis is groot.</i> — The house is big.</small>
```

Gender display for Dutch: show the **article** (`de` / `het`), not the raw m/f/n — that's what's pedagogically useful. Fall back to raw gender only if article is unknown.

No aspect display (remove that section entirely). For verbs, optionally show `(sep.)` and the auxiliary, e.g. `<small>(sep., zijn)</small>`.

**HTML formatting for inflection entries:**
```
<b>huis</b>: house, home
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
   <dc:language>nl</dc:language>
   <DictionaryInLanguage>nl</DictionaryInLanguage>
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
- `reading` is empty for Dutch (it's used for Japanese furigana)
- `definitionTags` gets the POS abbreviation; consider adding the article (`de`/`het`) as a term tag for nouns

---

## PHASE 5: Text processing pipeline

### process_text.py

Processes a Dutch text file to find words missing from the dictionary and optionally generates definitions via DeepSeek.

**Tokenization** — Same simple approach: split on whitespace and non-alpha characters. `char.isalpha()` handles Dutch diacritics (ë, ï, é, è, ö, ü, á, à, ç) correctly. Note the `ij` digraph is just two ASCII letters `i`+`j` — no special handling needed for lookup; sentence-initial `IJ` (e.g. *IJsland*) lowercases fine.

**Lemmatization** — Replace Majka with spaCy (or whatever you determined is best in research):
```python
def lemmatize_with_spacy(words):
    """Lemmatize words using spaCy Dutch model."""
    import spacy
    nlp = spacy.load("nl_core_news_lg")
    lemma_map = {}
    pos_map = {}
    for doc in nlp.pipe(words, batch_size=1000):
        for token in doc:
            lemma_map[token.text.lower()] = token.lemma_.lower()
            pos_map[token.text.lower()] = token.pos_
    return lemma_map, pos_map
```
Sanity-check separable verbs and strong verbs in particular (*liep*→*lopen*, *gegeven*→*geven*, *belde op*→*opbellen*); if spaCy mishandles separable verbs, fall back to the inflections table first and spaCy second.

**Finding missing words** — Same batch SQL query pattern: check entries table, then inflections table, then try the lemmatized form.

**DeepSeek system prompt for Dutch:**

```
You are a Dutch-English lexicographer creating dictionary entries.
For each Dutch word, produce a JSON entry with these fields:

{
  "lemma": "the dictionary form (grondvorm / infinitive for verbs, singular for nouns)",
  "pos": "noun|verb|adjective|adverb|preposition|conjunction|pronoun|numeral|particle|interjection",
  "gender": "m|f|n" (nouns only, omit for other POS),
  "article": "de|het" (nouns only — de for common gender, het for neuter; ALWAYS include for nouns),
  "auxiliary": "hebben|zijn" (verbs only, omit if unsure),
  "separable": true|false (verbs only, omit if unsure),
  "senses": [
    {
      "definition_en": "English definition/translation",
      "register": "neutral|formal|informal|colloquial|vulgar|archaic|literary|technical" (omit if neutral),
      "examples": [
        {"nl": "Dutch example sentence", "en": "English translation"}
      ]
    }
  ],
  "frequency": "common|moderate|uncommon|rare" (omit if unknown),
  "notes": "usage notes, false friends, plural/diminutive, learner pitfalls" (omit if none)
}

Rules:
1. Provide at least one natural example sentence per sense.
2. For nouns, ALWAYS specify the article (de/het) — this is the single most important field for learners. Also give the plural in notes.
3. For verbs, identify the auxiliary (hebben/zijn) and whether the verb is separable (e.g. opbellen → "ik bel op").
4. List senses from most common to least common.
5. Mark register accurately - most words are neutral, omit the field if neutral.
6. Flag false friends with English if applicable (e.g., "slim" means "clever", not "slim"; "bij" can mean "bee" or "near/at").
7. If unsure about any optional field, omit it rather than guessing — but never omit the article for a noun.
8. Output ONLY valid JSON, no markdown or commentary.
```

**Few-shot examples for Dutch:**

Example 1 (verb, separable):
```json
{
  "lemma": "opbellen",
  "pos": "verb",
  "auxiliary": "hebben",
  "separable": true,
  "senses": [
    {
      "definition_en": "to call (someone) on the phone, to ring up",
      "examples": [
        {"nl": "Ik bel je morgen op.", "en": "I'll call you tomorrow."},
        {"nl": "Heb je de dokter al opgebeld?", "en": "Have you called the doctor yet?"}
      ]
    }
  ],
  "frequency": "common",
  "notes": "Separable: prefix 'op' splits off in main clauses (bel ... op)."
}
```

Example 2 (noun):
```json
{
  "lemma": "sleutel",
  "pos": "noun",
  "gender": "m",
  "article": "de",
  "senses": [
    {
      "definition_en": "key (for a lock)",
      "examples": [
        {"nl": "Ik ben mijn sleutel kwijt.", "en": "I lost my key."}
      ]
    },
    {
      "definition_en": "key (figurative: solution to a problem)",
      "examples": [
        {"nl": "Dat is de sleutel tot succes.", "en": "That is the key to success."}
      ]
    }
  ],
  "frequency": "common",
  "notes": "Plural: sleutels. Diminutive: sleuteltje (het)."
}
```

**Validation** — Same pattern as Czech but adapted:
- Required fields: lemma, pos, senses
- Nouns must have `article` (de/het); `gender` is optional
- Check that English definitions don't contain stray Dutch fragments (Dutch is mostly ASCII, so a diacritic in the English side is a weak signal — check for whole-word Dutch leakage instead)
- Confidence: 0.8 for valid entries, 0.5 for entries with issues (e.g., noun missing article)

**Dutch character detection** (for filtering non-Dutch words):
```python
# Dutch uses mostly ASCII; diacritics appear in loanwords/stress marks.
dutch_diacritics = set("ëïéèêçáàäöüúîô")
```
A word containing these is *probably* Dutch (or a loanword in Dutch). But most Dutch words are pure ASCII (*huis*, *boek*, *spelen*), so this is a weak signal — rely primarily on the lemmatizer to verify, exactly like German.

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
5. Filter: remove proper nouns, non-Dutch words, low-frequency words
6. Deduplicate by lemma (so "liep" and "gelopen" both map to "lopen" — only generate once)
7. Generate definitions via DeepSeek
8. Optionally re-import inflections for new lemmas

**is_likely_dutch() function:**
```python
def is_likely_dutch(word, pos_map):
    dutch_diacritics = set("ëïéèêçáàäöüúîô")
    skip_words = {
        "www", "com", "http", "https", "html", "url", "email", "epub",
        "isbn", "pdf", "txt", "jpg", "png", "gif", "xml", "css",
        "copyright", "ebook", "kindle", "calibre",
        # common English function words (English bleed-through in epubs)
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
    if any(c in dutch_diacritics for c in w):
        return True
    if w.isascii() and len(w) <= 2:
        return False
    if w.isascii():
        # Use lemmatizer POS to check if it's recognized as Dutch
        if pos_map.get(w, '') != '':
            return True
        return False
    return True
```
Note: many short English words overlap with Dutch (*in, op, het, een, de, was, man, hand*). Lean on the lemmatizer POS signal and the Wiktionary/inflection lookups rather than spelling heuristics; the skip-list above only kills obvious English/markup noise.

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
1. `lookup <word>` — Look up a word: check entries table, then inflections table, show full entry JSON (including article for nouns)
2. `coverage <text_file>` — Analyze word coverage of a text file: tokenize, check each word against DB, report coverage %, list top 50 missing words

---

## Implementation order

1. **Research first** — Investigate Dutch morphology options, find best inflection source and runtime lemmatizer. Report findings before writing code.
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
- Kindle OPF needs `<DictionaryInLanguage>nl</DictionaryInLanguage>` and `<DictionaryOutLanguage>en</DictionaryOutLanguage>`.
- Default dict name: "Dutch-English" everywhere.
- **Unlike German, Dutch nouns are NOT capitalized** — store and display lemmas lowercase like English/Czech. The inflections table stores everything lowercase for lookup, as before.
- The `article` (de/het) field is the headline learner feature for Dutch — surface it prominently in every export format and never let DeepSeek omit it for nouns.
- Treat separable verbs carefully end-to-end: the lemma is the joined form (*opbellen*), but running text shows the split form (*bel … op*). Make sure the inflections table maps split/participle forms (*belt op*, *opgebeld*, *belde op*) back to *opbellen*, since KOReader/Kindle lookups will hit the surface form.

## Start with the research phase

Don't write code yet. First:
1. Download and examine the Kaikki Dutch JSONL (just the first 100 lines) to understand the actual structure — confirm gender (m/f/n), `forms` (plurals, diminutives, conjugation), separable-verb marking, etc. are present as expected.
2. Research Dutch inflection/morphology options (Kaikki forms volume, spaCy `nl` quality, OpenTaal Hunspell expansion, Frog) and recommend the best approach with concrete pair counts.
3. Research a second dictionary source (FreeDict `nld-eng` TEI, etc.) — confirm format and size.
4. Report your findings and proposed plan. Then we'll proceed to implementation.

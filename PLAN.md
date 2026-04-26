# Implementation Plan

Phased plan to build the BetterOfflineDict Czech-English dictionary.

---

## Phase 1: Foundation (Data Acquisition + Database)

**Goal**: Download all data sources, parse them, and populate the initial SQLite database.

### 1.1 Download Data Sources

- [ ] Kaikki.org English Wiktionary Czech JSONL (https://kaikki.org/dictionary/Czech/)
- [ ] Kaikki.org Czech Wiktionary JSONL (https://kaikki.org/cswiktionary/)
- [ ] MorfFlex CZ 2.1 TSV (https://hdl.handle.net/11234/1-5833, requires LINDAT agreement)
- [ ] Svobodne Slovniky / Cihar StarDict snapshot (https://cihar.com/software/slovnik/)
- [ ] FreeDict eng-ces TEI XML (https://download.freedict.org/dictionaries/eng-ces/)
- [ ] Czech frequency lists from CNC (http://www.korpus.cz/lists)
- [ ] Tatoeba Czech-English pairs (https://www.manythings.org/anki/ces-eng.zip)

### 1.2 Create SQLite Database

- [ ] Implement schema (see ARCHITECTURE.md)
- [ ] Write parsers for each data source format (JSONL, TSV, StarDict, TEI XML)
- [ ] Import kaikki en.wiktionary Czech data (~49K lemmas with definitions)
- [ ] Import kaikki cs.wiktionary Czech-English senses (~26K)
- [ ] Import Svobodne Slovniky entries (~88K)
- [ ] Import/reverse FreeDict eng-ces entries
- [ ] Deduplicate and merge overlapping entries (prefer Wiktionary for richer definitions)
- [ ] Tag each entry with its source

### 1.3 Load MorfFlex Inflections

- [ ] Parse MorfFlex CZ 2.1 TSV (127M triples)
- [ ] Build form->lemma mapping table
- [ ] Link inflected forms only for lemmas that exist in the dictionary
- [ ] Store in the `inflections` table
- [ ] Validate: spot-check common words (být, dělat, město, mladý)

### 1.4 Import Frequency Data

- [ ] Parse CNC frequency lists
- [ ] Tag entries with frequency (common/moderate/uncommon/rare)
- [ ] Use frequency for ambiguity ranking

**Deliverable**: SQLite database with ~80-130K lemma entries + millions of inflected form mappings.

---

## Phase 2: First Export (StarDict for KOReader)

**Goal**: Generate a working StarDict dictionary and test it on KOReader.

### 2.1 StarDict Export Script

- [ ] Write export script: SQLite -> tab-delimited format
- [ ] Generate HTML definitions (headword, POS, gender, definitions, examples)
- [ ] Decide on MergeSyns vs .syn approach (test both for performance)
- [ ] Generate inflected form entries/synonyms from `inflections` table
- [ ] Convert with pyglossary
- [ ] Compress with dictzip

### 2.2 Testing

- [ ] Test with `sdcv` on desktop: look up lemmas AND inflected forms
- [ ] Test lookup speed (critical with 2.5M+ entries)
- [ ] Test on actual KOReader (on Kindle or other device)
- [ ] Compare coverage against the Witcher text (already analyzed)
- [ ] Measure: what % of unique word forms in the Witcher text can be looked up?

### 2.3 Iterate

- [ ] Fix any format issues found during testing
- [ ] Optimize definition HTML for small screen readability
- [ ] Tune inflection coverage (are common forms being found?)

**Deliverable**: Working `.ifo`/`.idx`/`.dict.dz` files usable on KOReader.

---

## Phase 3: LLM Enrichment Pipeline

**Goal**: Build the automated pipeline to process Czech texts and fill dictionary gaps with DeepSeek.

### 3.1 Pipeline Script

- [ ] Refactor existing `parse_cs_txt.py` and `known_analyzer.py` into reusable modules
- [ ] Implement `process_text.py`:
  1. Accept text file path
  2. Tokenize + lemmatize (Majka)
  3. Query SQLite for missing lemmas
  4. Extract context sentences from source text
  5. Report: X unique lemmas, Y missing, estimated cost

### 3.2 DeepSeek Integration

- [ ] Implement async DeepSeek API client
- [ ] Design and test system prompt + few-shot examples
- [ ] Implement batch processing (10 concurrent requests)
- [ ] Implement JSON response parsing and schema validation
- [ ] Add retry logic for API failures

### 3.3 Validation Layer

- [ ] Implement automated validation checks (schema, language, completeness)
- [ ] Implement cross-reference check against Wiktionary data
- [ ] Implement confidence scoring
- [ ] Create review queue for low-confidence entries

### 3.4 End-to-End Test

- [ ] Process the Witcher text through the full pipeline
- [ ] Measure: how many words were missing? How many got definitions?
- [ ] Spot-check 50 LLM-generated definitions for quality
- [ ] Re-export to StarDict and test on KOReader

**Deliverable**: `process_text.py` script that takes any Czech text and enriches the dictionary.

---

## Phase 4: Kindle Dictionary Export

**Goal**: Generate a Kindle-format dictionary with full inflection support.

### 4.1 Kindle Export Script

- [ ] Write export: SQLite -> XHTML with idx:entry/idx:orth/idx:infl/idx:iform tags
- [ ] Split into multiple ~250KB XHTML files
- [ ] Generate OPF metadata file
- [ ] Handle inflection collision workaround (duplicate entries)

### 4.2 Build and Test

- [ ] Compile with kindlegen
- [ ] Test on Kindle device (native reader, not KOReader)
- [ ] Test inflection lookup: look up declined nouns, conjugated verbs
- [ ] Measure lookup speed

**Deliverable**: Working `.mobi` dictionary file for Kindle.

---

## Phase 5: Yomitan Dictionary

**Goal**: Create a Yomitan-compatible dictionary (and optionally contribute Czech language support to Yomitan).

### 5.1 Yomitan Export Script

- [ ] Write export: SQLite -> Yomitan term_bank JSON arrays
- [ ] Include inflected forms as separate term entries
- [ ] Generate index.json + tag_bank
- [ ] ZIP into dictionary archive

### 5.2 Czech Deinflection Rules (Optional, High Impact)

- [ ] Study Yomitan's transform system (suffixInflection helper)
- [ ] Write Czech deinflection rules for common patterns
- [ ] Submit PR to Yomitan project for Czech language support

### 5.3 Test

- [ ] Load dictionary in Yomitan browser extension
- [ ] Test lookups on Czech web pages

**Deliverable**: Working `.zip` Yomitan dictionary.

---

## Phase 6: Polish and Maintain

### 6.1 Quality Improvements

- [ ] Process 5-10 more Czech books through the pipeline
- [ ] Review and approve/correct entries in the review queue
- [ ] Add example sentences from Tatoeba for entries that lack them
- [ ] Add aspect pair cross-references for verbs (from Vallex or manual)

### 6.2 Upgrade Lemmatizer (Optional)

- [ ] Evaluate MorphoDiTa vs current Majka for accuracy
- [ ] If MorphoDiTa is significantly better, integrate it
- [ ] Add guesser support for truly unknown words

### 6.3 Community / Distribution

- [ ] Set up GitHub repo with clear README
- [ ] Publish pre-built dictionaries (StarDict, Kindle, Yomitan) as releases
- [ ] Write contribution guide for adding/correcting entries
- [ ] Consider CC-CEDICT-like format for community contributions

---

## Priority Order

For maximum value with minimum effort:

1. **Phase 1** (Foundation) - Can't skip this
2. **Phase 2** (StarDict/KOReader) - Your primary use case
3. **Phase 3** (LLM Pipeline) - The key differentiator, fills gaps
4. **Phase 4** (Kindle) - If you use native Kindle reader
5. **Phase 5** (Yomitan) - If you read Czech in the browser
6. **Phase 6** (Polish) - Ongoing maintenance

---

## Key Resources to Study

Before starting implementation, worth examining these existing projects for code/patterns:

1. **Vuizur/czech-dictionary-extender** (https://github.com/Vuizur/czech-dictionary-extender) - Very similar project, combines Svobodne Slovniky + MorfFlex, outputs StarDict
2. **Vuizur/ebook_dictionary_creator** (https://github.com/Vuizur/ebook_dictionary_creator) - Wiktionary to ebook dict, supports Czech, has Kindle workarounds
3. **anezih/add_inflections** (https://github.com/anezih/add_inflections) - Adds inflections to StarDict via .syn files
4. **pyglossary** (https://github.com/ilius/pyglossary) - Format conversion Swiss army knife

---

## Quick Win: Fastest Path to a Working Dictionary

If you want something usable as fast as possible:

1. Download Vuizur's pre-built Czech-English Wiktionary StarDict from https://github.com/Vuizur/Wiktionary-Dictionaries
2. Use add_inflections to inject Czech Hunspell inflections into it
3. Copy to KOReader's dict folder
4. Test

This gives you a ~49K-entry dictionary with inflections in under an hour. Then iterate from there with the full pipeline.

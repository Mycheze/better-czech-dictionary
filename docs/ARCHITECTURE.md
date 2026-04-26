# Architecture: Dictionary Design and Format Specifications

---

## 1. Canonical Data Format (Source of Truth)

The dictionary is maintained as a **SQLite database**. All exports (StarDict, Kindle, Yomitan) are generated from this single source.

### Schema

```sql
CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma TEXT NOT NULL,
    pos TEXT NOT NULL,           -- noun, verb, adjective, adverb, etc.
    gender TEXT,                 -- m_anim, m_inanim, f, n (nouns only)
    aspect TEXT,                 -- imperfective, perfective, biaspectual (verbs only)
    aspect_pair TEXT,            -- linked aspect partner lemma (verbs only)
    frequency TEXT,              -- common, moderate, uncommon, rare, archaic
    entry_json TEXT NOT NULL,    -- Full structured entry (see JSON schema below)
    source TEXT NOT NULL,        -- "wiktionary", "deepseek", "freedict", "manual"
    confidence REAL DEFAULT 1.0, -- 0.0-1.0, lower for LLM-generated
    validated BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(lemma, pos)          -- Same lemma can be noun + verb etc.
);

CREATE TABLE inflections (
    form TEXT NOT NULL,          -- The inflected surface form
    lemma TEXT NOT NULL,         -- Points to entries.lemma
    pos TEXT NOT NULL,           -- Points to entries.pos
    tag TEXT,                    -- Morphological tag (Prague positional or simplified)
    PRIMARY KEY (form, lemma, pos)
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
    reason TEXT,                 -- "low_confidence", "no_cross_ref", "llm_disagreement"
    status TEXT DEFAULT 'pending',
    reviewer_notes TEXT,
    FOREIGN KEY (entry_id) REFERENCES entries(id)
);

CREATE INDEX idx_inflections_form ON inflections(form);
CREATE INDEX idx_entries_lemma ON entries(lemma);
CREATE INDEX idx_entries_source ON entries(source);
CREATE INDEX idx_entries_confidence ON entries(confidence);
```

### Entry JSON Schema

```json
{
  "lemma": "město",
  "pos": "noun",
  "gender": "n",
  "pronunciation": "ˈmɲɛsto",
  "senses": [
    {
      "definition_en": "city, town",
      "register": "neutral",
      "domain": "general",
      "examples": [
        {"cs": "Velké město.", "en": "A big city."}
      ],
      "synonyms_cs": ["obec"],
      "see_also": ["městský", "městečko"]
    }
  ],
  "etymology": "From Proto-Slavic *město",
  "frequency": "common",
  "notes": "Neuter noun, město paradigm"
}
```

---

## 2. Target Format: StarDict (KOReader, GoldenDict, sdcv)

### File Structure

```
czech-english.ifo     -- metadata
czech-english.idx     -- binary index (sorted headwords + offsets)
czech-english.dict.dz -- compressed definitions (dictzip)
czech-english.syn     -- synonym/alias file (inflections -> lemma index)
```

### KOReader Specifics

- KOReader uses `sdcv` backend -- NO built-in morphological analysis
- Dictionary must contain ALL inflected forms for lookup to work
- Two approaches for inflections:

**Approach A: .syn file** (map inflections to lemma entry index)
- Simpler to generate
- Performance problem: sdcv loads entire .syn into memory at startup
- 2.4M synonyms = 3+ sec on i7, 15+ sec on Kindle
- sdcv 0.5.4+ uses binary search (faster but still loads all to memory)

**Approach B: StardictMergeSyns** (duplicate entries for each inflected form)
- Each inflected form gets its own full entry in .idx/.dict
- Larger file size but no .syn performance bottleneck
- pyglossary `--write-format=StardictMergeSyns`
- **Recommended approach for Czech** given ~2.5M inflected forms

### .ifo Format
```
StarDict's dict ifo file
version=2.4.2
bookname=Czech-English Dictionary
wordcount=2500000
idxfilesize=...
sametypesequence=h
```

### .idx Format
Binary, sorted by `stardict_strcmp()`. Each record:
- Word: UTF-8 null-terminated (max 255 bytes)
- Offset: 4 bytes big-endian
- Size: 4 bytes big-endian

### Compression
`dictzip` compresses `.dict` -> `.dict.dz` with random-access blocks.
Install: `apt install dictzip`

### Device Paths
- Kindle KOReader: `.adds/koreader/data/dict/` (Kobo) or similar
- Linux: `$HOME/.config/koreader/data/dict`
- Android: `/sdcard/koreader/data/dict`

### Tools
- **pyglossary**: Swiss-army knife converter. `pip install pyglossary`
- **sdcv**: CLI StarDict reader for testing: `sdcv -n -u "Czech-English" město`
- **dictzip**: `apt install dictzip`

---

## 3. Target Format: Kindle Dictionary (.mobi)

### Entry XHTML Structure

```html
<idx:entry name="default" scriptable="yes" spell="yes">
  <idx:orth value="město">město
    <idx:infl inflgrp="noun">
      <idx:iform value="města" exact="yes"/>
      <idx:iform value="městu" exact="yes"/>
      <idx:iform value="městě" exact="yes"/>
      <idx:iform value="městem" exact="yes"/>
      <idx:iform value="měst" exact="yes"/>
      <idx:iform value="městům" exact="yes"/>
      <idx:iform value="městech" exact="yes"/>
      <idx:iform value="městy" exact="yes"/>
    </idx:infl>
  </idx:orth>
  <p><b>město</b> <i>n</i> — city, town</p>
  <p><i>Velké město.</i> — A big city.</p>
</idx:entry>
```

### OPF Metadata

```xml
<x-metadata>
  <DictionaryInLanguage>cs</DictionaryInLanguage>
  <DictionaryOutLanguage>en</DictionaryOutLanguage>
  <DefaultLookupIndex>default</DefaultLookupIndex>
</x-metadata>
```

### Key Gotchas

1. **`exact="yes"` is ignored** by kindlegen -- Kindle uses fuzzy/accent-insensitive matching regardless
2. **Entry collision**: When a headword match is found, Kindle stops searching -- inflections that match another headword won't be found. Workaround: duplicate entries for colliding inflections
3. **Split large files**: Keep XHTML files ~250KB each for performance
4. **No limit on `<idx:iform>` count** per entry, but large inflection lists may slow lookup

### Build Tools

- **kindlegen**: Legacy (discontinued), findable in Kindle Previewer: `/Applications/Kindle Previewer 3.app/Contents/lib/fc/bin/kindlegen`
- **tab2opf**: https://github.com/apeyser/tab2opf -- converts tab-delimited to OPF+HTML
- **pyglossary**: Can output MOBI directly (write-only)

---

## 4. Target Format: Yomitan

### ZIP Archive Structure

```
dictionary.zip/
  index.json              -- metadata
  tag_bank_1.json         -- tag definitions
  term_bank_1.json        -- term entries (up to ~10K per file)
  term_bank_2.json
  term_meta_bank_1.json   -- frequency data (optional)
```

### index.json

```json
{
  "title": "Czech-English Dictionary",
  "revision": "1.0",
  "format": 3,
  "sequenced": true,
  "author": "BetterOfflineDict",
  "description": "Comprehensive Czech-English dictionary with full morphological coverage",
  "sourceLanguage": "cs",
  "targetLanguage": "en"
}
```

### term_bank entry format (8-element arrays)

```json
[
  "město",           // [0] Term text
  "",                // [1] Reading (empty if same as term)
  "",                // [2] Definition tags
  "",                // [3] Rule identifiers for deinflection
  0,                 // [4] Score (negative=rare, positive=common)
  ["city, town"],    // [5] Definitions array
  1,                 // [6] Sequence number (groups related entries)
  "noun"             // [7] Term tags
]
```

### Czech Support Status

**Czech is NOT currently supported** in Yomitan (39 languages, no Czech). To add support:

1. Add entry to `language-descriptors.js` with ISO code `cs`
2. Create `ext/js/language/cs/czech-transforms.js` with deinflection rules
3. Submit PR to Yomitan project

The deinflection system uses `suffixInflection(deinflectedSuffix, inflectedSuffix, conditionsIn, conditionsOut)`. For Czech, this would need hundreds of suffix rules for each POS.

**Alternative**: Instead of implementing Czech deinflection in Yomitan, include ALL inflected forms as separate term entries in the dictionary. Each inflected form entry points to the lemma's definition. This is the brute-force approach but guaranteed to work without Yomitan language support.

### Tools

- **yomichan-dict-builder**: https://github.com/MarvNC/yomichan-dict-builder (npm package)
- **kaikki-to-yomitan**: https://github.com/yomidevs/kaikki-to-yomitan (doesn't include Czech yet, could be extended)

---

## 5. Morphology Strategy

### Layer 1: Precomputed Lookup (MorfFlex) -- covers ~97% of text

Build a form->lemma hash map from MorfFlex CZ 2.1:

```
"hradech" -> ["hrad"]
"dělali" -> ["dělat"]
"mladých" -> ["mladý"]
"je"     -> ["být", "on"]  (ambiguous)
```

Storage: Raw MorfFlex ~238MB compressed. Compiled into binary structure: ~20-50MB.

### Layer 2: MorphoDiTa Guesser -- covers another ~2%

For words NOT in dictionary, predict lemma from suffix patterns:

```python
morpho = tagger.getMorpho()
result = morpho.analyze("neologismus", morpho.GUESSER, lemmas)
```

### Layer 3: Prefix Stripping -- for unknown prefixed verbs

Try stripping known prefixes and looking up base verb:

```
"znovuobjevovat" -> strip "znovu" -> try "objevovat" -> found!
```

Prefix list: do-, na-, nad(e)-, o-/ob(e)-, od(e)-, po-, pod(e)-, pro-, pře-, před(e)-, při-, roz(e)-, s(e)-, u-, v(e)-, vy-, z(e)/s-, za-, ne- (negation, always strip first)

### Layer 4: Graceful Degradation

For remaining ~1%: Show word as-is, offer substring search, suggest edit-distance matches.

### Ambiguity Resolution

When a form maps to multiple lemmas (e.g., "je" -> být/on):
- **For static dictionaries**: Include all possible lemmas, ranked by frequency (CNC data)
- **For reader integration**: Use context-aware MorphoDiTa tagger to disambiguate

### Coverage by Text Type

| Text Type | MorfFlex Coverage | With Guesser |
|-----------|------------------|--------------|
| Newspaper | 98-99% | 99%+ |
| Literary fiction | 96-98% | 98-99% |
| Web text | 93-96% | 96-98% |
| Technical | 95-97% | 97-99% |

---

## 6. Size Estimates

### Dictionary Entry Counts (Target)

| Source | Unique Lemmas | After Dedup |
|--------|--------------|-------------|
| Kaikki en.wiktionary | ~49K | ~49K |
| Kaikki cs.wiktionary (cs-en) | ~26K | ~15K new |
| Svobodne Slovniky | ~88K | ~30K new |
| FreeDict eng-ces (reversed) | ~150K | ~40K new |
| DeepSeek gap-filling | open-ended | varies |
| **Combined estimate** | | **~130-150K lemmas** |

### Inflected Form Counts

With MorfFlex providing inflections for all lemmas: **~2-3 million inflected forms**

### File Size Estimates

| Format | Estimated Size |
|--------|---------------|
| SQLite source DB | ~500MB-1GB |
| StarDict (MergeSyns) | ~200-400MB uncompressed, ~80-150MB with dictzip |
| StarDict (with .syn) | ~100-200MB + large .syn |
| Kindle .mobi | ~100-200MB |
| Yomitan .zip | ~50-100MB |

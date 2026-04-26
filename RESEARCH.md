# Research: Czech-English Dictionary Resources

Comprehensive research on all available data sources, tools, and prior art for building a complete offline Czech-English dictionary.

---

## 1. Existing Dictionary Data Sources

### Kaikki.org / English Wiktionary Czech Extraction (PRIMARY SOURCE)

The richest structured source for Czech-English dictionary content.

- **Coverage**: ~49,459 Czech lemmas + ~20,316 non-lemma forms = ~69,775 total entries
- **Breakdown**: 23,903 nouns, 8,355 adjectives, 5,160 verbs, 3,629 multiword terms, 1,178 adverbs
- **Data fields**: Definitions (English), pronunciations (IPA), inflection tables, etymology, usage examples, translations, semantic categories, POS tags
- **Format**: JSONL (JSON Lines) -- one JSON object per word sense
- **License**: CC BY-SA 3.0 + GFDL (Wiktionary license)
- **URL**: https://kaikki.org/dictionary/Czech/index.html
- **Raw data**: https://kaikki.org/dictionary/rawdata.html
- **Update frequency**: Regular (latest from enwiktionary dump 2026-03-03)

### Kaikki.org / Czech Wiktionary Extraction

Broader Czech coverage but definitions primarily in Czech.

- **Coverage**: 123,054 Czech senses, 25,793 Czech-English senses (out of 286,453 total)
- **Format**: JSONL
- **License**: CC BY-SA + GFDL
- **URL**: https://kaikki.org/cswiktionary/
- **Use case**: Supplement en.wiktionary gaps, potential monolingual dictionary base

### GNU/FDL Anglicko-Cesky Slovnik (svobodneslovniky.cz)

The main open-source Czech-English community dictionary.

- **Coverage**: ~88,000+ entries (English-Czech direction primarily)
- **Format**: Available as StarDict from Michal Cihar's daily snapshots
- **License**: GNU FDL
- **StarDict download**: https://cihar.com/software/slovnik/ (4.0 MiB download)
- **Source**: https://www.svobodneslovniky.cz/

### FreeDict Project

- **Czech-English (ces-eng)**: Only **488 headwords** -- essentially useless
- **English-Czech (eng-ces)**: **150,004 headwords** -- substantial but wrong direction for reading
- **Format**: TEI XML, StarDict, DICT
- **License**: ces-eng GPL, eng-ces CC BY-SA (WikDict-derived)
- **URL**: https://download.freedict.org/dictionaries/

### WikDict

- **Coverage**: Czech included in their 17.4M translations across 26 languages
- **Formats**: SQLite, TEI P5 XML (no StarDict for Czech specifically)
- **License**: CC BY-SA
- **URL**: https://www.wikdict.com/cs-en/ and https://download.wikdict.com/dictionaries/
- **Limitation**: No downloadable StarDict for Czech; SQLite/TEI might work

### GLOSBE

- Large Czech-English dictionary but **NOT open source**. Cannot be used as bulk data source.
- URL: https://glosbe.com/cs/en

---

## 2. Morphological Resources (CRITICAL)

### MorfFlex CZ 2.1 (THE essential resource)

The most comprehensive Czech morphological dictionary in existence. Maps every inflected form to its lemma.

- **Coverage**: **126,906,921 lemma-tag-wordform triples**, ~900,000 lemmas, 100M+ word forms
- **Format**: TSV (lemma, positional tag, wordform)
- **Tag system**: Prague 15-position tags (4,000+ distinct tags)
- **License**: **CC BY-NC-SA 4.0** (non-commercial; commercial license available from UFAL)
- **Download**: https://hdl.handle.net/11234/1-5833 (LINDAT repository, 238.88 MB compressed)
- **Released**: 2024-12-23
- **Coverage on standard text**: 96-99% of running tokens

### Majka (ALREADY IN PROJECT)

Fast finite-state morphological analyzer from Masaryk University.

- **Coverage**: ~903,888 distinct word forms, ~46,000 lemmas (Limited version)
- **Speed**: ~1M words/sec (FST lookup)
- **Format**: Compiled binary FST (~5 MB)
- **License**: CC BY-SA 4.0 (Limited), CC BY-NC-SA 4.0 (Free)
- **URL**: https://nlp.fi.muni.cz/czech-morphology-analyser/
- **Python wrapper**: `pip install majka`
- **Already integrated**: `./majka -f ./majka.w-lt`

### MorphoDiTa (Potential upgrade)

Morphological Dictionary and Tagger from UFAL. Built on MorfFlex.

- **Lemma accuracy**: 98.45% (macro average across test sets)
- **Speed**: 10,000-200,000 words/second
- **Features**: Analysis + disambiguation + guesser for unknown words
- **License**: Software MPL 2.0, Models CC BY-NC-SA
- **Python**: `pip install ufal.morphodita`
- **URL**: https://ufal.mff.cuni.cz/morphodita

### UDPipe 2

Universal Dependencies parser with best-in-class Czech lemmatization.

- **Lemma accuracy**: **99.54%** F1 on czech-pdtc (raw text)
- **License**: Models CC BY-NC-SA
- **REST API**: https://lindat.mff.cuni.cz/services/udpipe/
- **URL**: https://ufal.mff.cuni.cz/udpipe/2

### spaCy Czech Models

- **Models**: `cs_core_news_sm`, `cs_core_news_md`, `cs_core_news_lg`
- **Accuracy**: ~97-98% (estimated)
- **License**: MIT (spaCy framework)
- **Also**: `spacy-udpipe` wraps UDPipe models in spaCy API

### Comparison

| Tool | Lemma Accuracy | Speed | Ease of Use | Guesser |
|------|---------------|-------|-------------|---------|
| UDPipe 2 | 99.54% | Moderate | Python/REST | Yes |
| MorphoDiTa | 98.45% | Very fast | C++/Python | Yes |
| Majka | ~95-97% (est.) | Fastest | Binary/Python | No |
| spaCy Czech | ~97-98% | Fast | Very easy | No |

---

## 3. Czech Morphological Complexity

Understanding WHY we need 127M form-lemma mappings:

### Nouns: 7 cases x 2 numbers = 14 slots, ~8-12 distinct surface forms per noun
- 4 genders (masc animate, masc inanimate, feminine, neuter)
- ~14 major declension paradigms
- Example: "hrad" (castle) -> hrad, hradu, hradu, hrad, hrade, hradě, hradem, hrady, hradů, hradům, hrady, hrady, hradech, hrady

### Adjectives: 4 genders x 7 cases x 2 numbers = 56 slots, ~20-30 distinct forms
- Plus comparative and superlative (each also declines)
- **Total: ~50-60 distinct surface forms per adjective**
- Example: mladý -> mladý, mladá, mladé, mladého, mladému, mladém, mladým, mladí, mladých, mladší, nejmladší...

### Verbs: THE hardest part
- Present (6 forms), past (5 gender-inflected participle forms), imperative (3), conditional, transgressives, passive participle (declines like adjective ~25 forms), verbal noun (declines ~8 forms)
- **Total: 40-100+ distinct surface forms per verb**
- Plus productive prefixation: dělat -> udělat, dodělat, předělat, nadělat, oddělat, vydělat, zadělat, podělat, přidělat, rozdělat...
- ~18-20 productive verbal prefixes: do-, na-, nad(e)-, o-/ob(e)-, od(e)-, po-, pod(e)-, pro-, pře-, před(e)-, při-, roz(e)-, s(e)-, u-, v(e)-, vy-, z(e)/s-, za-

### Aspect Pairs
- Every verb has perfective/imperfective forms (separate lemmas, same meaning)
- dělat (impf.) / udělat (pf.), psát / napsat, číst / přečíst, kupovat / koupit
- Dictionary needs cross-references between pairs

### Scale for a 100K-lemma dictionary:
- ~50K nouns x ~10 forms = 500K
- ~15K adjectives x ~50 forms = 750K
- ~20K verbs x ~60 forms = 1.2M
- ~15K other x ~5 forms = 75K
- **Total: ~2.5 million inflected forms**

---

## 4. Parallel Corpora and Bilingual Resources

### CzEng 2.0
- Over **2 billion words** in each language
- Free for non-commercial research/educational purposes
- URL: https://ufal.mff.cuni.cz/czeng

### OpenSubtitles v2018 (via OPUS)
- **~44.7 million aligned Czech-English sentence pairs**
- URL: https://opus.nlpl.eu/OpenSubtitles/cs&en/v2018/OpenSubtitles
- Caveat: Colloquial language, may contain errors

### Tatoeba
- **~41,869 Czech-English pairs**
- High quality, human-created
- URL: https://tatoeba.org/en/downloads
- Pre-formatted: https://www.manythings.org/anki/ (ces-eng.zip)
- License: CC BY 2.0 FR
- Great for example sentences

### Other OPUS corpora
- Europarl, EUBookshop, EMEA (medical), JRC-Acquis (EU legal), WikiMatrix, ParaCrawl
- URL: https://opus.nlpl.eu/

---

## 5. Frequency Lists

### Czech National Corpus (CNC)
- Comparative frequency lists from SYN2000/2005/2010/2015 corpora
- License: **CC BY 4.0**
- URL: http://www.korpus.cz/lists

### Wiktionary Czech Frequency Wordlist (SYN2015)
- Top **15,000 most used Czech words**
- URL: https://en.wiktionary.org/wiki/Wiktionary:Frequency_lists/Czech_wordlist

### Leipzig Corpora Collection
- Czech corpora: News, Web, Wikipedia categories, up to 1M sentences each
- Includes word frequency lists up to 1M entries
- License: CC BY 4.0
- URL: https://wortschatz.uni-leipzig.de/en/download/ces

---

## 6. Existing Similar Projects

### Vuizur/czech-dictionary-extender (CLOSEST PRIOR ART)

Combines svobodneslovniky.cz data with MorfFlex inflections. Essentially the same concept.

- **URL**: https://github.com/Vuizur/czech-dictionary-extender
- **Output**: StarDict, Tabfile
- **License**: GNU FDL / CC-BY-SA-NC
- **Worth studying/forking**

### Vuizur/Wiktionary-Dictionaries

Extracted Wiktionary data in e-reader formats for many languages.

- **Czech-English**: TSV (~5 MB) and Kindle (.mobi) available
- **URL**: https://github.com/Vuizur/Wiktionary-Dictionaries
- **License**: CC BY-SA 3.0 + GNU FDL

### Vuizur/ebook_dictionary_creator

Creates ebook dictionaries from kaikki.org Wiktionary data. Supports Czech.

- **URL**: https://github.com/Vuizur/ebook_dictionary_creator
- **Outputs**: StarDict, Kindle, Tabfile
- **Has Kindle inflection bug workaround** (`try_to_fix_failed_inflections`)
- **Uses pyglossary internally**

### reader-dict.com

Commercial Czech-English dictionary (~408K entries). Not open source.

- URL: https://www.reader-dict.com/get/cs/en

### add_inflections (by anezih)

Adds inflected forms to StarDict dictionaries via .syn files.

- **URL**: https://github.com/anezih/add_inflections
- Accepts Hunspell .aff/.dic files or JSON morphological data
- Critical tool for the inflection injection step

---

## 7. Academic Resources (UFAL, Charles University)

- **Vallex**: Czech verb valency lexicon (aspect pair information)
- **CzEngVallex**: 20,835 aligned Czech-English verb sense pairs
- **PDT**: Prague Dependency Treebank (rich linguistic annotations)
- **RobeCzech**: Czech BERT model (used by UDPipe 2)

---

## 8. License Summary

| Resource | License | Commercial OK? |
|----------|---------|---------------|
| Kaikki/Wiktionary | CC BY-SA 3.0 + GFDL | Yes (with attribution + share-alike) |
| MorfFlex CZ 2.1 | CC BY-NC-SA 4.0 | **No** (contact UFAL) |
| Majka (Limited) | CC BY-SA 4.0 | Yes |
| MorphoDiTa models | CC BY-NC-SA | No |
| Svobodne Slovniky | GNU FDL | Yes |
| FreeDict eng-ces | CC BY-SA | Yes |
| Tatoeba | CC BY 2.0 FR | Yes |
| CNC frequency lists | CC BY 4.0 | Yes |

**Key constraint**: MorfFlex CZ is the most important resource but is non-commercial. For an open-source, free dictionary project this is fine. If commercialization ever matters, contact UFAL for licensing.

# Pipeline: Text Processing and LLM Enrichment

How to process Czech texts, detect missing words, generate definitions with DeepSeek, and grow the dictionary over time.

---

## 1. Overview

```
Czech Text (.txt/.epub)
    |
    v
[1. Tokenization]
    |
    v
[2. Lemmatization] (Majka / MorphoDiTa)
    |
    v
[3. Dictionary Lookup] -> missing lemmas list
    |
    v
[4. Context Extraction] -> example sentences from source text
    |
    v
[5. DeepSeek API] -> structured JSON dictionary entries
    |
    v
[6. Validation] -> schema check, cross-ref, confidence scoring
    |
    v
[7. SQLite Dictionary DB] -> persistent, growing dictionary
    |
    v
[8. Export] -> StarDict / Kindle / Yomitan
```

---

## 2. Step 1-2: Tokenization and Lemmatization

Already implemented in `parse_cs_txt.py` and `known_analyzer.py`.

**Current stack**: Majka (`./majka -f ./majka.w-lt`), one word per line on stdin, output `lemma:tag` per line.

**Potential upgrade**: MorphoDiTa (98.45% accuracy) or UDPipe 2 (99.54% accuracy) for higher quality lemmatization. Majka is fastest but has no guesser for unknown words -- it returns the input unchanged when it can't analyze.

---

## 3. Step 3: Missing Word Detection

Query the SQLite dictionary for each unique lemma. Produce a frequency-ranked list of missing words.

```python
def find_missing_lemmas(lemmas_with_freq, db_path="dictionary.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    missing = []
    for lemma, freq, pos in lemmas_with_freq:
        cursor.execute("SELECT 1 FROM entries WHERE lemma = ?", (lemma,))
        if cursor.fetchone() is None:
            missing.append((lemma, freq, pos))
    conn.close()
    return sorted(missing, key=lambda x: -x[1])  # Sort by frequency desc
```

---

## 4. Step 4: Context Extraction

Pull example sentences from the source text for each missing word. Real sentences from books are more useful than synthetic LLM examples.

```python
def extract_context_sentences(text, word, max_sentences=3):
    sentences = re.split(r'(?<=[.!?])\s+', text)
    matching = [s.strip() for s in sentences if word.lower() in s.lower()]
    return matching[:max_sentences]
```

---

## 5. Step 5: DeepSeek API Definition Generation

### API Setup

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key="your-deepseek-key",
    base_url="https://api.deepseek.com/v1"
)
```

DeepSeek is OpenAI-compatible. Use `deepseek-chat` model (V3.2).

### Pricing

| Component | Cost per 1M tokens |
|-----------|-------------------|
| Input (cache miss) | $0.28 |
| Input (cache hit) | $0.028 |
| Output | $0.42 |

Cache is automatic and prefix-based. System prompt + few-shot examples (~800 tokens) are cached across consecutive requests at 10x cheaper rate.

### Per-Word Cost Estimate

- Cached system prompt: ~800 tokens @ $0.028/M = ~$0.00002
- Word-specific input: ~80 tokens @ $0.28/M = ~$0.00002
- Output: ~400 tokens @ $0.42/M = ~$0.00017
- **Total per word: ~$0.0002 (~$0.20 per 1,000 words)**

### Cost Per Novel

| Scenario | Missing Words | Cost |
|----------|--------------|------|
| First novel | ~3,000 | ~$0.63 |
| Second novel | ~2,000 | ~$0.42 |
| After 5 novels | ~1,000 | ~$0.21 |
| After 10 novels | ~500 | ~$0.11 |
| After 20 novels | ~200 | ~$0.04 |

### System Prompt

```
You are a Czech-English lexicographer creating dictionary entries.
For each Czech word, produce a JSON entry with these fields:

{
  "lemma": "the dictionary form",
  "pos": "noun|verb|adjective|adverb|preposition|conjunction|pronoun|numeral|particle|interjection",
  "gender": "m_anim|m_inanim|f|n" (nouns only),
  "aspect": "imperfective|perfective|biaspectual" (verbs only),
  "aspect_pair": "the other aspect form" (verbs only),
  "senses": [
    {
      "definition_en": "English definition/translation",
      "register": "neutral|formal|informal|colloquial|vulgar|archaic|literary|technical",
      "domain": "general|legal|medical|cooking|sports|..." (if applicable),
      "examples": [
        {"cs": "Czech example sentence", "en": "English translation"}
      ],
      "synonyms_cs": ["syn1"],
      "see_also": ["related_word"]
    }
  ],
  "frequency": "common|moderate|uncommon|rare|archaic",
  "notes": "usage notes, false friends, learner pitfalls"
}

Rules:
1. At least one natural example sentence per sense.
2. For verbs, ALWAYS identify aspect and provide the aspect pair.
3. List senses from most common to least common.
4. Mark register accurately - most words are "neutral".
5. Flag false friends with English.
6. If unsure about any field, omit it rather than guessing.
7. Output ONLY valid JSON.
```

### Batch Processing

```python
async def generate_entry(word, pos_hint, context_sentences):
    user_content = f"Word: {word}"
    if pos_hint:
        user_content += f"\nPOS hint: {pos_hint}"
    if context_sentences:
        user_content += f"\nContext:\n" + "\n".join(f"- {s}" for s in context_sentences[:3])

    response = await client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + FEW_SHOT_EXAMPLES},
            {"role": "user", "content": user_content}
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=1000
    )
    return json.loads(response.choices[0].message.content)

async def batch_generate(missing_words, batch_size=10):
    results = {}
    for i in range(0, len(missing_words), batch_size):
        batch = missing_words[i:i+batch_size]
        tasks = [generate_entry(w, pos, ctx) for w, pos, ctx in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        for (word, _, _), result in zip(batch, batch_results):
            if not isinstance(result, Exception):
                results[word] = result
        await asyncio.sleep(0.5)  # Polite rate limiting
    return results
```

---

## 6. Step 6: Validation

### Automated Checks

1. **Schema validation**: Required fields present (lemma, pos, senses)
2. **Lemma match**: Generated lemma matches input word
3. **POS-specific fields**: Verbs must have aspect, nouns must have gender
4. **Language check**: English definitions don't contain Czech characters
5. **Example completeness**: Each sense has at least one example

### Cross-Referencing

Check generated entries against existing sources:
- Wiktionary (kaikki.org data) for the ~30% of words that have entries
- Existing dictionary entries from other sources
- If LLM says "klíč" means "hammer" but Wiktionary says "key" -> flag it

### Confidence Scoring

```python
def confidence_score(entry, freq_in_text, has_cross_ref):
    score = 0.5
    if freq_in_text > 50: score += 0.15
    elif freq_in_text > 10: score += 0.10
    if has_cross_ref == "exact": score += 0.25
    elif has_cross_ref == "partial": score += 0.10
    if len(entry.get("senses", [])) >= 2: score += 0.05
    # Schema validation
    valid, issues = validate_entry(entry)
    if valid: score += 0.10
    else: score -= 0.10 * len(issues)
    return min(1.0, max(0.0, score))
```

Entries with confidence < 0.6 go into the review queue.

### Multi-LLM Cross-Validation (Optional)

For low-confidence entries, query a second LLM (e.g., GPT-4o-mini at $0.15/$0.60 per M tokens) and compare. Agreement -> high confidence. Disagreement -> flag for review.

---

## 7. Step 7: Storage

Insert validated entries into SQLite. Track which texts have been processed. Record word encounter frequencies across texts.

The database grows monotonically -- each text processed adds entries, and the number of missing words per new text decreases over time following Zipf's law.

### Growth Trajectory

| Books Processed | Dict Size (lemmas) | Missing per New Book |
|----------------|-------------------|---------------------|
| 0 (base import) | ~60-80K | - |
| 1 | ~83K | ~3,000 |
| 5 | ~88K | ~1,000 |
| 10 | ~91K | ~500 |
| 20 | ~93K | ~200 |

Active Czech literary vocabulary is ~30-50K words. After 10-20 novels across genres, the dictionary covers the vast majority of words encountered in normal reading.

---

## 8. Step 8: Export

### StarDict Export

1. Generate tab-delimited file (headword \t HTML definition) for each lemma
2. For inflected forms: either generate .syn file or use StardictMergeSyns (recommended for performance)
3. Convert with pyglossary
4. Compress with dictzip

```bash
# Using pyglossary
pyglossary dictionary.tab czech-english.ifo --write-format=StardictMergeSyns
dictzip czech-english.dict
```

### Kindle Export

1. Generate XHTML with `<idx:entry>` blocks
2. Each lemma entry includes `<idx:iform>` for all inflected forms
3. Split into multiple ~250KB XHTML files
4. Generate OPF manifest
5. Compile with kindlegen

### Yomitan Export

1. Generate term_bank JSON files (arrays of 8-element tuples)
2. Include inflected forms as separate term entries pointing to lemma definitions
3. Create index.json with metadata
4. ZIP all files together

---

## 9. Alternative LLMs

| Provider | Model | Input/Output $/M | Czech Quality | Notes |
|----------|-------|------------------|---------------|-------|
| **DeepSeek** | V3.2 | $0.28/$0.42 | Excellent | Best value, proven by user |
| OpenAI | GPT-4o mini | $0.15/$0.60 | Very Good | Slightly cheaper input |
| Google | Gemini 2.0 Flash | $0.10/$0.40 | Good | Competitive pricing |
| Anthropic | Haiku 3.5 | $0.80/$4.00 | Very Good | 10x more expensive |

**Local models**: Qwen 2.5 72B is the best open-source option for multilingual tasks (needs ~40GB VRAM). At $0.63/novel for DeepSeek API, local inference ROI is negative unless processing millions of entries.

**Recommendation**: DeepSeek as primary. GPT-4o-mini as cross-validation for low-confidence entries.

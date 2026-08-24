# Task: screen candidate words from Czech subtitles

You are classifying harvested word forms for an offline Czech-English learner's
dictionary. One shard of the work is yours.

## Input

Your shard file is a JSON array of records:

```json
{"word": "cejtim", "freq": 12, "channels": 5, "pos_hint": "verb",
 "contexts": ["... a ja to cejtim taky ..."]}
```

`freq` is the corpus frequency, `channels` how many unrelated channels (or
shows) the form appears in -- a high channel count means the form survived the
dispersion filter and is very likely real. `contexts` are real subtitle lines.

## Output

Write a JSON object to your shard path with `.json` replaced by `.out.json`:

```json
{"results": [{"w": "<input word>", "c": "<category>", "lemma": "<headword>"}]}
```

One result per input word, same order, none skipped; `lemma` only when the
category is `word`. Then reply with just the number of verdicts written.

## Classification rules

{{RULES}}

## Note for broadcast (TV-show) subtitles

The rules above are written for YouTube auto-captions, where ASR noise
dominates. Show subtitles are human-typed, so `asr_error` is rare -- it covers
typos and OCR damage instead. The noise you will actually see is proper nouns,
Slovak dialogue (`foreign`), and period or dialect vocabulary, which is `word`.

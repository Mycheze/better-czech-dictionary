# Porting this dictionary to another language

This repo is a Czech-English dictionary, but nothing about the *design* is
Czech-specific: a SQLite source of truth, a bulk form->lemma table so inflected
forms resolve, LLM gap-filling from real texts, and three exporters. Two other
languages have been built this way already.

The practical route is **not** to fork the code and parameterise it -- the
per-language work is in the data sources and the grammar fields, not the
plumbing. It is faster to hand a coding agent a build prompt describing the
architecture and let it write the language's own version. Two worked examples:

- [`porting/dutch-dict-prompt.md`](porting/dutch-dict-prompt.md)
- [`porting/german-dict-prompt.md`](porting/german-dict-prompt.md)

Copy the closer one, swap in your language, and check it against the sections
below before starting.

## What has to be replaced

| Piece | Czech | What to find for your language |
|---|---|---|
| Definitions (primary) | kaikki.org Czech Wiktionary extract | kaikki.org publishes every language; same JSONL shape |
| Definitions (secondary) | Svobodne Slovniky (tab-separated) | FreeDict TEI, or any open bilingual word list; the parser changes, the import does not |
| Example sentences | Tatoeba `ces-eng` | Tatoeba pair for your language (`nld-eng`, `deu-eng`, ...) |
| Inflection table | MorfFlex CZ 2.1, 16.8M pairs | The hard one. Look for a national morphological database first; failing that, Wiktionary `forms` + a Hunspell/`.dic` expansion. Measure form coverage before committing |
| Runtime lemmatizer | Majka binary + `majka.w-lt` | Anything that maps surface form -> lemma from the CLI (simplemma, spaCy, a Hunspell stemmer). Only `lemmatize_with_majka()` in `processing/process_text.py:139` cares |

For a morphologically light language, Wiktionary's own `forms` field may be
enough on its own -- check before hunting for a MorfFlex equivalent.

## Grammar fields in the schema

`entries` carries `gender`, `aspect` and `aspect_pair` (see
[`ARCHITECTURE.md`](ARCHITECTURE.md)). Aspect is Slavic; drop it elsewhere.
Replace both with whatever a learner of your language actually needs -- Dutch
wants `article` (de/het) and `auxiliary` (hebben/zijn), German wants article
plus plural form and separability. Keep the rest of the schema as is; the
exporters only need `lemma`, `pos`, `entry_json` and the `inflections` table.

## Hardcoded Czech in the code

If you do fork rather than regenerate, these are the spots that assume Czech:

- Alphabet sets, duplicated per script: `processing/process_text.py:395`,
  `processing/process_books.py:79`, `processing/process_subs.py:138`,
  `processing/yt_word_filter.py:28-32`
- Lemmatizer paths: `processing/process_text.py:28-29`,
  `processing/process_subs.py:28-29`, `tools/test_dictionary.py:176-177`
- Example-sentence key `"cs"` in every exporter (`export_stardict.py:299`,
  `export_yomitan.py:95`, ...) and in the generation prompts
- Dictionary name / metadata: `DEFAULT_DICT_NAME` at the top of each exporter
- Every prompt: `processing/process_text.py:60-110` (few-shot entry writing),
  `processing/yt_word_filter.py:150+` (screening),
  `processing/prompts/*.md` (swarm briefs)

## Corpus expansion phases

The two example build prompts stop after book processing, which is enough for a
first working dictionary. The later phases are where the last few points of
coverage come from, and they port with more thought:

1. **Books** (`processing/process_books.py`) -- start here, works anywhere.
2. **YouTube auto-captions** (`download_yt_subs.py` -> `build_yt_corpus.py` ->
   `process_yt_corpus.py`). Two traps are language-independent: read captions
   as `json3` (VTT repeats every line and inflates counts ~3x) and restrict to
   videos whose *original* audio is your language, since a caption track on a
   foreign video is machine translation. You will need your own channel list
   (`data/youtube_channels.tsv`) and your own ASR failure modes in the
   screening prompt -- the Czech list of them is worthless for Dutch.
3. **TV-show subtitles** (`process_show_subs.py`). Human-typed, so the noise is
   proper nouns, neighbouring-language dialogue and dialect spellings rather
   than ASR garbage.

Noise filtering in both corpus phases is layered cheapest-first: orthography,
then **cross-source dispersion** (a real word turns up across many unrelated
channels or shows; garbage and in-jokes stay local), then a capitalisation test,
then the LLM. Dispersion is the load-bearing one and needs no language
knowledge -- keep it.

## Running the LLM work as an agent swarm

Screening ~20k candidates through a reasoning-model API is dominated by hidden
reasoning tokens. The alternative is to shard the work to a swarm of coding
agents: `processing/yt_swarm.py shard-screen` / `shard-gen` writes numbered
shard files plus an `INSTRUCTIONS.md` brief into the shard directory, one agent
handles one shard and writes `shard_NNN.out.json`, and `merge-screen` /
`merge-gen` fold the results into the same caches the API path uses. The two
paths are interchangeable and both resumable.

The briefs live in [`../processing/prompts/`](../processing/prompts):
`swarm_screen_words.md` (classification; the rules are pulled from
`yt_word_filter.SCREEN_SYSTEM_PROMPT` at shard time so there is one copy) and
`swarm_gen_entries.md` (entry writing). Rewrite both for your language --
especially the colloquial-forms section, which is what tells the model that
`bejt` is real Czech and not a typo.

`process_show_subs.py` deliberately has no API client at all: it exports
candidates to JSON and imports verdicts and entries back, so any model or swarm
can do the middle step.

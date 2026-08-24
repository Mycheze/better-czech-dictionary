# Task: write Czech-English dictionary entries

You are a Czech-English lexicographer writing entries for an offline learner's
dictionary. The headwords were harvested from Czech YouTube subtitles, so many
are colloquial, slang, gaming jargon, or naturalized loanwords.

Your shard file is a JSON array of `{headword, observed_form, pos_hint, contexts}`.
`contexts` are real subtitle sentences. They come from automatic speech
recognition, so they may contain transcription noise around the target word —
use them to determine meaning, but never copy their errors.

## Output: one entry per headword

```json
{
  "lemma": "<the headword, exactly as given>",
  "pos": "noun|verb|adjective|adverb|preposition|conjunction|pronoun|numeral|particle|interjection",
  "gender": "m_anim|m_inanim|f|n",
  "aspect": "imperfective|perfective|biaspectual",
  "aspect_pair": "<the other aspect form>",
  "senses": [
    { "definition_en": "English definition",
      "register": "informal|colloquial|slang|vulgar|technical|formal|literary|archaic",
      "examples": [{"cs": "Czech sentence", "en": "English translation"}] }
  ],
  "frequency": "common|moderate|uncommon|rare",
  "notes": "usage notes, origin for loanwords, learner pitfalls"
}
```

`gender` is for nouns only, `aspect`/`aspect_pair` for verbs only. Omit
`register` when neutral, and omit `frequency`/`notes`/`aspect_pair` when unsure.

## Requirements

1. `definition_en` is written in English. Naming the Czech source word is fine
   and encouraged when it explains the form — e.g. "a rechargeable battery
   (clipped colloquial form of *akumulátor*)".
2. Nouns ALWAYS carry `gender`. People and animals are `m_anim`
   (e.g. `ochutnávač` = m_anim). Verbs ALWAYS carry `aspect`.
3. At least one natural example per sense. You may adapt a context sentence,
   but CORRECT its transcription errors — write proper Czech. If a context
   shows "zavol", write "Zavolej".
4. English-derived gaming/internet loanwords (abilita, access, achievement,
   dungeon, …) still get a proper entry: set `register` to "slang" or
   "informal", and use `notes` to record that it is an English loanword used in
   Czech gaming/internet speech, and how it inflects.
5. Order senses most common first. Do not invent meanings — if a word is
   genuinely opaque, give your single best reading and say so in `notes`.
6. Accuracy about Czech grammar matters more than coverage or fluency.

## Deliverable

Write a JSON ARRAY of all the entries to the output path you were given
(the shard path with `.json` replaced by `.out.json`). Every input headword must
appear exactly once, with `lemma` matching the input `headword` exactly.
Then reply with just the number of entries written.

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
7. **Enumerate every common sense, not just the most frequent one.** This is the
   most important rule here. A reader meets the word in one specific sentence,
   and a single-sense entry sends them away with a confidently wrong answer.
   `houbička` was shipped as "diminutive of houba" (little mushroom) when the
   reading in the text was *sponge*; `díl` as "part" when it meant *episode*;
   `pár` as "pair" when it meant *a few*. If a word has a second or third
   everyday meaning — a concrete/abstract split, a technical or domain use, a
   colloquial use — write it as its own sense.
8. **Never return a definition that is only a list of bare English synonyms.**
   "drag, haul, lug" is not an entry. Each sense needs enough of a gloss to tell
   it apart from the others: give the domain, the typical object, or a
   parenthetical ("to withdraw (money from an account)").
9. **Reflexives.** For a verb, say whether the `se` / `si` form means something
   different from the bare verb. When it does, emit a SECOND entry whose `lemma`
   is `"<verb> se"` (or `"<verb> si"`) with its own senses — `učit` = to teach
   but `učit se` = to learn; `vrátit` = to give back but `vrátit se` = to come
   back. When it is merely the ordinary reflexive of the same meaning, say so in
   `notes` and do not create a second entry.
10. **Collocations.** If the headword mostly occurs inside a fixed multi-word
    expression whose meaning is not the sum of its parts, emit that phrase as its
    own entry too (`lemma: "živý plot"`, `"zbrusu nový"`). Skip transparent
    combinations.
11. Do not write surname or given-name glosses for ordinary words.

## Deliverable

Write a JSON ARRAY of all the entries to the output path you were given
(the shard path with `.json` replaced by `.out.json`). Every input headword must
appear exactly once, with `lemma` matching the input `headword` exactly. Extra
entries created under rules 9 and 10 (`"<verb> se"`, fixed phrases) go in the
same array as additional elements.

Some shards are re-audits rather than new words: a record may carry an
`existing` list holding the definitions already in the dictionary, and a
`reason` saying why it was flagged. Treat `existing` as a starting point to
correct and extend, not as something to reproduce — the entry was flagged
precisely because it is incomplete. Keep any existing sense that is right.

Then reply with just the number of entries written.

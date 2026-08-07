#!/usr/bin/env python3
"""
Noise filtering for words harvested from YouTube auto-generated captions.

YouTube's captions are ~95% accurate, but 5% of a 13-million-token corpus is
still hundreds of thousands of junk tokens. The junk falls into predictable
classes, and each gets its own defence:

  ASR mishearings / invented words  -> channel dispersion + orthography + LLM
  Proper nouns (names, brands)      -> capitalization ratio + LLM
  Foreign speech misheard as Czech  -> orthography (q/w/x, impossible clusters)
                                       + dispersion + LLM
  Fillers and stutters              -> repeated-character and vowel rules

The layers run cheapest-first so the LLM only ever sees words that already look
plausible, which keeps screening affordable.

This module is imported by process_yt_corpus.py; it has no side effects.
"""

import json
import re

# Czech uses q, w, x essentially only in unassimilated loanwords. Their presence
# is a strong (not decisive) signal of foreign material.
FOREIGN_LETTERS = set("qwx")

VOWELS = set("aáeéěiíoóuúůyý")
# Czech permits syllabic r and l ("vlk", "krk", "smrt"), and m in a few words.
SYLLABIC = set("rlm")

CZECH_LETTERS = set("aábcčdďeéěfghiíjklmnňoópqrřsštťuúůvwxyýzž")

REPEATED_CHAR = re.compile(r"(.)\1{2,}")       # 3+ identical letters in a row
# Consonant runs longer than this don't occur in Czech even with syllabic r/l.
MAX_CONSONANT_RUN = 5

# High-frequency English words that survive as "Czech-looking" tokens. Czech
# YouTubers code-switch constantly, so these appear across many channels and
# would otherwise defeat the dispersion filter.
ENGLISH_STOPWORDS = {
    "the", "and", "you", "for", "that", "this", "with", "are", "not", "but",
    "was", "have", "has", "will", "can", "your", "all", "from", "they", "been",
    "would", "there", "their", "what", "about", "which", "when", "one", "she",
    "her", "his", "him", "how", "out", "its", "than", "into", "some", "could",
    "them", "only", "come", "made", "after", "did", "should", "more", "these",
    "other", "may", "just", "also", "like", "get", "got", "make", "know",
    "think", "want", "need", "time", "people", "good", "very", "really",
    "because", "where", "here", "were", "then", "our", "who", "why", "how",
    "yes", "yeah", "okay", "guys", "video", "channel", "subscribe", "comment",
    "please", "thanks", "thank", "welcome", "today", "going", "let", "well",
    "see", "look", "way", "back", "day", "new", "now", "much", "many", "such",
    "over", "any", "even", "most", "take", "give", "say", "said", "does",
    "him", "she", "own", "same", "too", "off", "down", "before", "between",
    "never", "always", "every", "both", "each", "while", "during", "under",
}

# Platform/brand vocabulary that is neither Czech vocabulary nor worth defining.
PLATFORM_WORDS = {
    "youtube", "youtubu", "youtuber", "youtubers", "instagram", "instagramu",
    "facebook", "facebooku", "tiktok", "tiktoku", "twitch", "discord",
    "patreon", "spotify", "netflix", "google", "iphone", "android", "windows",
    "www", "com", "http", "https", "html", "url", "email", "gmail",
    "gt", "lt", "amp", "nbsp", "quot",
}

# Interjections and vocal noise the ASR transcribes literally.
FILLER_WORDS = {
    "hmm", "hm", "mhm", "mmm", "aha", "ehm", "eh", "ee", "eee", "aa", "aaa",
    "uh", "uhm", "oo", "ooo", "ii", "yy", "nn", "mm", "eeh", "hmh",
}


def longest_consonant_run(word):
    run = best = 0
    for ch in word:
        if ch.isalpha() and ch not in VOWELS:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def structural_verdict(word):
    """Cheap orthography check. Returns None if plausible, else a reason string."""
    w = word.lower()

    if len(w) < 2:
        return "too_short"
    if len(w) > 24:
        return "too_long"
    if not w.isalpha():
        return "non_alpha"

    # Characters outside the Czech alphabet (Cyrillic, Greek, emoji fallout...).
    if any(ch not in CZECH_LETTERS for ch in w):
        return "non_czech_charset"

    if w in FILLER_WORDS:
        return "filler"
    if w in ENGLISH_STOPWORDS:
        return "english"
    if w in PLATFORM_WORDS:
        return "platform"

    if REPEATED_CHAR.search(w):
        return "repeated_chars"

    # Must contain a vowel, or a syllabic consonant if it's short enough to be
    # a genuine vowel-less Czech word like "vlk", "smrt", "čtvrthrst".
    if not any(ch in VOWELS for ch in w):
        if not any(ch in SYLLABIC for ch in w) or len(w) > 8:
            return "no_vowel"

    if longest_consonant_run(w) > MAX_CONSONANT_RUN:
        return "consonant_cluster"

    if sum(1 for ch in w if ch in FOREIGN_LETTERS) >= 2:
        return "foreign_letters"

    return None


def looks_like_proper_noun(word, freq, caps, threshold=0.80):
    """True when the form is nearly always capitalized in the corpus.

    Sentence-initial position inflates the ratio for every word, so the
    threshold is set high; genuine common nouns land far below it.
    """
    if freq <= 0:
        return False
    return (caps / freq) >= threshold


# ---------------------------------------------------------------------------
# LLM screening
# ---------------------------------------------------------------------------

SCREEN_SYSTEM_PROMPT = """You screen candidate words harvested from \
auto-generated Czech YouTube subtitles. Automatic speech recognition produces \
misheard words, invented words, mangled foreign speech, and proper nouns \
alongside genuine Czech vocabulary.

Classify EVERY word you are given into exactly one category:

- "word"        a real Czech word a learner could meet again, including
                COLLOQUIAL and SPOKEN forms (see Common Czech below), slang,
                and loanwords genuinely used by Czech speakers
                ("vygooglit", "mejl", "lajk", "burger").
- "proper_noun" a name: person, place, company, brand, product, title, river,
                team, channel name.
- "foreign"     not Czech: English, Slovak, German, Polish, Russian, etc.,
                including foreign words never adapted into Czech.
- "asr_error"   misheard, garbled, merged, truncated, or non-existent; a
                misspelling or a word fragment.

COMMON CZECH (obecná čeština) IS REAL CZECH, NOT ERROR.
Spoken Czech on YouTube systematically differs from written Czech. These are
genuine words and must be classified "word":
  - -ej for -ý:            dobrej, mladej, novej, bejt, cejtit, vejš
  - -ýho/-ýmu for -ého:    dobrýho, malýmu, tohodle
  - -ý for -é:             dobrý (n.pl.), zaprvý, takový
  - demonstratives + -dle: tohodle, todleto, tydle, tadle, takovýdle, toleto
  - time/deixis variants:  teďka, teďko, teďkom, hnedka, todle, ject
  - prothetic v-:          vokno, von, vodejít, votevřít
  - dropped/changed vowels: pojď sem -> pocem, méně -> míň
  - interjections:         ježišmarja, hergot, sakra
If a form is a recognisable colloquial variant of a standard Czech word,
classify it "word" and set "lemma" to the DICTIONARY FORM OF THAT COLLOQUIAL
WORD'S STANDARD EQUIVALENT (e.g. "cejtím" -> lemma "cítit", "dobrýho" ->
lemma "dobrý", "tohodle" -> lemma "tenhle", "bejt" -> lemma "být").

ASR ERRORS TO REJECT.
The Czech ASR engine has systematic failure modes; these are NOT words:
  - a final -ý/-í collapsed to -j:  "kterj", "nějakj", "jakj", "dobrj"
  - truncated fragments:            "pů" (from "půl"), "abys" cut mid-word
  - two words fused:                "atakdále" written solid where wrong
  - phonetic nonsense with no plausible Czech meaning

Rules:
1. Be strict about invented words, but do NOT reject a form merely for being
   colloquial or non-standard. Common Czech is the main value of this corpus.
2. For every "word", give "lemma": the standard dictionary headword it belongs
   to (nominative singular for nouns, infinitive for verbs, masculine singular
   nominative for adjectives).
3. When genuinely unsure whether something is a real Czech word AND it does not
   fit a Common Czech pattern above, choose "asr_error".
4. Output ONLY a JSON object:
   {"results": [{"w": <input word>, "c": <category>,
                 "lemma": <standard headword, only when c is "word">}]}
5. Return exactly one result per input word, in the same order. Skip none."""


def build_screen_prompt(batch, contexts=None):
    """Render one screening batch. `batch` is a list of word strings."""
    lines = []
    for w in batch:
        if contexts and contexts.get(w):
            snippet = contexts[w][:130].replace("\n", " ")
            lines.append(f"{w}  ||  {snippet}")
        else:
            lines.append(w)
    header = ("Classify these Czech subtitle words. Each line is a word, "
              "optionally followed by '||' and a context snippet.\n\n")
    return header + "\n".join(lines)


def parse_screen_response(content, batch):
    """Parse the model's JSON reply into {word: (category, lemma)}."""
    out = {}
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return out

    results = data.get("results")
    if not isinstance(results, list):
        # Tolerate a bare list or a dict keyed by word.
        if isinstance(data, list):
            results = data
        else:
            return out

    valid = {"word", "proper_noun", "foreign", "asr_error"}
    batch_lower = {w.lower(): w for w in batch}
    for item in results:
        if not isinstance(item, dict):
            continue
        w = str(item.get("w", "")).strip().lower()
        if w not in batch_lower:
            continue
        cat = str(item.get("c", "")).strip()
        if cat not in valid:
            continue
        lemma = item.get("lemma")
        lemma = str(lemma).strip().lower() if lemma else None
        out[batch_lower[w]] = (cat, lemma)
    return out

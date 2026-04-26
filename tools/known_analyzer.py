#!/usr/bin/env python3
"""
Known words analyzer - compare your known vocabulary against a text

Usage: python3 known_analyzer.py <known_words_file> <text_file> <target_pct> <save_results>
    known_words_file: File with known words (one per line, may have header)
    text_file: Czech text to analyze
    target_pct: Target sentence coverage % (e.g., 98)
    save_results: true/false - whether to save results to file
"""

import sys
import subprocess
import re
from collections import Counter
from pathlib import Path
import json

def tokenize_text(text):
    """Simple tokenization - split on whitespace and punctuation"""
    tokens = []
    current_word = []
    
    # Czech and common punctuation to skip
    punctuation = '.,!?;:()[]{}""\'«»—–-„""‚''…'
    
    for char in text:
        if char.isspace():
            if current_word:
                tokens.append(''.join(current_word))
                current_word = []
        elif char in punctuation:
            if current_word:
                tokens.append(''.join(current_word))
                current_word = []
            # Skip punctuation entirely
        else:
            current_word.append(char)
    
    if current_word:
        tokens.append(''.join(current_word))
    
    return tokens

def run_majka(tokens, majka_path="./majka", dict_path="./majka.w-lt"):
    """Run majka parser on tokens and return parsed output"""
    input_text = '\n'.join(tokens)
    
    try:
        result = subprocess.run(
            [majka_path, "-f", dict_path],  # Remove -p flag
            input=input_text,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error running majka: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"majka not found at {majka_path}", file=sys.stderr)
        sys.exit(1)

def parse_majka_output(majka_output, original_tokens):
    """Parse majka output into lemmas, POS tags, and words
    
    Format without -p: one line per word, format is lemma:tag
    """
    lemmas = []
    pos_tags = []
    words = []
    
    lines = [l for l in majka_output.strip().split('\n') if l.strip()]
    
    line_idx = 0
    for token in original_tokens:
        word = token.lower()
        words.append(word)
        
        if line_idx < len(lines):
            line = lines[line_idx]
            line_idx += 1
            
            # Parse format: lemma:tag
            parts = line.split(':')
            if len(parts) >= 2:
                lemma = parts[0].lower()  # First part is lemma
                pos_tag = parts[1]        # Second part is tag
                lemmas.append(lemma)
                pos_tags.append(pos_tag)
            else:
                lemmas.append(word)
                pos_tags.append("UNKNOWN")
        else:
            # No majka output for this token
            lemmas.append(word)
            pos_tags.append("UNKNOWN")
    
    return lemmas, pos_tags, words

def load_known_words(filepath):
    """Load known words from file, skip header if present"""
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Skip header line if it looks like a header
    start_idx = 0
    if lines and ('word' in lines[0].lower() or 'lemma' in lines[0].lower() or lines[0].startswith('#')):
        start_idx = 1
    
    words = [line.strip() for line in lines[start_idx:] if line.strip()]
    return words

def split_into_sentences(text):
    """Split text into sentences based on sentence-ending punctuation"""
    # First split by sentence enders
    sentence_texts = re.split(r'[.!?]+', text)
    
    sentences = []
    for sent_text in sentence_texts:
        tokens = tokenize_text(sent_text)
        if tokens:  # Only add non-empty sentences
            sentences.append(tokens)
    
    return sentences

def calculate_sentence_stats(sentence_lemmas, sentence_words, known_set):
    """Calculate sentence statistics given known lemmas and words
    Checks both lemmas and word forms against known set"""
    stats = {
        '0T': 0,
        '1T': 0,
        '2T': 0,
        '3+T': 0
    }
    
    for lemmas, words in zip(sentence_lemmas, sentence_words):
        unknown_count = 0
        for lemma, word in zip(lemmas, words):
            # Check both lemma and word form
            if lemma not in known_set and word not in known_set:
                unknown_count += 1
        
        if unknown_count == 0:
            stats['0T'] += 1
        elif unknown_count == 1:
            stats['1T'] += 1
        elif unknown_count == 2:
            stats['2T'] += 1
        else:
            stats['3+T'] += 1
    
    total = len(sentence_lemmas)
    pct_0T = (stats['0T'] / total * 100) if total > 0 else 0
    pct_0T_1T = ((stats['0T'] + stats['1T']) / total * 100) if total > 0 else 0
    
    return stats, pct_0T, pct_0T_1T

def find_words_to_learn(sentence_lemmas, sentence_words, known_set, text_lemma_freq, target_pct):
    """
    Find which words to learn to reach target sentence coverage
    Uses efficient greedy algorithm
    """
    # Build set of all unknown lemmas
    all_lemmas_set = set()
    for sent in sentence_lemmas:
        all_lemmas_set.update(sent)
    
    unknown_lemmas = all_lemmas_set - known_set
    
    print(f"  Analyzing {len(unknown_lemmas)} unknown lemmas...")
    
    # Pre-calculate which sentences are 2T+ with current knowledge
    problem_sentences = []
    
    for sent_idx, (sent_lemmas, sent_words) in enumerate(zip(sentence_lemmas, sentence_words)):
        unknown_count = sum(
            1 for lemma, word in zip(sent_lemmas, sent_words) 
            if lemma not in known_set and word not in known_set
        )
        if unknown_count >= 2:
            # Store both lemmas and words for this sentence
            unknown_items = set()
            for lemma, word in zip(sent_lemmas, sent_words):
                if lemma not in known_set and word not in known_set:
                    unknown_items.add(lemma)  # Use lemma as the canonical form
            problem_sentences.append((sent_idx, unknown_items))
    
    print(f"  Found {len(problem_sentences)} sentences with 2+ unknown words")
    
    # Calculate impact: how many problem sentences does each word appear in?
    impact_scores = {}
    for lemma in unknown_lemmas:
        impact = sum(1 for _, unknowns in problem_sentences if lemma in unknowns)
        impact_scores[lemma] = impact
    
    # Sort by impact, then frequency
    sorted_unknowns = sorted(
        unknown_lemmas, 
        key=lambda l: (impact_scores[l], text_lemma_freq.get(l, 0)), 
        reverse=True
    )
    
    # Greedily add words until we hit target
    words_to_learn = []
    simulated_known = known_set.copy()
    
    for lemma in sorted_unknowns:
        simulated_known.add(lemma)
        impact = impact_scores[lemma]
        freq = text_lemma_freq.get(lemma, 0)
        words_to_learn.append((lemma, impact, freq))
        
        # Check every 10 words to save time
        if len(words_to_learn) % 10 == 0:
            _, _, pct_0T_1T = calculate_sentence_stats(sentence_lemmas, sentence_words, simulated_known)
            if pct_0T_1T >= target_pct:
                break
    
    # Final check
    _, _, final_pct = calculate_sentence_stats(sentence_lemmas, sentence_words, simulated_known)
    
    # Prepare full impact list for output
    all_impacts = [(l, impact_scores[l], text_lemma_freq.get(l, 0)) for l in sorted_unknowns]
    
    return words_to_learn, all_impacts

def main():
    if len(sys.argv) != 5:
        print("Usage: python3 known_analyzer.py <known_words_file> <text_file> <target_pct> <save_results>")
        print("  known_words_file: File with known words (one per line)")
        print("  text_file: Czech text to analyze")
        print("  target_pct: Target sentence coverage % (e.g., 98)")
        print("  save_results: true/false")
        sys.exit(1)
    
    known_file = sys.argv[1]
    text_file = sys.argv[2]
    target_pct = float(sys.argv[3])
    should_save = sys.argv[4].lower() == 'true'
    
    print(f"Loading known words from: {known_file}")
    known_words = load_known_words(known_file)
    print(f"Found {len(known_words)} known words")
    
    # Most words in the file are already lemmatized, so just lowercase and deduplicate
    print("Processing known words (lowercasing and deduplicating)...")
    known_lemmas_set = set(word.lower() for word in known_words)
    print(f"Unique known lemmas: {len(known_lemmas_set)}")
    
    # Show sample
    known_lemma_freq = Counter(word.lower() for word in known_words)
    print(f"\nTop 10 most frequent in your known words:")
    for lemma, count in known_lemma_freq.most_common(10):
        print(f"  {lemma}: {count} times")
    
    if max(known_lemma_freq.values()) > 5:
        print(f"\n⚠ Warning: Some words appear many times in your file. This might indicate duplicates.")
    
    # Load and process text
    print(f"\nLoading text from: {text_file}")
    with open(text_file, 'r', encoding='utf-8') as f:
        text = f.read()
    
    # Split into sentences first
    print("Splitting into sentences...")
    sentences_text = split_into_sentences(text)
    print(f"Total sentences: {len(sentences_text)}")
    
    # Tokenize and lemmatize all text
    print("Tokenizing all text...")
    all_tokens = []
    for sent in sentences_text:
        all_tokens.extend(sent)
    print(f"Text tokens: {len(all_tokens)}")
    
    print("Running majka on text...")
    text_majka = run_majka(all_tokens)
    
    print("Parsing lemmas...")
    text_lemmas, text_pos_tags, text_words = parse_majka_output(text_majka, all_tokens)
    text_lemma_freq = Counter(text_lemmas)
    
    print(f"Unique lemmas in text: {len(text_lemma_freq)}")
    
    # Filter out likely proper nouns (single-letter words, very short uncommon words)
    # and expand known set to include both lemmas and word forms
    print("\nExpanding known vocabulary...")
    expanded_known = known_lemmas_set.copy()
    
    # Add common function words that might be in inflected forms
    for word in known_words:
        expanded_known.add(word.lower())
    
    print(f"Expanded known set: {len(expanded_known)} items")
    
    # Filter proper nouns from text lemmas
    # Heuristic: single capital letters (A, V) or words with unusual tags
    proper_noun_indicators = []
    for i, (lemma, pos, word) in enumerate(zip(text_lemmas, text_pos_tags, text_words)):
        # Single letter (likely sentence-starting conjunction)
        if len(word) == 1 and word.upper() == all_tokens[i]:
            proper_noun_indicators.append(i)
        # Very short words that appear capitalized and are uncommon
        elif len(word) <= 2 and word[0].isupper() and text_lemma_freq[lemma] < 5:
            proper_noun_indicators.append(i)
    
    print(f"Identified {len(proper_noun_indicators)} potential proper nouns/artifacts")
    
    # Map sentences to lemmas
    sentence_lemmas = []
    sentence_words = []
    lemma_idx = 0
    for sent_tokens in sentences_text:
        sent_lemmas = []
        sent_words = []
        for _ in sent_tokens:
            if lemma_idx < len(text_lemmas):
                sent_lemmas.append(text_lemmas[lemma_idx])
                sent_words.append(text_words[lemma_idx])
                lemma_idx += 1
        sentence_lemmas.append(sent_lemmas)
        sentence_words.append(sent_words)
    
    # Calculate current coverage
    print("\n" + "="*90)
    print("CURRENT KNOWLEDGE ANALYSIS")
    print("="*90)
    
    # Check which known lemmas appear in text
    known_in_text = expanded_known & set(text_lemma_freq.keys())
    print(f"\nKnown lemmas that appear in text: {len(known_in_text)} / {len(expanded_known)}")
    
    # Show top known lemmas by frequency in text
    known_in_text_freq = {l: text_lemma_freq[l] for l in known_in_text}
    top_known = sorted(known_in_text_freq.items(), key=lambda x: x[1], reverse=True)[:10]
    print(f"\nTop 10 known lemmas by frequency in text:")
    for lemma, freq in top_known:
        print(f"  {lemma}: {freq} occurrences")
    
    stats, pct_0T, pct_0T_1T = calculate_sentence_stats(sentence_lemmas, sentence_words, expanded_known)
    
    total_words = len(text_lemmas)
    known_words_in_text = sum(
        1 for lemma, word in zip(text_lemmas, text_words) 
        if lemma in expanded_known or word in expanded_known
    )
    word_coverage = (known_words_in_text / total_words * 100) if total_words > 0 else 0
    
    print(f"\nWord Coverage:")
    print(f"  • Known words in text: {known_words_in_text:,} / {total_words:,}")
    print(f"  • Word coverage: {word_coverage:.2f}%")
    
    print(f"\nSentence Coverage:")
    print(f"  • 0T sentences (fully known): {stats['0T']:,} ({pct_0T:.1f}%)")
    print(f"  • 1T sentences (1 unknown): {stats['1T']:,} ({stats['1T']/len(sentence_lemmas)*100:.1f}%)")
    print(f"  • 2T sentences (2 unknown): {stats['2T']:,} ({stats['2T']/len(sentence_lemmas)*100:.1f}%)")
    print(f"  • 3+T sentences (3+ unknown): {stats['3+T']:,} ({stats['3+T']/len(sentence_lemmas)*100:.1f}%)")
    print(f"  • Total 0T+1T coverage: {pct_0T_1T:.2f}%")
    
    print(f"\n" + "="*90)
    print(f"WORDS TO LEARN FOR {target_pct}% SENTENCE COVERAGE")
    print("="*90)
    
    words_to_learn, all_impacts = find_words_to_learn(
        sentence_lemmas, sentence_words, expanded_known, text_lemma_freq, target_pct
    )
    
    words_to_save = []  # Initialize for later use
    
    if not words_to_learn:
        print(f"\n✓ You already have {pct_0T_1T:.1f}% coverage - target already met!")
    else:
        # Filter likely proper nouns from recommendations
        # Heuristics: Single capitals, very rare words with capital patterns
        filtered_words = []
        for lemma, impact, freq in words_to_learn:
            # Skip single capital letters
            if len(lemma) == 1 and lemma.isupper():
                continue
            # Skip words that look like proper nouns (capitalized and rare)
            if lemma[0].isupper() and freq < 10:
                continue
            filtered_words.append((lemma, impact, freq))
        
        print(f"\nYou need to learn {len(filtered_words)} more words to reach {target_pct}% coverage")
        print(f"(Filtered out {len(words_to_learn) - len(filtered_words)} likely proper nouns)")
        print(f"\nTop words to learn (ordered by impact):")
        print(f"\n{'Rank':<6} {'Lemma':<25} {'Impact':<10} {'Freq':<10}")
        print("-"*60)
        
        for rank, (lemma, impact, freq) in enumerate(filtered_words[:50], 1):
            print(f"{rank:<6} {lemma:<25} {impact:<10} {freq:<10}")
        
        if len(filtered_words) > 50:
            print(f"\n... and {len(filtered_words) - 50} more words")
        
        # Simulate final coverage using all words (including proper nouns)
        final_known = expanded_known | {w[0] for w in words_to_learn}
        final_stats, final_pct_0T, final_pct_0T_1T = calculate_sentence_stats(
            sentence_lemmas, sentence_words, final_known
        )
        
        final_word_coverage = sum(
            1 for l, w in zip(text_lemmas, text_words) 
            if l in final_known or w in final_known
        ) / total_words * 100
        
        print(f"\n" + "="*90)
        print("PROJECTED COVERAGE AFTER LEARNING")
        print("="*90)
        print(f"  • Word coverage: {final_word_coverage:.2f}%")
        print(f"  • 0T sentences: {final_stats['0T']:,} ({final_pct_0T:.1f}%)")
        print(f"  • 0T+1T sentences: {final_stats['0T'] + final_stats['1T']:,} ({final_pct_0T_1T:.1f}%)")
        
        # Store filtered_words for later use
        words_to_save = filtered_words
    
    # Save if requested
    if should_save:
        output_data = {
            'known_words_file': known_file,
            'text_file': text_file,
            'target_pct': target_pct,
            'current_coverage': {
                'known_lemmas': len(expanded_known),
                'word_coverage_pct': word_coverage,
                'sentence_0T_pct': pct_0T,
                'sentence_0T_1T_pct': pct_0T_1T,
                'sentence_stats': stats
            },
            'words_to_learn': [
                {'lemma': lemma, 'impact': impact, 'frequency': freq}
                for lemma, impact, freq in words_to_save
            ],
            'all_unknown_words_by_impact': [
                {'lemma': lemma, 'impact': impact, 'frequency': freq}
                for lemma, impact, freq in all_impacts[:200]  # Top 200
            ]
        }
        
        output_name = Path(text_file).stem + "_learning_plan.json"
        with open(output_name, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\nResults saved to: {output_name}")
    
    print(f"\n" + "="*90)
    print("Analysis complete!")
    print("="*90 + "\n")

if __name__ == "__main__":
    main()

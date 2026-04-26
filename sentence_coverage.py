#!/usr/bin/env python3
"""
Sentence coverage analysis for Czech texts
Analyzes how many words you need to know to understand X% of sentences

Usage: python3 sentence_coverage_analysis.py <text_file> <save_results>
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
    
    for char in text:
        if char.isspace():
            if current_word:
                tokens.append(''.join(current_word))
                current_word = []
        elif char in '.,!?;:()[]{}""\'«»—–-':
            if current_word:
                tokens.append(''.join(current_word))
                current_word = []
            tokens.append(char)
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
            [majka_path, "-f", dict_path],
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
    """Parse majka output into structured data"""
    words = []
    lemmas = []
    
    lines = [l for l in majka_output.strip().split('\n') if l.strip()]
    
    line_idx = 0
    for token in original_tokens:
        # Skip pure punctuation tokens
        if token in '.,!?;:()[]{}""\'«»—–-':
            continue  # Completely ignore punctuation
        
        if line_idx < len(lines):
            line = lines[line_idx]
            line_idx += 1
            
            parts = line.split(':')
            if len(parts) >= 2:
                lemma_word = parts[0]
                words.append(token)
                lemmas.append(lemma_word)
            else:
                words.append(token)
                lemmas.append(token.lower())
        else:
            words.append(token)
            lemmas.append(token.lower())
    
    return words, lemmas

def split_into_sentences(tokens):
    """Split tokens into sentences based on sentence-ending punctuation"""
    sentences = []
    current_sentence = []
    
    for token in tokens:
        # Skip punctuation entirely
        if token in '.,!?;:()[]{}""\'«»—–-':
            if token in '.!?':
                # End of sentence
                if current_sentence:
                    sentences.append(current_sentence)
                    current_sentence = []
            continue
        
        current_sentence.append(token)
    
    # Add final sentence if it exists
    if current_sentence:
        sentences.append(current_sentence)
    
    return sentences

def calculate_sentence_coverage(sentences, lemmas_list, lemma_freq):
    """
    Calculate what % of words needed to achieve 90% coverage at 0T and 1T
    
    Returns dict with analysis results
    """
    # Create a mapping of word -> lemma for the text
    word_to_lemma = {}
    for word, lemma in zip(sentences, lemmas_list):
        # Flatten since sentences is list of lists
        pass
    
    # Get lemmas in frequency order
    sorted_lemmas = [lemma for lemma, count in lemma_freq.most_common()]
    total_unique_lemmas = len(sorted_lemmas)
    
    results = []
    
    # Test different vocabulary sizes
    for n_known in range(100, total_unique_lemmas + 1, 50):
        if n_known > total_unique_lemmas:
            n_known = total_unique_lemmas
        
        known_lemmas = set(sorted_lemmas[:n_known])
        
        # Count sentences by number of unknown words
        sentence_stats = {
            '0T': 0,  # All words known
            '1T': 0,  # 1 unknown word
            '2T': 0,  # 2 unknown words
            '3+T': 0  # 3+ unknown words
        }
        
        for sentence_words in sentences:
            unknown_count = sum(1 for word in sentence_words if word not in known_lemmas)
            
            if unknown_count == 0:
                sentence_stats['0T'] += 1
            elif unknown_count == 1:
                sentence_stats['1T'] += 1
            elif unknown_count == 2:
                sentence_stats['2T'] += 1
            else:
                sentence_stats['3+T'] += 1
        
        total_sentences = len(sentences)
        pct_0T = (sentence_stats['0T'] / total_sentences) * 100
        pct_0T_1T = ((sentence_stats['0T'] + sentence_stats['1T']) / total_sentences) * 100
        pct_known_vocab = (n_known / total_unique_lemmas) * 100
        
        results.append({
            'n_known_lemmas': n_known,
            'pct_vocab': pct_known_vocab,
            '0T_sentences': sentence_stats['0T'],
            '1T_sentences': sentence_stats['1T'],
            '2T_sentences': sentence_stats['2T'],
            '3+T_sentences': sentence_stats['3+T'],
            'pct_0T': pct_0T,
            'pct_0T_1T': pct_0T_1T
        })
    
    return results

def print_results_table(results, total_sentences):
    """Print formatted results table"""
    print(f"\n{'='*90}")
    print(f"{'SENTENCE COVERAGE ANALYSIS':^90}")
    print(f"{'='*90}")
    print(f"Total sentences: {total_sentences:,}")
    print(f"\n{'Known':<8} {'% of':<8} {'0T':<10} {'1T':<10} {'2T':<10} {'3+T':<10} {'%0T':<8} {'%0T+1T':<8}")
    print(f"{'Lemmas':<8} {'Vocab':<8} {'Sents':<10} {'Sents':<10} {'Sents':<10} {'Sents':<10} {'Cov':<8} {'Cov':<8}")
    print(f"{'-'*90}")
    
    for r in results:
        print(f"{r['n_known_lemmas']:<8} {r['pct_vocab']:<8.1f} "
              f"{r['0T_sentences']:<10} {r['1T_sentences']:<10} "
              f"{r['2T_sentences']:<10} {r['3+T_sentences']:<10} "
              f"{r['pct_0T']:<8.1f} {r['pct_0T_1T']:<8.1f}")

def find_threshold(results, target_pct=90.0):
    """Find the vocabulary size needed for target% sentence coverage"""
    for r in results:
        if r['pct_0T_1T'] >= target_pct:
            return r
    return results[-1] if results else None

def create_ascii_chart(results, metric='pct_0T_1T', max_width=50):
    """Create ASCII line chart showing coverage progression"""
    print(f"\n{'='*70}")
    print(f"{'Sentence Coverage by Vocabulary Size':^70}")
    print(f"{'='*70}\n")
    
    max_val = max(r[metric] for r in results)
    
    for r in results[::max(1, len(results)//20)]:  # Show ~20 data points
        bar_length = int((r[metric] / 100) * max_width)
        bar = '█' * bar_length
        print(f"{r['n_known_lemmas']:>5} lemmas │{bar} {r[metric]:.1f}%")

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 sentence_coverage_analysis.py <text_file> <save_results> [target_pct]")
        print("  text_file: Path to Czech text file")
        print("  save_results: true/false")
        print("  target_pct: Optional target % (default: 90, 95, 98)")
        sys.exit(1)
    
    input_file = sys.argv[1]
    should_save = sys.argv[2].lower() == 'true'
    target_pcts = [90, 95, 98] if len(sys.argv) < 4 else [float(sys.argv[3])]
    
    # Read input file
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        print(f"Error: File '{input_file}' not found", file=sys.stderr)
        sys.exit(1)
    
    print(f"Processing: {input_file}")
    print(f"File size: {len(text)} characters")
    
    # Tokenize
    print("\nTokenizing text...")
    tokens = tokenize_text(text)
    
    # Split into sentences
    print("Splitting into sentences...")
    sentences = split_into_sentences(tokens)
    print(f"Found {len(sentences)} sentences")
    
    # Get all non-punctuation tokens for majka
    all_words = []
    for sent in sentences:
        all_words.extend(sent)
    
    print(f"Total words (no punctuation): {len(all_words)}")
    
    # Run majka
    print("\nRunning majka parser...")
    majka_output = run_majka(all_words)
    
    print("Parsing output...")
    words, lemmas = parse_majka_output(majka_output, all_words)
    
    # Create lemma frequency
    lemma_freq = Counter(lemmas)
    print(f"Unique lemmas: {len(lemma_freq)}")
    
    # Map sentences to lemmas
    sentence_lemmas = []
    lemma_idx = 0
    for sent in sentences:
        sent_lemmas = []
        for _ in sent:
            if lemma_idx < len(lemmas):
                sent_lemmas.append(lemmas[lemma_idx])
                lemma_idx += 1
        sentence_lemmas.append(sent_lemmas)
    
    print("\nCalculating sentence coverage...")
    results = calculate_sentence_coverage(sentence_lemmas, lemmas, lemma_freq)
    
    # Display results
    print_results_table(results, len(sentences))
    
    # Find thresholds
    print(f"\n{'='*90}")
    print(f"{'KEY FINDINGS':^90}")
    print(f"{'='*90}")
    
    for target_pct in target_pcts:
        threshold = find_threshold(results, target_pct)
        if threshold:
            print(f"\nTo understand {target_pct:.0f}% of sentences at 0T or 1T level:")
            print(f"  • Need to know: {threshold['n_known_lemmas']:,} lemmas")
            print(f"  • This is: {threshold['pct_vocab']:.1f}% of unique vocabulary in text")
            print(f"  • Results in: {threshold['pct_0T']:.1f}% fully known sentences (0T)")
            print(f"  • Results in: {threshold['pct_0T_1T']:.1f}% known/1-unknown sentences (0T+1T)")
    
    print(f"{'='*90}")
    
    # Create visualization
    create_ascii_chart(results)
    
    # Save if requested
    if should_save:
        thresholds = {f"{pct}pct": find_threshold(results, pct) for pct in target_pcts}
        
        output_data = {
            'total_sentences': len(sentences),
            'total_words': len(all_words),
            'unique_lemmas': len(lemma_freq),
            'coverage_analysis': results,
            'thresholds': thresholds
        }
        
        output_name = Path(input_file).stem + "_sentence_coverage.json"
        with open(output_name, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\nResults saved to: {output_name}")
    
    print(f"\n{'='*90}")
    print("Analysis complete!")
    print(f"{'='*90}\n")

if __name__ == "__main__":
    main()

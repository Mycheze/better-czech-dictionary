#!/usr/bin/env python3
"""
Czech text parser and frequency analyzer
Usage: python3 parse_cs_txt.py <text_file> <save_results>
    text_file: Path to the Czech text file to analyze
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
    # Split on whitespace and keep punctuation as separate tokens
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
    # Majka expects one word per line
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
    """Parse majka output into structured data
    
    Majka output format is: lowercased_word:tag
    where the first part is the lemma and second is the morphological tag
    """
    words = []
    lemmas = []
    pos_tags = []
    
    lines = [l for l in majka_output.strip().split('\n') if l.strip()]
    
    # Handle mismatch in line counts
    line_idx = 0
    for token in original_tokens:
        # Skip pure punctuation tokens
        if token in '.,!?;:()[]{}""\'«»—–-':
            words.append(token)
            lemmas.append(token)
            pos_tags.append("PUNCT")
            continue
        
        if line_idx < len(lines):
            line = lines[line_idx]
            line_idx += 1
            
            # majka output format: lemma:tag
            parts = line.split(':')
            if len(parts) >= 2:
                lemma_word = parts[0]  # This is the lemmatized form
                tag = parts[1]  # This is the morphological tag
                
                words.append(token)  # Use original token to preserve case
                lemmas.append(lemma_word)  # Use majka's lemma
                pos_tags.append(tag)
            else:
                # Fallback
                words.append(token)
                lemmas.append(token.lower())
                pos_tags.append("UNKNOWN")
        else:
            # No more majka output
            words.append(token)
            lemmas.append(token.lower())
            pos_tags.append("UNKNOWN")
    
    return words, lemmas, pos_tags

def clean_text(text):
    """Basic text cleaning"""
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def analyze_frequencies(words, lemmas, pos_tags):
    """Generate frequency statistics"""
    stats = {
        'total_tokens': len(words),
        'unique_words': len(set(words)),
        'unique_lemmas': len(set(lemmas)),
        'word_freq': Counter(words),
        'lemma_freq': Counter(lemmas),
        'pos_freq': Counter(pos_tags)
    }
    return stats

def print_frequency_table(counter, title, top_n=30):
    """Print a formatted frequency table"""
    print(f"\n{'='*60}")
    print(f"{title:^60}")
    print(f"{'='*60}")
    print(f"{'Rank':<6} {'Item':<30} {'Count':<10} {'%':<10}")
    print(f"{'-'*60}")
    
    total = sum(counter.values())
    for rank, (item, count) in enumerate(counter.most_common(top_n), 1):
        percentage = (count / total) * 100
        print(f"{rank:<6} {item:<30} {count:<10} {percentage:>6.2f}%")

def create_ascii_bar_chart(counter, title, top_n=20, max_width=50):
    """Create an ASCII bar chart"""
    print(f"\n{'='*70}")
    print(f"{title:^70}")
    print(f"{'='*70}\n")
    
    items = counter.most_common(top_n)
    if not items:
        print("No data to display")
        return
    
    max_count = items[0][1]
    
    for item, count in items:
        bar_length = int((count / max_count) * max_width)
        bar = '█' * bar_length
        print(f"{item[:25]:<25} │{bar} {count}")

def save_results(stats, output_path):
    """Save results to JSON file"""
    # Convert Counter objects to dicts for JSON serialization
    output_data = {
        'total_tokens': stats['total_tokens'],
        'unique_words': stats['unique_words'],
        'unique_lemmas': stats['unique_lemmas'],
        'top_50_words': dict(stats['word_freq'].most_common(50)),
        'top_50_lemmas': dict(stats['lemma_freq'].most_common(50)),
        'pos_distribution': dict(stats['pos_freq'])
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"\nResults saved to: {output_path}")

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 parse_cs_txt.py <text_file> <save_results>")
        print("  text_file: Path to Czech text file")
        print("  save_results: true/false")
        sys.exit(1)
    
    input_file = sys.argv[1]
    should_save = sys.argv[2].lower() == 'true'
    
    # Read input file
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        print(f"Error: File '{input_file}' not found", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Processing: {input_file}")
    print(f"File size: {len(text)} characters")
    
    # Clean and tokenize
    text = clean_text(text)
    print("\nTokenizing text...")
    tokens = tokenize_text(text)
    print(f"Found {len(tokens)} tokens")
    
    print("Running majka parser...")
    majka_output = run_majka(tokens)
    
    print("Parsing output...")
    words, lemmas, pos_tags = parse_majka_output(majka_output, tokens)
    
    # Analyze
    print("Analyzing frequencies...")
    stats = analyze_frequencies(words, lemmas, pos_tags)
    
    # Display results
    print(f"\n{'='*60}")
    print(f"{'ANALYSIS SUMMARY':^60}")
    print(f"{'='*60}")
    print(f"Total tokens: {stats['total_tokens']:,}")
    print(f"Unique word forms: {stats['unique_words']:,}")
    print(f"Unique lemmas: {stats['unique_lemmas']:,}")
    print(f"Type-Token Ratio (words): {stats['unique_words']/stats['total_tokens']:.4f}")
    print(f"Type-Token Ratio (lemmas): {stats['unique_lemmas']/stats['total_tokens']:.4f}")
    
    # Visualizations
    create_ascii_bar_chart(stats['lemma_freq'], "Top 20 Lemmas by Frequency", top_n=20)
    create_ascii_bar_chart(stats['word_freq'], "Top 20 Word Forms by Frequency", top_n=20)
    create_ascii_bar_chart(stats['pos_freq'], "Part-of-Speech Distribution", top_n=15)
    
    # Detailed tables
    print_frequency_table(stats['lemma_freq'], "Top 30 Lemmas", top_n=30)
    print_frequency_table(stats['word_freq'], "Top 30 Word Forms", top_n=30)
    
    # Save if requested
    if should_save:
        output_name = Path(input_file).stem + "_analysis.json"
        save_results(stats, output_name)
    
    print(f"\n{'='*60}")
    print("Analysis complete!")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()

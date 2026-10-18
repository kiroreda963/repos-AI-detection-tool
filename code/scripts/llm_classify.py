#!/usr/bin/env python3
"""
Classify matches using LLM and propagate classifications to duplicates.
"""

import argparse
import csv
import subprocess
import sys
from pathlib import Path
import random


def load_results(csv_file):
    """Load results from CSV file."""
    results = []
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Convert string 'True'/'False' to boolean
                row['selected'] = row['selected'].lower() == 'true'
                row['id'] = int(row['id'])
                results.append(row)
    except FileNotFoundError:
        print(f"Error: Input file '{csv_file}' not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading input file: {e}", file=sys.stderr)
        sys.exit(1)

    return results


def call_llm(model, prompt, context):
    """Call LLM with the given prompt and context."""
    # Combine prompt with context
    full_input = f"{prompt}\n\n{context}"

    cmd = ['llm', '-m', model, full_input]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=60  # 60 second timeout
        )

        if result.returncode != 0:
            print(f"\nWarning: LLM command failed: {result.stderr}", file=sys.stderr)
            return ""

        return result.stdout.strip()

    except subprocess.TimeoutExpired:
        print("\nWarning: LLM command timed out", file=sys.stderr)
        return ""
    except FileNotFoundError:
        print("Error: 'llm' command not found. Please install llm CLI.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nWarning: Error calling LLM: {e}", file=sys.stderr)
        return ""


def print_progress_bar(current, total, bar_length=40):
    """Print a progress bar."""
    percent = float(current) / total
    filled_length = int(bar_length * percent)
    bar = '█' * filled_length + '░' * (bar_length - filled_length)

    sys.stdout.write(f'\r[{bar}] {current}/{total} ({percent*100:.1f}%)')
    sys.stdout.flush()


def classify_results(results, model, prompt, sample_size=None, show_output=False):
    """Classify selected results using LLM and propagate to duplicates."""
    # Get selected items
    selected = [r for r in results if r['selected']]

    # Sample if requested
    if sample_size is not None and sample_size < len(selected):
        print(f"Randomly sampling {sample_size} out of {len(selected)} selected items...")
        selected = random.sample(selected, sample_size)
        # Mark which ones we're classifying
        selected_ids = {r['id'] for r in selected}
    else:
        selected_ids = None

    total_selected = len(selected)
    print(f"Classifying {total_selected} selected item(s)...\n")

    # Dictionary to store classifications by ID
    classifications = {}
    classification_column = f'classification-{model}'

    # Classify selected items
    for i, result in enumerate(selected, 1):
        if not show_output:
            print_progress_bar(i, total_selected)

        # Call LLM
        classification = call_llm(model, prompt, result['match_with_context'])

        # Store classification
        result[classification_column] = classification
        classifications[result['id']] = classification

        # Show output if requested
        if show_output:
            print(f"\n{'='*80}")
            print(f"Item {i}/{total_selected} (ID: {result['id']})")
            print(f"File: {result['file']}")
            print(f"{'='*80}")
            print(f"\nContext:\n{result['match_with_context']}")
            print(f"\nClassification: {classification}")
            print(f"{'='*80}\n")

    if not show_output:
        print()  # New line after progress bar

    # Propagate classifications to duplicates (only if we classified all selected items)
    if selected_ids is None:
        print("Propagating classifications to duplicates...")

        for result in results:
            if not result['selected'] and result['duplicates']:
                # This is a non-selected duplicate
                # Find the selected item in this duplicate group
                duplicate_ids = [int(id_str) for id_str in result['duplicates'].split(',') if id_str]

                # Find which ID is selected
                for dup_id in duplicate_ids:
                    if dup_id in classifications:
                        result[classification_column] = classifications[dup_id]
                        break
            elif not result['selected']:
                # Non-selected item without duplicates info (shouldn't happen, but handle it)
                result[classification_column] = ""
    else:
        # When sampling, only add classification column to sampled items
        print("Skipping duplicate propagation (sample mode)...")
        for result in results:
            if result['id'] not in classifications:
                result[classification_column] = ""

    return results, classification_column


def write_results(results, output_file, classification_column):
    """Write classified results to CSV file."""
    try:
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['id', 'pattern', 'file', 'parent_directory',
                         'match_with_context', 'duplicates', 'selected', classification_column]
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            writer.writeheader()
            for result in results:
                writer.writerow(result)

        print(f"\nResults written to '{output_file}'")

        # Print statistics
        classified_count = sum(1 for r in results if r.get(classification_column, ''))
        print(f"  Total rows: {len(results)}")
        print(f"  Classified rows: {classified_count}")

    except Exception as e:
        print(f"Error writing output file: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Classify matches using LLM and propagate to duplicates'
    )
    parser.add_argument(
        'input_file',
        help='Input CSV file from ripgrep search script'
    )
    parser.add_argument(
        '-m', '--model',
        default='gpt-oss-20b-mlx',
        help='LLM model to use (default: gpt-oss)'
    )
    parser.add_argument(
        '-p', '--prompt',
        default='Read this markdown content and determine whether it instructs a coding agent NOT to advertise its presence (in commit messages, or code), or not. Output one of these classes, and one only: Forbids, Encourages, Unsure, Unrelated',
        help='Prompt to send to LLM for classification (default: classify as documentation/tutorial/reference)'
    )
    parser.add_argument(
        '-o', '--output',
        default='classified_results.csv',
        help='Output CSV file (default: classified_results.csv)'
    )
    parser.add_argument(
        '-n', '--sample',
        type=int,
        metavar='N',
        help='Classify only N random selected items and show results in output'
    )

    args = parser.parse_args()

    # Validate input file
    if not Path(args.input_file).exists():
        print(f"Error: Input file '{args.input_file}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # Load results
    print(f"Loading results from '{args.input_file}'...")
    results = load_results(args.input_file)
    print(f"Loaded {len(results)} result(s)")
    print(f"Using model: {args.model}")
    print(f"Using prompt: {args.prompt}")

    if args.sample:
        print(f"Sample mode: will classify {args.sample} random items\n")
    else:
        print()

    # Classify results
    results, classification_column = classify_results(
        results,
        args.model,
        args.prompt,
        sample_size=args.sample,
        show_output=args.sample is not None
    )

    # Write output
    write_results(results, args.output, classification_column)


if __name__ == '__main__':
    main()

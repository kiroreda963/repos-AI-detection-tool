#!/usr/bin/env python3
"""
Search for patterns from a CSV file using ripgrep and output results to CSV.
"""

import argparse
import csv
import subprocess
import sys
from pathlib import Path
import re


def load_patterns(csv_file):
    """Load patterns from CSV file."""
    patterns = []
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if row:  # Skip empty rows
                    patterns.append(row[0])  # Take first column
    except FileNotFoundError:
        print(f"Error: Pattern file '{csv_file}' not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading pattern file: {e}", file=sys.stderr)
        sys.exit(1)

    return patterns


def search_pattern(pattern, search_path, context):
    """Search for a pattern using ripgrep."""
    cmd = [
        'rg',
        pattern,
        search_path,
        '-g', '*.md',
        '-C', str(context * 2),
        '--json',  # JSON output for easier parsing,
        '-L', # follow symlinks
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )
        return result.stdout
    except FileNotFoundError:
        print("Error: ripgrep (rg) not found. Please install ripgrep.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error running ripgrep: {e}", file=sys.stderr)
        return ""


def parse_ripgrep_json(json_output, pattern, context):
    """Parse ripgrep JSON output and extract match information."""
    import json

    matches = []
    files = {}

    for line in json_output.strip().split('\n'):
        if not line:
            continue

        try:
            data = json.loads(line)
            msg_type = data.get('type')

            if msg_type == 'begin':
                current_file = data['data']['path']['text']
                files[current_file] = {'contexts': [], 'matches': []}

            elif msg_type == 'match':
                match_data = data['data']
                match_info = {
                    'line': match_data['lines']['text'].rstrip('\n'),
                    'line_number': match_data['line_number']
                }
                files[current_file]['matches'].append(match_info)

            elif msg_type == 'context':
                context_info = {
                    'line': data['data']['lines']['text'].rstrip('\n'),
                    'line_number': data['data']['line_number']
                }
                files[current_file]['contexts'].append(context_info)

        except json.JSONDecodeError:
            continue
        except Exception as e:
            print(f"Warning: Error parsing line: {e}", file=sys.stderr)
            continue

    # Now, for each file, associate contexts to matches
    for file_path, file_data in files.items():
        contexts = file_data['contexts']
        matches_list = file_data['matches']

        # Sort contexts by line_number
        contexts.sort(key=lambda x: x['line_number'])

        for match in matches_list:
            match_line_num = match['line_number']
            before = [c['line'] for c in contexts if c['line_number'] < match_line_num]
            after = [c['line'] for c in contexts if c['line_number'] > match_line_num]

            # Take last n before, first n after for context
            n = context
            before_selected = before[-n:] if len(before) > n else before
            after_selected = after[:n] if len(after) > n else after

            # For expanded, take 2*n
            expanded_before = before[-(2*n):] if len(before) > 2*n else before
            expanded_after = after[:(2*n)] if len(after) > 2*n else after

            context_text = '\n'.join(before_selected + [match['line']] + after_selected)
            expanded_context_text = '\n'.join(expanded_before + [match['line']] + expanded_after)

            file_path_obj = Path(file_path)
            parent_parent = file_path_obj.parent.parent.name if len(file_path_obj.parent.parts) > 0 else ''

            matches.append({
                'pattern': pattern,
                'file': file_path,
                'parent_directory': parent_parent,
                'context': context_text,
                'expanded_context': expanded_context_text
            })

    return matches


def find_duplicates(results):
    """Find duplicate matches based on match_with_context and mark them."""
    # Assign IDs to all results
    for i, result in enumerate(results):
        result['id'] = i + 1

    # Group by expanded_context
    content_groups = {}
    for result in results:
        content = result['expanded_context']
        if content not in content_groups:
            content_groups[content] = []
        content_groups[content].append(result)

    # Process duplicates
    for content, group in content_groups.items():
        if len(group) > 1:
            # Multiple items with same content - duplicates found
            duplicate_ids = [item['id'] for item in group]

            # Select the first one (could use other criteria)
            for i, item in enumerate(group):
                if i == 0:
                    # First item is selected
                    item['selected'] = True
                    item['duplicates'] = ','.join(map(str, duplicate_ids))
                else:
                    # Other items are not selected
                    item['selected'] = False
                    item['duplicates'] = ''
        else:
            # No duplicates - automatically selected
            group[0]['selected'] = True
            group[0]['duplicates'] = ''

    return results


def write_results(results, output_file):
    """Write results to CSV file."""
    if not results:
        print("No matches found.")
        return

    # Find and mark duplicates
    print("Checking for duplicates...")
    results = find_duplicates(results)

    # Count statistics
    selected_count = sum(1 for r in results if r['selected'])
    duplicate_groups = len(set(r['duplicates'] for r in results if r['duplicates']))

    try:
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['id', 'pattern', 'file', 'parent_directory', 'context', 'expanded_context', 'duplicates', 'selected']
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            writer.writeheader()
            for result in results:
                writer.writerow(result)

        print(f"Results written to '{output_file}'")
        print(f"  Total matches: {len(results)}")
        print(f"  Selected (unique): {selected_count}")
        print(f"  Duplicate groups: {duplicate_groups}")
    except Exception as e:
        print(f"Error writing output file: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Search for patterns using ripgrep and output to CSV'
    )
    parser.add_argument(
        'search_path',
        help='Path to search in'
    )
    parser.add_argument(
        '-p', '--patterns',
        default='../config/patterns/rg_patterns.csv',
        help='CSV file containing patterns (default: patterns.csv)'
    )
    parser.add_argument(
        '-c', '--context',
        type=int,
        default=2,
        help='Number of context lines (default: 2)'
    )
    parser.add_argument(
        '-o', '--output',
        default='results.csv',
        help='Output CSV file (default: results.csv)'
    )

    args = parser.parse_args()

    # Validate search path
    if not Path(args.search_path).exists():
        print(f"Error: Search path '{args.search_path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # Load patterns
    print(f"Loading patterns from '{args.patterns}'...")
    patterns = load_patterns(args.patterns)
    print(f"Loaded {len(patterns)} pattern(s)")

    # Search for each pattern
    all_results = []
    for i, pattern in enumerate(patterns, 1):
        print(f"Searching for pattern {i}/{len(patterns)}: {pattern}")
        json_output = search_pattern(pattern, args.search_path, args.context)

        if json_output:
            matches = parse_ripgrep_json(json_output, pattern, args.context)
            all_results.extend(matches)
            print(f"  Found {len(matches)} match(es)")

    # Write results
    write_results(all_results, args.output)


if __name__ == '__main__':
    main()

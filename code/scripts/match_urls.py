#!/usr/bin/env python3
import argparse
import csv
import re
from pathlib import Path

def normalize(s: str) -> str:
    """Normalize a string for comparison: lowercase, remove non-alphanumerics."""
    return re.sub(r'[^a-z0-9]', '', s.lower())

def best_match(name, urls):
    """Find the best match for a given name among GitHub URLs."""
    norm_name = normalize(name)
    for url in urls:
        repo_path = re.sub(r'https?://github\.com/', '', url, flags=re.IGNORECASE)
        if normalize(repo_path) in norm_name or norm_name in normalize(repo_path):
            return url
    return ''

def main():
    parser = argparse.ArgumentParser(description="Match directory names to GitHub URLs.")
    parser.add_argument("dir_csv", help="CSV file containing a 'dir' column or single column of directory names")
    parser.add_argument("url_csv", help="CSV file containing a 'github_url' column")
    parser.add_argument("-o", "--output", default="matched.csv", help="Output CSV file name")
    parser.add_argument("--no-blanks", action="store_true", help="Filter out rows with blank GitHub URLs")
    args = parser.parse_args()

    # Read dir list
    with open(args.dir_csv, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        if 'dir' in reader.fieldnames:
            dir_list = [row['dir'].strip() for row in reader if row.get('dir')]
        else:
            # Handle single-column CSV with no header
            f.seek(0)
            dir_list = [line.strip() for line in f if line.strip() and not line.lower().startswith('dir')]

    # Read URL list
    with open(args.url_csv, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        if 'github_url' not in reader.fieldnames:
            raise ValueError("The second CSV must contain a 'github_url' column.")
        urls = [row['github_url'].strip() for row in reader if row.get('github_url')]

    results = []
    for name in dir_list:
        match_url = best_match(name, urls)
        if match_url or not args.no_blanks:
            results.append({'dir': name, 'github_url': match_url})

    # Write output CSV
    output_path = Path(args.output)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['dir', 'github_url'])
        writer.writeheader()
        writer.writerows(results)

    print(f"✅ Done. Saved {len(results)} rows to {output_path}")

if __name__ == "__main__":
    main()

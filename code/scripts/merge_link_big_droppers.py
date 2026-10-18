#!/usr/bin/env python3
"""
Merge two CSV files on 'github_url', create directory, symlink paths, and save merged CSV.

This script:
1. Merges two CSV files using an inner join on the 'github_url' column.
2. Creates the specified directory.
3. For each row in the merged dataframe, creates a symbolic link in the directory
   named after the last element of the 'path' column, pointing to the full 'path'.
4. Saves the merged dataframe to a CSV file.
"""

import argparse
import os
import pandas as pd
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description='Merge CSVs on github_url, create directory with symlinks, and save merged CSV'
    )
    parser.add_argument('csv1', help='First CSV file')
    parser.add_argument('csv2', help='Second CSV file')
    parser.add_argument('directory', help='Directory to create and place symlinks')
    parser.add_argument(
        '-o', '--output',
        default='merged-droppers.csv',
        help='Output CSV file (default: merged-droppers.csv)'
    )

    args = parser.parse_args()

    # Validate inputs
    if not Path(args.csv1).exists():
        print(f"Error: CSV file '{args.csv1}' does not exist.", file=sys.stderr)
        sys.exit(1)
    if not Path(args.csv2).exists():
        print(f"Error: CSV file '{args.csv2}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # Read CSVs
    try:
        df1 = pd.read_csv(args.csv1)
        df2 = pd.read_csv(args.csv2)
    except Exception as e:
        print(f"Error reading CSV files: {e}", file=sys.stderr)
        sys.exit(1)

    # Check for required columns
    if 'github_url' not in df1.columns or 'github_url' not in df2.columns:
        print("Error: Both CSV files must contain 'github_url' column.", file=sys.stderr)
        sys.exit(1)
    if 'path' not in df1.columns and 'path' not in df2.columns:
        print("Error: at least one CSV files must contain 'path' column.", file=sys.stderr)
        sys.exit(1)

    # Merge on github_url (inner join)
    merged_df = pd.merge(df1, df2, on='github_url', how='inner')

    if merged_df.empty:
        print("No common 'github_url' entries found. Output will be empty.", file=sys.stderr)

    # Create directory
    dir_path = Path(args.directory)
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Error creating directory '{args.directory}': {e}", file=sys.stderr)
        sys.exit(1)

    # Create symlinks
    for _, row in merged_df.iterrows():
        full_path = Path(row['path']).resolve()
        if not full_path.exists():
            print(f"Warning: Path '{full_path}' does not exist. Skipping symlink.", file=sys.stderr)
            continue
        link_name = full_path.name
        link_path = dir_path / link_name
        try:
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()  # Remove existing link or file
            link_path.symlink_to(full_path)
        except Exception as e:
            print(f"Error creating symlink for '{full_path}': {e}", file=sys.stderr)

    # Save merged dataframe
    try:
        merged_df.to_csv(args.output, index=False)
        print(f"Merged data saved to '{args.output}'")
        print(f"Directory '{args.directory}' created with {len(merged_df)} symlinks.")
    except Exception as e:
        print(f"Error saving output CSV: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

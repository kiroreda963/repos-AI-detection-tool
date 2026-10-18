"""
Merge annotator-specific inspection files back into combined files.

Takes a directory of inspection_samples with per-annotator files (created with --by-annotator),
and merges them back so each entity has ratings from both annotators.

Usage:
    python merge_inspection_results.py <inspection_samples_dir>

Example:
    python merge_inspection_results.py ../temp/inspection_samples

Output CSVs are written to the same directory with "-merged" in the filename.
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd


def find_annotator_files(samples_dir: Path) -> Dict[str, List[Path]]:
    """
    Find all annotator-specific CSV files.
    Returns dict mapping entity_type -> list of annotator files
    Example: {'ai_commits_for_inspection': [path_alice.csv, path_bob.csv]}
    """
    files_by_type = defaultdict(list)

    for csv_file in samples_dir.glob("*.csv"):
        # Extract base name and annotator
        # e.g., "ai_commits_for_inspection_alice.csv" -> ("ai_commits_for_inspection", "alice")
        parts = csv_file.stem.rsplit("_", 1)
        if len(parts) == 2:
            entity_type = parts[0]
            annotator = parts[1]
            files_by_type[entity_type].append((annotator, csv_file))

    return files_by_type


def merge_files(samples_dir: Path):
    """
    Find all annotator-specific files and merge them.
    For each artifact, identifies the two judges who reviewed it and normalizes
    their data to J1_name, J1_notes, J1_verdict, J2_name, J2_notes, J2_verdict columns.
    """
    samples_dir = Path(samples_dir)

    if not samples_dir.exists():
        print(f"Error: {samples_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    # Find all annotator files grouped by entity type
    files_by_type = find_annotator_files(samples_dir)

    if not files_by_type:
        print("No annotator-specific files found", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(files_by_type)} entity types to merge")

    # Merge each entity type
    for entity_type in sorted(files_by_type.keys()):
        files = files_by_type[entity_type]
        annotators = [f[0] for f in files]

        print(f"\nMerging {entity_type}: {', '.join(sorted(annotators))}")

        # Load all files for this entity type
        dfs_data = {}
        for annotator, filepath in files:
            df = pd.read_csv(filepath)
            dfs_data[annotator] = df

        if len(dfs_data) < 2:
            print(f"Warning: {entity_type} has fewer than 2 annotators, skipping")
            continue

        # Get key column (first column)
        key_col = list(dfs_data.values())[0].columns[0]

        # Merge all annotator data on key column
        annotators_list = sorted(dfs_data.keys())
        merged = dfs_data[annotators_list[0]][[key_col, "notes", "verdict"]].copy()
        merged.columns = [
            key_col,
            f"{annotators_list[0]}_notes",
            f"{annotators_list[0]}_verdict",
        ]

        for annotator in annotators_list[1:]:
            df = dfs_data[annotator]
            to_merge = df[[key_col, "notes", "verdict"]].copy()
            to_merge.columns = [key_col, f"{annotator}_notes", f"{annotator}_verdict"]
            merged = pd.merge(merged, to_merge, on=key_col, how="outer")

        # For each row, identify which judges reviewed it and normalize to J1/J2
        result_rows = []

        for _, row in merged.iterrows():
            # Find which judges have non-null verdicts (indicating they reviewed this artifact)
            reviewing_judges = []
            for annotator in annotators_list:
                verdict_col = f"{annotator}_verdict"
                if verdict_col in row and pd.notna(row[verdict_col]):
                    reviewing_judges.append(annotator)

            # Should have exactly 2 judges
            if len(reviewing_judges) < 2:
                print(
                    f"Warning: {key_col}={row[key_col]} has {len(reviewing_judges)} judges, skipping"
                )
                continue

            # Sort judges alphabetically
            reviewing_judges.sort()
            judge1, judge2 = reviewing_judges[0], reviewing_judges[1]

            # Build result row with J1/J2 naming
            result_row = {key_col: row[key_col]}
            result_row["J1_name"] = judge1
            result_row["J1_notes"] = row[f"{judge1}_notes"]
            result_row["J1_verdict"] = row[f"{judge1}_verdict"]
            result_row["J2_name"] = judge2
            result_row["J2_notes"] = row[f"{judge2}_notes"]
            result_row["J2_verdict"] = row[f"{judge2}_verdict"]

            # Add any other columns from merged that aren't judge-specific
            for col in merged.columns:
                if col not in [key_col] and not any(
                    f"{ann}_" in col for ann in annotators_list
                ):
                    result_row[col] = row[col]

            result_rows.append(result_row)

        # Create result DataFrame
        result_df = pd.DataFrame(result_rows)

        # Reorder columns: key, J1_name, J1_notes, J1_verdict, J2_name, J2_notes, J2_verdict, rest
        first_cols = [
            key_col,
            "J1_name",
            "J1_notes",
            "J1_verdict",
            "J2_name",
            "J2_notes",
            "J2_verdict",
        ]
        other_cols = [c for c in result_df.columns if c not in first_cols]
        final_cols = first_cols + other_cols
        result_df = result_df[[c for c in final_cols if c in result_df.columns]]
	# Add a new column: True if J1_verdict and J2_verdict agree (ignoring whitespace and case)
        result_df['agreement'] = (result_df['J1_verdict'].str.strip().str.lower() == result_df['J2_verdict'].str.strip().str.lower())

        # Write output
        output_path = samples_dir / f"{entity_type}-merged.csv"
        result_df.to_csv(output_path, index=False)
        print(f"Wrote {len(result_df)} merged results to {output_path}")

    print(f"\nMerge complete. Files written to {samples_dir}/")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    samples_dir = sys.argv[1]
    merge_files(samples_dir)

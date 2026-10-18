#!/usr/bin/env python3
"""
Consolidate metrics script.

This script takes an existing metrics file (with github URLs), loads tool metrics and commit ratio data
from a base directory, matches project directories to github URLs using match_urls, and adds the
specified metrics to the existing metrics file, saving the result to a new CSV.

Usage:
    python consolidate_metrics.py <metric_file> <base_dir> <output_csv> [--specs SPEC_LIST]

Arguments:
    metric_file: Path to the existing metrics CSV file containing github URLs.
    base_dir: Directory containing subdirectories with tool_metrics.csv and ratios.csv files.
    output_csv: Output path for the consolidated metrics CSV.

Options:
    --specs: Comma-separated list of specs for commit ratios, each as 'name,type,metric'.
             Default: overall,overall,commit_ratio,overall,source_code,commit_ratio,since_adoption,overall,commit_ratio,since_adoption,source_code,commit_ratio
"""

import argparse
import pandas as pd
import os
from pathlib import Path
from reports.common import default_args, load_data_basic, load_and_aggregate_tool_metrics, load_from_report, match_projects_to_urls, set_input_path, write_dataframe, import_metric_file, find_and_select_report

# Default commit ratio specs: list of (name, type, metric)
DEFAULT_SPECS = [
    ("overall_r", "since_adoption", "overall", "commit_ratio"),
    ("code_r", "since_adoption", "source_code", "commit_ratio"),
    ("code_c", "since_adoption", "source_code", "commits"),
    ("documentation_r", "since_adoption", "documentation", "commit_ratio"),
    ("documentation_c", "since_adoption", "documentation", "commits"),
    ("configuration_r", "since_adoption", "configuration", "commit_ratio"),
    ("configuration_c", "since_adoption", "configuration", "commits"),
    ("files_r", "since_adoption", "overall", "files_ratio"),
    ("contributors_r", "since_adoption", "source_code", "contributor_ratio"),
    ("lines_added_r", "since_adoption", "overall", "added_lines_ratio"),
    ("lines_deleted_r", "since_adoption", "overall", "deleted_lines_ratio"),
    ("velocity_r", "since_adoption", "overall", "add_minus_del_ratio"),
    # Add more as needed
]

languages = ["JavaScript", "TypeScript", "Shell", "Text", "Python", "HTML", "TSX", "Go", "PLSQL", "Rust", "C", "SCSS", "Batchfile", "C++", "Java", "C#", "Ruby", "PowerShell", "Kotlin", "Vue", "Jupyter Notebook", "Swift", "Hack", "GraphQL", "Lua", "Assembly", "Starlark", "Svelte", "Dart", "Objective-C++", "Groovy", "Solidity", "fish", "R", "GLSL", "Perl", "EJS", "Scala", "Elixir", "Cuda", "Scheme", "Julia", "Vim Script", "Common Lisp", "XSLT", "Clojure", "F#", "Zig", "Haskell", "Fortran Free Form"]

PL_SPECS = [("code_r", "since_adoption", "source_code", "commit_ratio"), ("code_c", "since_adoption", "source_code", "commits")]

for l in languages:
    PL_SPECS.extend([
        (f"{l}_r", "since_adoption", l, "commit_ratio"),
        (f"{l}_c", "since_adoption", l, "commits"),
        #(f"{l}_f", "since_adoption", l, "files_ratio"),
        #(f"{l}_add", "since_adoption", l, "added_lines_ratio"),
        #(f"{l}_del", "since_adoption", l, "deleted_lines_ratio"),
        #(f"{l}_vel", "since_adoption", l, "add_minus_del_ratio"),
    ])


def extract_commit_ratios(df_ratios_list: list[pd.DataFrame], specs:tuple[str,str,str]):

    extracted = {}

    for proj_data in df_ratios_list:
        project_name = proj_data["project"][0]
        for (metric_name, name, type, metric) in specs:
            row = proj_data[(proj_data['name'] == name) & (proj_data['type'] == type)]
            if not row.empty:
                value = row[metric].iloc[0]
            else:
                value = pd.NA
            col_name = metric_name

            if col_name not in extracted:
                extracted[col_name] = {}

            extracted[col_name][project_name] = value

    print(f"\n=== Ratio Extraction complete. Created {len(extracted)} columns ===")
    print(f"Column names: {list(extracted.keys())}\n")

    # Convert to DataFrame
    print("Converting to DataFrame...")
    df_extracted = pd.DataFrame(extracted)

    df_extracted.index.name = 'project'
    df_extracted.reset_index(inplace=True)

    print(f"\n=== Final DataFrame ===")
    print(f"Shape: {df_extracted.shape}")
    print(f"Columns: {list(df_extracted.columns)}")
    print(f"Missing values: {df_extracted.isna().sum().sum()} total")
    print("=" * 50 + "\n")

    return df_extracted

def save_df_for_specs(df_metric, df_ratios, df_tool, df_matched, df_report, specs, partial_path):
    # Extract specified commit ratios
    ratios_extracted = extract_commit_ratios(df_ratios, specs)

    # Merge tool_df with ratios_extracted
    tool_ratios = pd.merge(df_tool, ratios_extracted, on='project', how='left')

    # Merge with df_matched to add github_url
    merged = pd.merge(tool_ratios, df_matched, on='project', how='left')

    # merged with data from adoption report
    merged = pd.merge(merged, df_report, on='github_url', how='inner')

    # Now merge with df_metric on github_url
    final_df = pd.merge(df_metric, merged, on='github_url', how='inner')
    write_dataframe(partial_path, final_df)
    print(f"Consolidated metrics saved to {partial_path}")

# needs the analysis report for recent commits ... to filter out noise

def main():
    parser = default_args("consolidate metrics for RQ3")
    parser.add_argument("--output-csv", default="consolidated_metrics", help="Output CSV base name (without extension) for consolidated metrics.")
    parser.add_argument("--metric-file", default="../data/projects-29-08.csv", help="Path where project metrics are, if needed")
    # parser.add_argument("--specs", default=','.join([f"{n},{t},{m}" for n,t,m in DEFAULT_SPECS]),
    #                    help="Comma-separated list of commit ratio specs as 'name,type,metric'.")
    args = parser.parse_args()
    set_input_path(args.input)

    # specs = parse_specs(args.specs)

    # Load full ratios
    df_ratios =  load_data_basic(args.input, filename="ratios.csv", in_subfolder=False)
    # this is to print the top languages and make a list
    if False:
        df = pd.concat(df_ratios, ignore_index=True)

        counts = df['type'].value_counts()
        counts.to_csv("pl_counts.csv")
        return 0



    # Load and normalize metrics file
    df_metric = pd.read_csv(args.metric_file)
    df_metric = import_metric_file(df_metric, args.metric_file)
    
    # Load adoption report
    report = find_and_select_report(Path(args.input))
    df_report = load_from_report(args, ['commit_use', 'tool_use', 'ignored_tools', 'since_adoption_commits', 'adoption_ratio'])

    if 'github_url' not in df_metric.columns:
        raise ValueError("Metrics file must contain 'github_url' column.")


    # Load tool metrics aggregated
    df_tool = load_and_aggregate_tool_metrics(args.input)
    print(df_tool.head(6))

    # Load matched data
    # matching should also take into account projects that are commit-only???
    df_matched = match_projects_to_urls(df_tool['project'], df_metric['github_url'].tolist())
    if df_matched.empty:
        print("No matches found.")
        return
    print(df_matched.head(6))

    save_df_for_specs(df_metric, df_ratios, df_tool, df_matched, df_report, DEFAULT_SPECS, f"rq3/{args.output_csv}_main.csv")
    save_df_for_specs(df_metric, df_ratios, df_tool, df_matched, df_report, PL_SPECS, f"rq3/{args.output_csv}_pl.csv")

    # Save to output
    #final_df.to_csv(args.output_csv, index=False)
    #print(f"Consolidated metrics saved to {args.output_csv}")

if __name__ == "__main__":
    main()

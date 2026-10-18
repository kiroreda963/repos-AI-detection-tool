import pandas as pd
import matplotlib.pyplot as plt
import math
import seaborn as sns
import numpy as np
import scipy.stats as stats
import sys
import os
import argparse
from pathlib import Path
from reports.common import generate_visualization_report, default_args, filter_df, set_input_path, read_dataframe, get_report_path

def to_deciles(df: pd.DataFrame, metric: str):
    edges = pd.qcut(df[metric], q=10, duplicates='drop', retbins=True)[1]
    df[f'{metric}_decile'] = pd.cut(df[metric], bins=edges, include_lowest=True, labels=False) + 1

def add_intensity_metric(df):
    file_metrics = ['total_files', 'total_lines', 'total_changes']
    for m in file_metrics:
        to_deciles(df, m)
    df['file_intensity'] = df['total_files_decile'] + df['total_lines_decile'] + df['total_changes_decile']
    s = df['file_intensity']
    df['file_intensity'] = (s - s.min()) / (s.max() - s.min())


def plot_correlation_heatmap(df: pd.DataFrame, metrics) -> plt.Figure:
    """Correlation heatmap of all metrics."""
    sns.set_style("whitegrid")
    fig, ax = plt.subplots(figsize=(8, 6))

    corr = df[metrics].corr()
    sns.heatmap(corr, annot=True, cmap="coolwarm", ax=ax, square=True)
    ax.set_title("Correlation Heatmap", fontsize=16, fontweight='bold')

    plt.tight_layout()
    return fig

def plot_violin_sep(df: pd.DataFrame, metrics, sharey=False, trim_outliers=True) -> plt.Figure:
    """
    Create violin plots for each metric in subplots.
    - Layout adapts automatically based on the number of metrics.
    - Optionally trims extreme outliers for clearer visualization.
    - Optionally shares the y-axis for comparison.
    """
    n = len(metrics)

    # Determine grid layout
    n_cols = math.ceil(math.sqrt(n))
    n_rows = math.ceil(n / n_cols)

    fig, axes = plt.subplots(
        n_rows, n_cols, figsize=(5 * n_cols, 5 * n_rows), sharey=sharey
    )
    axes = axes.flatten() if n > 1 else [axes]

    for i, metric in enumerate(metrics):
        data = df[metric].dropna()

        # Optionally trim outliers for visualization (1st–99th percentile)
        if trim_outliers and len(data) > 0:
            lower, upper = np.percentile(data, [1, 99])
            data = data[(data >= lower) & (data <= upper)]

        sns.violinplot(y=data, ax=axes[i], inner='quartile', cut=0)
        axes[i].set_title(metric, fontsize=13, weight='bold')
        axes[i].set_xlabel("")
        axes[i].set_ylabel("Value (log for Lines/Changes)")
        axes[i].grid(axis="y", linestyle="--", alpha=0.4)

    # Hide unused subplots
    for j in range(len(metrics), len(axes)):
        axes[j].set_visible(False)

    fig.suptitle("Comparison of Metric Distributions", fontsize=15, weight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.95])

    return fig

def plot_binned_histograms(df: pd.DataFrame, metrics, bins) -> plt.Figure:
    """Binned histograms showing percentage distribution for each metric."""
    sns.set_style("white")
    n = len(metrics)

    # Determine grid layout
    n_cols = math.ceil(math.sqrt(n))
    n_rows = math.ceil(n / n_cols)

    fig, axes = plt.subplots(
        n_rows, n_cols, figsize=(5 * n_cols, 5 * n_rows), sharey=True
    )
    axes = axes.flatten() if n > 1 else [axes]

    labels = bins_to_labels(bins)
    all_max = 0  # track the maximum percentage across all metrics

    # First pass: plot histograms and find global max
    for i, metric in enumerate(metrics):
        ax = axes[i]
        data = df[metric].dropna()

        # Bin and compute percentage per bin
        binned = pd.cut(data, bins=bins, labels=labels, right=False)
        counts = binned.value_counts().reindex(labels, fill_value=0)
        percentages = counts / counts.sum() * 100
        all_max = max(all_max, percentages.max())

        sns.barplot(x=percentages.index, y=percentages.values, ax=ax, palette="viridis")

        ax.set_title(metric, fontsize=16, fontweight='bold')
        ax.set_xlabel(metric.replace("_", " ").title(), fontsize=14)
        ax.set_ylabel("Percentage (%)", fontsize=14)
        ax.grid(axis="y", linestyle="--", alpha=0.4)

        ax.tick_params(axis='x', rotation=45, labelsize=12)
        ax.tick_params(axis='y', labelsize=12)

    # Second pass: apply a shared y-limit with a bit of headroom
    ymax = all_max * 1.15 if all_max > 0 else 1
    for ax in axes[:len(metrics)]:
        ax.set_ylim(0, ymax)

    # Hide unused subplots
    for j in range(len(metrics), len(axes)):
        axes[j].set_visible(False)

    plt.tight_layout()
    return fig

def plot_binned_histograms_by_bin(df: pd.DataFrame, metrics, bins) -> plt.Figure:
    """
    Creates one subplot per bin range.
    Each subplot compares the percentage of values from each metric that fall into that bin.
    """
    sns.set_style("white")

    labels = bins_to_labels(bins)
    n = len(labels)

    # Determine grid layout based on number of bins
    n_cols = math.ceil(math.sqrt(n))
    n_rows = math.ceil(n / n_cols)

    fig, axes = plt.subplots(
        n_rows, n_cols, figsize=(5 * n_cols, 5 * n_rows), sharey=True
    )
    axes = axes.flatten() if n > 1 else [axes]

    # Calculate percentage per bin for each metric
    percentages_df = pd.DataFrame(index=labels, columns=metrics)
    for metric in metrics:
        data = df[metric].dropna()
        binned = pd.cut(data, bins=bins, labels=labels, right=False)
        counts = binned.value_counts().reindex(labels, fill_value=0)
        percentages = counts / counts.sum() * 100
        percentages_df[metric] = percentages

    # Track global maximum for consistent y-axis
    all_max = percentages_df.max().max()

    # Plot one graph per bin
    for i, bin_label in enumerate(labels):
        if i >= len(axes):
            break
        ax = axes[i]
        sns.barplot(
            x=percentages_df.columns,
            y=percentages_df.loc[bin_label].values,
            ax=ax,
            palette="viridis"
        )
        ax.set_title(f"Bin: {bin_label}", fontsize=15, fontweight='bold')
        ax.set_xlabel("Metric", fontsize=13)
        ax.set_ylabel("Percentage (%)", fontsize=13)
        ax.set_ylim(0, all_max * 1.15 if all_max > 0 else 1)
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        ax.tick_params(axis='x', rotation=45, labelsize=11)
        ax.tick_params(axis='y', labelsize=11)

    # Hide unused subplots
    for j in range(len(labels), len(axes)):
        axes[j].set_visible(False)

    fig.suptitle("Distribution of Metrics Across Bins", fontsize=16, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.95])

    return fig

def bins_to_labels(bins):
    tuples = zip(bins[:-1],bins[1:])
    #tuples = list(tuples)[1:]
    return [f"{a}--{b}" for a,b in tuples]

def generate_report(df: pd.DataFrame, output_pdf: str):
    """Main function to generate the report."""
    add_intensity_metric(df)
    df = df[df['overall_r'] > 0]
    # Create figures
    figures = []
    captions = []
    fig_labels = []
    
    figures.append(plot_violin_sep(df,  ['total_files', 'total_lines', 'total_changes',  'file_intensity', 'overall_r']))
    captions.append("Comparative distributions of total files, lines of code, changes, and derived file intensity metric.")
    fig_labels.append("fig:file-metrics-violin-distributions")
    
    figures.append(plot_violin_sep(df,  ['overall_r', 'code_r', 'documentation_r',  'configuration_r', 'files_r',
            'contributors_r','lines_added_r', 'lines_deleted_r', 'velocity_r']))
    captions.append("Violin plots showing distributions of code, documentation, configuration, and contributor commit ratios.")
    fig_labels.append("fig:ratio-metrics-violin-distributions")
    
    figures.append(plot_binned_histograms(df,  ['overall_r', 'code_r', 'documentation_r',  'configuration_r', 'files_r',
            'contributors_r','lines_added_r', 'lines_deleted_r', 'velocity_r'], [0, 1, 5, 10, 20, 50, 100]))
    captions.append("Percentage distribution of projects across bins for each commit ratio metric, enabling comparative analysis of adoption intensities.")
    fig_labels.append("fig:ratio-binned-histograms-metrics")
    
    figures.append(plot_binned_histograms_by_bin(df,  ['overall_r', 'code_r', 'documentation_r',  'configuration_r', 'files_r',
            'contributors_r','lines_added_r', 'lines_deleted_r', 'velocity_r'], [0, 1, 5, 10, 20, 50, 100]))
    captions.append("Per-bin analysis showing metric distributions within each adoption intensity category.")
    fig_labels.append("fig:ratio-binned-histograms-bins")

    figures.append(plot_correlation_heatmap(df, ['total_files', 'total_lines', 'total_changes',  'file_intensity', 'overall_r']))
    captions.append("Pearson correlation heatmap of total files, lines, code changes, and intensity metrics.")
    fig_labels.append("fig:file-metrics-correlation-heatmap")

    # Generate PDF
    generate_visualization_report(figures, Path(output_pdf), captions=captions, labels=fig_labels)

languages = ["JavaScript", "TypeScript", "Go", "Rust", "Java", "Pascal", "HTML", "CSS", "JSON", "PHP", "Kotlin", "Swift", "Ruby", "Dart", "TSX", "Markdown"]

def generate_report_pl(df: pd.DataFrame, output_pdf: str):
    """Main function to generate the report."""
    # df = df[df['overall_r'] > 0]
    # Create figures
    figures = []
    lits = ["r", "f", "add", "del"]
    
    # Filter to only columns that exist in the dataframe
    available_cols = [f"{lg}_{lit}" for lit in lits for lg in languages if f"{lg}_{lit}" in df.columns]
    
    if not available_cols:
        print(f"Warning: No language columns found. Available columns: {df.columns.tolist()}")
        print(f"Expected pattern: {{language}}_{{lit}} where lit in {lits}")
        return
    
    print(f"Found {len(available_cols)} language metric columns")
    
    figures.append(plot_binned_histograms(df, available_cols, [1, 5, 10, 20, 50, 100]))
    for lit in lits:
        lit_cols = [f"{lg}_{lit}" for lg in languages if f"{lg}_{lit}" in df.columns]
        if lit_cols:
            figures.append(plot_binned_histograms_by_bin(df, lit_cols, [1, 5, 10, 20, 50, 100]))

    #figures.append(plot_correlation_heatmap(df, ['total_files', 'total_lines', 'total_changes',  'file_intensity', 'overall_r']))

    # Generate PDF
    generate_visualization_report(figures, Path(output_pdf))


if __name__ == "__main__":
    parser = default_args("Computes commit ratio statistics ")
    parser.add_argument('--main', action='store_true', default=False, help='Generate main report only')
    parser.add_argument('--pl', action='store_true', default=False, help='Generate programming language report only')

    args = parser.parse_args()
    
    # Set input path for common.py functions
    set_input_path(args.input)
    
    # Default: generate both if neither flag specified
    gen_main = args.main or (not args.main and not args.pl)
    gen_pl = args.pl or (not args.main and not args.pl)
    
    if gen_main:
        print("Generating main report...")
        try:
            df_main = read_dataframe("rq3/consolidated_metrics_main.csv")
            df_main = filter_df(df_main, args, "github_url")
            output_main = get_report_path("extra/commit_ratio_report_main.pdf")
            generate_report(df_main, str(output_main))
            print(f"✅ Main report generated")
        except Exception as e:
            print(f"❌ Error generating main report: {e}")
    
    if gen_pl:
        print("Generating programming language report...")
        try:
            df_pl = read_dataframe("rq3/consolidated_metrics_pl.csv")
            df_pl = filter_df(df_pl, args, "github_url")
            output_pl = get_report_path("extra/commit_ratio_report_pl.pdf")
            generate_report_pl(df_pl, str(output_pl))
            print(f"✅ Programming language report generated")
        except Exception as e:
            print(f"❌ Error generating PL report: {e}")

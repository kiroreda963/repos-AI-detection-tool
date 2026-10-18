import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import scipy.stats as stats
import argparse
from pathlib import Path
import pltpublish as pub
from reports.common import default_args, generate_visualization_report, load_and_aggregate_tool_metrics, load_from_report, set_input_path, write_file






def plot_pairplot_hex_2(df: pd.DataFrame) -> plt.Figure:
    """
    Pairwise relationships between key codebase metrics.
    Upper triangle: hexbin plots with correlation coefficients
    Lower triangle: scatter plots
    Diagonal: KDE distributions.
    Automatically applies log scaling where appropriate,
    but shows axis ticks as real (non-log) values.
    """
    sns.set_theme(style="white")
    # Select and rename columns for readability
    rename_map = {
        'total_files': 'Files',
        'total_lines': 'Lines of Code',
        'total_changes': 'Code Changes',
        'adoption_ratio': 'Commit Ratio'
    }
    pair_df = df[list(rename_map.keys())].rename(columns=rename_map).dropna()
    pair_df = pair_df[(pair_df > 0).all(axis=1)]  # Remove nonpositive values
    # --- 1️⃣ Smart log scaling decision ---
    # Decide which columns to log-transform if they span > 3 orders of magnitude
    use_log = []
    for col in pair_df.columns:
        rng = pair_df[col].max() / max(pair_df[col].min(), 1)
        use_log.append(rng > 1e3)  # log-scale if range > 1000×
    pair_df_scaled = pair_df.copy()
    for col, log_it in zip(pair_df.columns, use_log):
        if log_it:
            pair_df_scaled[col] = np.log10(pair_df[col])
    # Keep track of which are log scaled
    log_cols = {col for col, log_it in zip(pair_df.columns, use_log) if log_it}
    # --- 2️⃣ Plot grid ---
    g = sns.PairGrid(pair_df_scaled, diag_sharey=False)
    # Lower triangle: scatter plots
    g.map_lower(sns.scatterplot, alpha=0.4, edgecolor=None, s=15)
    # Upper triangle: hexbin plots with correlation
    def hexbin_with_corr(x, y, **kws):
        plt.hexbin(x, y, gridsize=30, cmap="viridis", mincnt=1)
        # Calculate correlation and add text
        corr = np.corrcoef(x, y)[0, 1]
        ax = plt.gca()
        ax.text(0.95, 0.95, f'r = {corr:.3f}',
                transform=ax.transAxes,
                fontsize=10,
                verticalalignment='top',
                horizontalalignment='right',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
    g.map_upper(hexbin_with_corr)
    # Diagonal: KDE plots
    g.map_diag(sns.kdeplot, fill=True, color="steelblue")
    # --- 3️⃣ Label formatting + axis ticks in real units ---
    for i, row in enumerate(g.axes):
        for j, ax in enumerate(row):
            if ax is None:
                continue
            x_var = pair_df.columns[j]
            y_var = pair_df.columns[i]
            if x_var in log_cols:
                ticks = ax.get_xticks()
                ax.set_xticklabels([f"{10**t:.0f}" if t > 0 else f"{10**t:.2g}" for t in ticks])
            if y_var in log_cols:
                ticks = ax.get_yticks()
                ax.set_yticklabels([f"{10**t:.0f}" if t > 0 else f"{10**t:.2g}" for t in ticks])
            xlabel = x_var + (" (log scale)" if x_var in log_cols else "")
            ylabel = y_var + (" (log scale)" if y_var in log_cols else "")
            ax.set_xlabel(xlabel, fontsize=8)
            ax.set_ylabel(ylabel, fontsize=8)
            ax.tick_params(axis='both', labelsize=7)
    #g.fig.suptitle(
    #    "Pairwise Relationships Between Codebase Metrics",
    #    y=1.03,
    #    fontsize=14,
    #    fontweight='bold'
    #)
    g.fig.tight_layout()
    return g.fig

def plot_binned_histograms(df: pd.DataFrame) -> plt.Figure:
    """Binned histograms for total_files, total_lines, total_changes."""
    sns.set_style("whitegrid")
    fig, axes = plt.subplots(3, 1, figsize=(14, 12))

    # Define bins
    bins_files = [1, 2, 3, 4, 5, 10, 20, float('inf')]
    labels_files = ['1', '2', '3', '4', '5-9', '10-20', '20+']

    bins_lines = [0, 10, 20, 50, 100, 500, 1000, 10000, float('inf')]
    labels_lines = ['0-10', '11-20', '21-50', '51-100', '101-500', '501-1000', '1001-10000', '10000+']

    bins_changes = [1, 2, 3, 4, 5, 10, 20, float('inf')]
    labels_changes = ['1', '2', '3', '4', '5-9', '10-20', '20+']

    metrics = ["total_files", "total_lines", "total_changes"]
    bins_list = [bins_files, bins_lines, bins_changes]
    labels_list = [labels_files, labels_lines, labels_changes]
    titles = ["Binned Distribution of Total Files per Project", "Binned Distribution of Total Lines per Project", "Binned Distribution of Total Changes per Project"]

    for i, (metric, bins, labels, title) in enumerate(zip(metrics, bins_list, labels_list, titles)):
        ax = axes[i]
        data = df[metric].dropna()
        binned = pd.cut(data, bins=bins, labels=labels, right=False)
        counts = binned.value_counts().reindex(labels, fill_value=0)
        percentages = (counts / counts.sum()) * 100
        sns.barplot(x=counts.index, y=percentages.values, ax=ax, palette="viridis")
        ax.set_title(title, fontsize=16, fontweight='bold')
        ax.set_xlabel(metric.replace("_", " ").title(), fontsize=14)
        ax.set_ylabel("Percentage (%)", fontsize=14)
        plt.xticks(rotation=45, fontsize=12)
        plt.yticks(fontsize=12)

    plt.tight_layout()
    return fig


def to_deciles(df: pd.DataFrame, metric: str):
    edges = pd.qcut(df[metric], q=10, duplicates='drop', retbins=True)[1]
    df[f'{metric}_decile'] = pd.cut(df[metric], bins=edges, include_lowest=True, labels=False) + 1


def generate_report(args: argparse.Namespace):
     """Main function to generate the report."""
     print("Loading and aggregating data...")
     report = load_from_report(args, ['adoption_ratio', 'since_adoption_commits'])
     df = load_and_aggregate_tool_metrics(args.input)
     df = pd.merge(df, report, on='project', how='inner')


     if df.empty:
         print("No data to process.")
         return

     file_metrics = ['total_files', 'total_lines', 'total_changes']
     print(f"Aggregated data for {len(df)} projects.")
     print(df.head())
     print("\nSummary Statistics:")
     print(df[file_metrics].describe())
     for m in file_metrics:
         to_deciles(df, m)
     df['file_intensity'] = df['total_files_decile'] + df['total_lines_decile'] + df['total_changes_decile']
     s = df['file_intensity']
     df['file_intensity'] = (s - s.min()) / (s.max() - s.min())


     # Save CSV
     write_file("rq2/file_metrics_aggregated.csv", df.to_csv(index=False))
     print(f"Aggregated data saved to rq2/file_metrics_aggregated.csv")

     # Create figures
     figures = []
     captions = []
     fig_labels = []
     #figures.append(plot_histograms(df))
     figures.append(plot_binned_histograms(df))
     captions.append("Binned analysis showing project distribution across intensity categories for files, lines, and changes.")
     fig_labels.append("fig:codebase-metrics-binned-distribution")
     #figures.append(plot_scatter(df))
     #figures.append(plot_pairplot(df))
     #figures.append(plot_pairplot_hex(df))
     #figures.append(plot_top_n(df))
     df = df[df['since_adoption_commits'] > 10]
     figures.append(plot_pairplot_hex_2(df))
     captions.append("Hexbin pairplot showing correlations (upper triangle), scatter relationships (lower triangle), and distributions (diagonal) of key metrics.")
     fig_labels.append("fig:codebase-metrics-pairplot")
     #df = df[df['since_adoption_commits'] > 20]
     #figures.append(plot_pairplot_hex_2(df))
     #df = df[df['since_adoption_commits'] > 50]
     #figures.append(plot_pairplot_hex_2(df))


     # Generate PDF
     generate_visualization_report(figures, "rq2/file_vs_commit_ratio.pdf", separate_files=True, captions=captions, labels=fig_labels)

# load matched.csv
# load analysis report
# add commit ratio
# add it to the pairwise relationship after filtering
# add correlations there
#

if __name__ == "__main__":
     pub.setup()
     parser = default_args("Computes basic adoption statistics ")

     args = parser.parse_args()
     set_input_path(args.input)

     generate_report(args)

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.pyplot import Figure
from matplotlib.ticker import PercentFormatter
from reports.common import (
    default_args,
    generate_visualization_report,
    read_dataframe,
    set_input_path,
)

style = "white"


def add_delta_metric(df, m):
    delta_m = f"delta_{m}"
    df[delta_m] = df[m] / df["overall_r"]
    return delta_m


def plot_binned_histogram_ref(
    df: pd.DataFrame,
    metrics: list[str],
    bins: list[float | int],
    labels: list[str],
    reference_metric: str = None,
    min_observations: int = 0,
    filter: bool = True,
    ratios=[],
):
    """
    Creates a binned histogram showing the distribution of ratio values
    across adoption categories. If a reference_metric is provided, displays
    the difference between each metric's distribution and the reference distribution.

    Parameters:
    -----------
    df : pd.DataFrame
        Input dataframe
    metrics : list[str]
        List of metric column names to analyze
    bins : list[float|int]
        Bin edges for categorization
    labels : list[str]
        Labels for each bin
    reference_metric : str, optional
        Metric to use as reference. Differences will be shown relative to this metric.
    min_observations : int, optional
        Minimum number of observations required for a metric to be included
    filter : bool
        Whether to filter out zero values
    ratios : list
        Alternative columns to check for > 0 if filter is False
    """
    # Calculate reference distribution if provided
    reference_dist = None

    if reference_metric not in metrics:
        raise ValueError(
            f"Reference metric '{reference_metric}' not found in metrics list"
        )

    if filter:
        ref_filtered = df[df[reference_metric].notna() & (df[reference_metric] > 0)]
    else:
        ref_filtered = df[df[reference_metric].notna()]
        if ratios:
            ref_idx = metrics.index(reference_metric)
            ref_filtered = ref_filtered[ref_filtered[ratios[ref_idx]] > 0]

    ref_binned = pd.cut(
        ref_filtered[reference_metric], bins=bins, labels=labels, right=False
    )
    reference_dist = ref_binned.value_counts(normalize=True).sort_index() * 100
    print(f"Reference metric: {reference_metric} ({len(ref_filtered)} observations)")
    print(f"Reference distribution:\n{reference_dist}\n")

    # Prepare data for plotting
    bin_counts = []
    metric_names = []
    valid_metrics = []

    for i, language in enumerate(metrics):
        # Skip the reference metric itself when showing differences
        if reference_dist is not None and language == reference_metric:
            print(f"{'=' * 50}")
            print(f"Metric: {language} (REFERENCE - skipped in plot)")
            continue

        metric = f"{language}_r"
        commits = f"{language}_c"

        # Filter data
        if filter:
            filtered_df = ref_filtered[
                ref_filtered[metric].notna() & (ref_filtered[metric] > 0)
            ]
            filtered_df = filtered_df[filtered_df[commits] > 10]
        else:
            filtered_df = ref_filtered[ref_filtered[metric].notna()]
            if ratios:
                filtered_df = filtered_df[filtered_df[ratios[i]] > 0]

        print(f"{'=' * 50}")
        print(f"Metric: {metric}")
        print(f"Observations: {filtered_df.shape[0]}")

        # Check if metric meets minimum observation threshold
        if len(filtered_df) < min_observations:
            print(
                f"SKIPPED: Below minimum threshold of {min_observations} observations\n"
            )
            continue

        # Clean metric name for display
        clean_name = metric.replace("_r", "").replace("_", " ").title()
        clean_name = f"{clean_name} ({len(filtered_df)})"

        valid_metrics.append(metric)

        # Bin the data
        binned = pd.cut(filtered_df[metric], bins=bins, labels=labels, right=False)
        counts = binned.value_counts(normalize=True).sort_index() * 100

        # Calculate counts for each label
        for label in labels:
            if reference_dist is not None:
                # Show difference from reference
                current_val = counts[label] if label in counts.index else 0
                ref_val = reference_dist[label] if label in reference_dist.index else 0
                bin_counts.append(current_val - ref_val)
            else:
                # Show absolute percentage
                bin_counts.append(counts[label] if label in counts.index else 0)

            metric_names.append(clean_name)

        print(f"Distribution:\n{counts}")
        if reference_dist is not None:
            print(f"Differences from reference:")
            for label in labels:
                current_val = counts[label] if label in counts.index else 0
                ref_val = reference_dist[label] if label in reference_dist.index else 0
                print(f"  {label}: {current_val - ref_val:+.2f}%")
        print()

    if not valid_metrics:
        print(f"No metrics met the minimum observation threshold of {min_observations}")
        return None

    # Create dataframe for plotting
    plot_df = pd.DataFrame(
        {
            "Metric": metric_names,
            "Adoption Category": labels * len(valid_metrics),
            "Percentage": bin_counts,
        }
    )

    # Create bar plot
    sns.set_style(style)
    fig, ax = plt.subplots(figsize=(14, 8))
    sns.barplot(
        data=plot_df,
        x="Metric",
        y="Percentage",
        hue="Adoption Category",
        palette="viridis",
        ax=ax,
    )

    # Adjust y-axis based on whether we're showing differences or absolute values
    if reference_dist is not None:
        # For differences, center around 0
        max_abs = max(
            abs(plot_df["Percentage"].min()), abs(plot_df["Percentage"].max())
        )
        ax.set_ylim(-max_abs * 1.1, max_abs * 1.1)
        ax.axhline(y=0, color="black", linestyle="-", linewidth=0.8, alpha=0.5)
        ax.set_ylabel("Difference from Reference (%)", fontsize=14)
        title = f"Distribution Differences from Reference Metric: {reference_metric.replace('_', ' ').title()}"
    else:
        ax.set_ylim(0, 100)
        ax.set_ylabel("Percentage of Projects (%)", fontsize=14)
        title = "Distribution of Ratio Metrics by Adoption Category"

    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
    ax.set_title(title, fontsize=18, fontweight="bold")
    ax.set_xlabel("Metric Category", fontsize=14)
    plt.xticks(rotation=45, ha="right", fontsize=12)
    plt.yticks(fontsize=12)
    ax.legend(title="Adoption Category", fontsize=11, title_fontsize=12)
    plt.tight_layout()

    return fig


if __name__ == "__main__":
    """Main function to find data, generate plots, and create a report."""
    parser = default_args("Analyse commit ratios")
    parser.add_argument(
        "--input-file",
        default="consolidated_metrics_main.csv",
        help="Input CSV file name in rq3/ directory",
    )
    args = parser.parse_args()

    set_input_path(args.input)
    df = read_dataframe(f"rq3/{args.input_file}")
    df = df[df["commit_use"]]
    df = df[df["overall_r"] > 0]
    print(df.head())

    # Metrics to plot
    metrics = [
        "overall_r",
        "code_r",
        "documentation_r",
        "configuration_r",
        "files_r",
        "contributors_r",
    ]
    metrics = [
        "overall_r",
        "code",
        "documentation",
        "configuration",
    ]  # 'files_r', 'contributors_r']

    # deltas = []
    # for m in ['code_r', 'documentation_r', 'configuration_r']:
    #    deltas.append(add_delta_metric(df, m))

    # Generate PDF report
    output_path = "rq3/ratio_category_report.pdf"
    figs = []
    captions = []
    fig_labels = []
    for min in [0, 10, 20, 50]:
        df = df[df["since_adoption_commits"] > min]
        print("****************** shape is: ", df.shape)

        # figs.append(plot_ratio_categories(df, metrics))
        # figs.append(plot_ratio_categories(df, deltas, ylims_pos=False))
        bins = [0, 1, 5, 20, 100]
        labels = [
            "Experimental\n(0-1%)",
            "Limited\n(1-5%)",
            "Consistent\n(5-20%)",
            "Pervasive\n(20%+)",
        ]
        fig = plot_binned_histogram_ref(
            df,
            metrics,
            reference_metric="overall_r",
            min_observations=25,
            bins=bins,
            labels=labels,
        )
        if fig:
            figs.append(fig)
            captions.append(
                f"Distribution of overall, code, documentation, and configuration commit ratios stratified by adoption intensity categories (minimum {min} commits since adoption)."
            )
            fig_labels.append(f"fig:commit-ratio-category-comparison-{min}commits")
        # figs.append(plot_binned_histogram_by_category(df, metrics))
        # figs.append(plot_binned_histogram(df, deltas))
        # figs.append(plot_contributors_ratio_by_count(df))

    # Generate PDF report
    generate_visualization_report(
        figs, output_path, captions=captions, labels=fig_labels
    )

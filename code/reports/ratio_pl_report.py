from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
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
        ref_filtered = df[df[reference_metric].notna()]  # & (df[reference_metric] > 0)]
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
                ref_filtered[metric].notna()
            ]  # & (ref_filtered[metric] > 0)]
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


def add_delta_metric(df, m):
    delta_m = f"delta_{m}"
    df[delta_m] = ((df[m] - df["code_r"]) / df["code_r"]) * 100
    return delta_m


from math import ceil

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns



def horizontal_box_jitter_metrics_ref(
    df,
    metrics,
    min_obs=10,
    max_points=None,
    figsize=(12, 8),
    random_state=42,
    reference_metric=None,
    min_commits=10,
):
    from math import ceil

    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns

    ref_stats = None
    if reference_metric is not None:
        ref_metric = f"{reference_metric}_r"
        ref_commits = (
            "since_adoption_commits"
            if reference_metric == "Overall"
            else f"{reference_metric}_c"
        )

        ref_data = df.loc[
            (df[ref_commits] > min_commits) & (df[ref_metric] > 0), ref_metric
        ]

        if len(ref_data) >= min_obs:
            ref_stats = {
                "median": ref_data.median(),
                "q1": ref_data.quantile(0.25),
                "q3": ref_data.quantile(0.75),
            }
            print("====================REF STATS===================")
            print(ref_stats)

    filtered_metrics = []
    plot_data = []
    stats = {}
    n_obs = {}

    for language in metrics:
        metric = f"{language}_r"
        commits = f"{language}_c"

        data = df.loc[(df[commits] > min_commits) & (df[metric] > 0), [metric]].copy()
        data.rename(columns={metric: "value"}, inplace=True)
        data["metric"] = language

        if len(data) < min_obs:
            continue

        n_obs[language] = len(data)

        stats[language] = {
            "median": data["value"].median(),
            "q1": data["value"].quantile(0.25),
            "q3": data["value"].quantile(0.75),
        }

        if max_points is not None and len(data) > max_points:
            data = data.sample(n=max_points, random_state=random_state)

        plot_data.append(data)
        filtered_metrics.append(language)

    if len(plot_data) == 0:
        return None

    filtered_metrics.sort(key=lambda x: stats[x]["median"], reverse=True)

    plot_df = pd.concat(plot_data, ignore_index=True)

    half = ceil(len(filtered_metrics) / 2)
    left_metrics = filtered_metrics[:half]
    right_metrics = filtered_metrics[half:]

    fig, axes = plt.subplots(1, 2, figsize=figsize, sharey=False)

    for ax, metrics_subset in zip(axes, [left_metrics, right_metrics]):
        subset_df = plot_df[plot_df["metric"].isin(metrics_subset)]

        if ref_stats is not None:
            ax.axvline(ref_stats["median"], color="red", linewidth=1)
            ax.axvline(ref_stats["q1"], linestyle=":", color="red", alpha=0.6)
            ax.axvline(ref_stats["q3"], linestyle=":", color="red", alpha=0.6)

        sns.boxplot(
            data=subset_df,
            y="metric",
            x="value",
            order=metrics_subset,
            orient="h",
            ax=ax,
            width=0.5,
            fliersize=0,
            color="white",
            boxprops=dict(edgecolor="black"),
            medianprops=dict(color="black", linewidth=2),
        )

        sns.stripplot(
            data=subset_df,
            y="metric",
            x="value",
            order=metrics_subset,
            orient="h",
            ax=ax,
            color="steelblue",
            size=2,
            alpha=0.45,
            jitter=0.25,
        )

        labels = [f"{m} ({n_obs[m]})" for m in metrics_subset]
        ax.set_yticklabels(labels)

        ax.set_xlabel("Percentage")
        ax.set_ylabel("")
        ax.grid(axis="x", linestyle="--", alpha=0.4)
        ax.set_xlim(0, 100)

    plt.tight_layout()
    return fig



def expand_topics_to_columns(
    df,
    topics_column="topics",
    value_column="adoption_ratio",
    commits_column="since_adoption_commits",
    min_frequency=10,
    exclude_topics=None,
):
    """
    Expand semicolon-separated topics into individual columns.

    Parameters
    ----------
    df : pd.DataFrame
        The dataframe containing the topics column
    topics_column : str
        Name of the column containing semicolon-separated topics
    value_column : str
        Name of the column whose value should be copied to <topic>_r columns
    commits_column : str
        Name of the column whose value should be copied to <topic>_c columns
    min_frequency : int
        Minimum number of occurrences for a topic to get its own column
    exclude_topics : list, optional
        List of topic names to exclude from column creation

    Returns
    -------
    list
        List of topic names (without _r or _c suffix) that were added
    """
    if exclude_topics is None:
        exclude_topics = []

    # Count topic frequencies
    topic_counts = {}
    for topics_str in df[topics_column].dropna():
        topics = [t.strip() for t in str(topics_str).split(";")]
        for topic in topics:
            if topic:  # Skip empty strings
                topic_counts[topic] = topic_counts.get(topic, 0) + 1

    # Filter topics by frequency and exclusion list
    valid_topics = [
        topic
        for topic, count in topic_counts.items()
        if count >= min_frequency and topic not in exclude_topics
    ]

    # Create columns for valid topics
    added_topics = []
    for topic in valid_topics:
        # Create _r column with adoption_ratio values
        r_col = f"{topic}_r"
        df[r_col] = df.apply(
            lambda row: (
                row[value_column]
                if pd.notna(row[topics_column])
                and topic in str(row[topics_column]).split(";")
                else None
            ),
            axis=1,
        )

        # Create _c column with since_adoption_commits values
        c_col = f"{topic}_c"
        df[c_col] = df.apply(
            lambda row: (
                row[commits_column]
                if pd.notna(row[topics_column])
                and topic in str(row[topics_column]).split(";")
                else None
            ),
            axis=1,
        )

        added_topics.append(topic)

    return added_topics


def trials(figs, df, selection):
    df3 = df.copy()
    figs.append(
        horizontal_box_jitter_metrics_ref(
            df3,
            selection,
            figsize=(12, 6),
            max_points=500,
            min_obs=15,
            reference_metric="code",
            min_commits=10,
        )
    )


if __name__ == "__main__":
    """Main function to find data, generate plots, and create a report."""
    parser = default_args("Analyse commit ratios")
    parser.add_argument(
        "--input-file",
        default="consolidated_metrics_pl.csv",
        help="Input CSV file name in rq3/ directory",
    )
    args = parser.parse_args()

    set_input_path(args.input)
    df = read_dataframe(f"rq3/{args.input_file}")
    df = df[df["commit_use"]]
    df = df[df["adoption_ratio"] > 0]
    df["Overall_r"] = df["adoption_ratio"]
    print(df.head())

    languages = [
        "JavaScript",
        "TypeScript",
        "Shell",
        "Python",
        "HTML",
        "TSX",
        "Go",
        "PLSQL",
        "Rust",
        "C",
        "SCSS",
        "Batchfile",
        "C++",
        "Java",
        "C#",
        "Ruby",
        "PowerShell",
        "Kotlin",
        "Vue",
        "Jupyter Notebook",
        "Swift",
        "Hack",
        "GraphQL",
        "Lua",
        "Assembly",
        "Starlark",
        "Svelte",
        "Dart",
        "Objective-C++",
        "Groovy",
        "Solidity",
        "fish",
        "R",
        "GLSL",
        "Perl",
        "EJS",
        "Scala",
        "Elixir",
        "Cuda",
        "Scheme",
        "Julia",
        "Vim Script",
        "Common Lisp",
        "XSLT",
        "Clojure",
        "F#",
        "Zig",
        "Haskell",
        "Fortran Free Form",
    ]
    ratios = [f"{lg}_r" for lg in languages]

    deltas = []
    for m in ratios:
        deltas.append(add_delta_metric(df, m))

    # Generate PDF report
    output_path = "rq3/ratio_category_report.pdf"
    figs = []
    captions = []
    fig_labels = []

    n = 50
    chunk_num = 1
    for i in range(0, len(languages), n):
        just_ratios = ratios[i : i + n]
        chunk_ratios = ["Overall_r", "code_r"] + ratios[i : i + n]
        chunk_ratios = ["Overall_r"] + ratios[i : i + n]
        chunk_ratios_c = ["code_r"] + ratios[i : i + n]
        chunk_languages = ["Overall_r"] + languages[i : i + n]
        chunk_languages_c = ["code_r"] + languages[i : i + n]

        chunk_deltas = deltas[i : i + n]

        for min_commits in [10]:  # [0, 10, 20, 50]:
            df = df[df["since_adoption_commits"] > min_commits]
            print("****************** shape is: ", df.shape)
            # figs.append(plot_ratio_categories(df, ratios))
            # figs.append(plot_ratio_categories(df, deltas, ylims_pos=False))
            bins = [0, 1, 5, 20, 100]
            labels = [
                "Experimental\n(0-1%)",
                "Limited\n(1-5%)",
                "Consistent\n(5-20%)",
                "Pervasive\n(20%+)",
            ]

            initial_fig_count = len(figs)
            trials(figs, df, languages[i : i + n])
            # Add captions for any figures added by trials()
            if len(figs) > initial_fig_count:
                captions.append(f"Distribution of commit ratios stratified by programming language, showing adoption variance and median comparisons (languages {i+1}-{min(i+n, len(languages))}).")
                fig_labels.append(f"fig:programming-language-adoption-{chunk_num}")
                chunk_num += 1

    topics = expand_topics_to_columns(
        df,
        min_frequency=40,
        exclude_topics=[l.lower() for l in languages]
        + ["hacktoberfest", "csharp", "sql", "golang", "php", "cpp"],
    )
    df.loc[df["topics"].notna(), "topic_r"] = df["adoption_ratio"]
    df.loc[df["topics"].notna(), "topic_c"] = df["since_adoption_commits"]
    topic_fig = horizontal_box_jitter_metrics_ref(
        df,
        topics,
        figsize=(12, 10),
        max_points=500,
        min_obs=15,
        reference_metric="topic",
        min_commits=10,
    )
    if topic_fig:
        figs.append(topic_fig)
        captions.append("Topic-stratified analysis of commit adoption ratios, comparing adoption patterns across repository categorizations.")
        fig_labels.append("fig:topic-adoption-distribution")

    figs = [f for f in figs if f is not None]
    for f in figs:
        print(f)
    # Generate PDF report
    generate_visualization_report(figs, output_path, separate_files=True, captions=captions, labels=fig_labels)

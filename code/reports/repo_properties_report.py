# reports/repo_properties_report.py
# python reports/repo_properties_report.py ../temp/october-26/analysis_report_129428.csv ../data/all-projects-labels-topics-languages.csv


import json
from pathlib import Path
from typing import Optional, Set

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from adjustText import adjust_text
from reports.common import (
    default_args,
    filter_df,
    find_and_select_report,
    merge_dataframes_by_github_url,
    generate_visualization_report,
    set_input_path,
    write_file,
)


def get_significant_languages(row, min_percentage_threshold):
    if pd.isna(row["languages"]):
        return []
    try:
        # The language string is a dict-like string, not always valid JSON
        # It might contain single quotes.
        lang_str = row["languages"].replace("'", '"')
        languages_dict = json.loads(lang_str)
        total_bytes = sum(languages_dict.values())
        if total_bytes == 0:
            return []
        significant_languages = [
            lang
            for lang, size in languages_dict.items()
            if size / total_bytes >= min_percentage_threshold
        ]
        return significant_languages
    except (json.JSONDecodeError, TypeError):
        return []


def create_adoption_vs_frequency_plot(
    data: pd.DataFrame,
    column_name: str,
    threshold: int = 100,
    processing_type: str = "split",
    min_lang_percentage: float = 0.05,
    adoption_metric: str = "uses_any_tool",
    intensity_metric: Optional[str] = "estimated_numerical_intensity",
    languages_to_filter: Set[str] = set(),
):
    """
    Generic function to plot adoption rate vs. frequency for a given column.
    """
    df = data.copy()

    use_intensity = intensity_metric is not None

    required_cols = [adoption_metric]
    if use_intensity:
        required_cols.append(intensity_metric)

    if column_name not in df.columns:
        print(f"Skipping plot for '{column_name}' due to missing column.")
        return None
    if not all(col in df.columns for col in required_cols):
        print(
            f"Skipping plot for '{column_name}' due to missing required metrics columns."
        )
        return None

    overall_adoption_rate = df[adoption_metric].mean() * 100

    df_exploded = None
    plot_column_name = column_name
    if processing_type == "split":
        df_col = df.dropna(subset=[column_name]).copy()
        # handle cases where the column might be empty string or NaN which become float
        df_col[column_name] = df_col[column_name].astype(str).str.split(";")
        df_exploded = df_col.explode(column_name)
        df_exploded[column_name] = df_exploded[column_name].str.strip()
        df_exploded = df_exploded[df_exploded[column_name] != ""]

        # Filter out items that are also programming languages
        if languages_to_filter:
            initial_count = len(df_exploded)
            df_exploded = df_exploded[
                ~df_exploded[column_name].str.lower().isin(languages_to_filter)
            ]
            print(
                f"Filtered {initial_count - len(df_exploded)} {column_name} that were also languages."
            )

    elif processing_type == "language":
        plot_column_name = "significant_languages"
        df_lang = df.copy()
        df_lang[plot_column_name] = df_lang.apply(
            lambda row: get_significant_languages(row, min_lang_percentage), axis=1
        )
        df_exploded = df_lang.explode(plot_column_name).dropna(
            subset=[plot_column_name]
        )
        df_exploded = df_exploded[df_exploded[plot_column_name] != ""]
    else:
        raise ValueError(f"Unknown processing type: {processing_type}")

    if df_exploded is None or df_exploded.empty:
        print(f"No data to plot for '{column_name}' after processing.")
        return None

    item_frequency = (
        df_exploded.groupby(plot_column_name).size().reset_index(name="frequency")
    )
    item_adoption = (
        df_exploded.groupby(plot_column_name)[adoption_metric]
        .mean()
        .mul(100)
        .reset_index(name="adoption_rate")
    )

    merged_data = pd.merge(item_frequency, item_adoption, on=plot_column_name)

    if use_intensity:
        adopters_exploded = df_exploded[df_exploded[adoption_metric] == True].copy()
        item_intensity = (
            adopters_exploded.groupby(plot_column_name)[intensity_metric]
            .mean()
            .reset_index(name="avg_intensity")
        )
        merged_data = pd.merge(
            merged_data, item_intensity, on=plot_column_name, how="left"
        )
        merged_data["avg_intensity"] = merged_data["avg_intensity"].fillna(0)
    else:
        # Uniform intensity of 1.0 when no metric is provided
        merged_data["avg_intensity"] = 1.0

    merged_data = merged_data[merged_data["frequency"] >= threshold]

    if merged_data.empty:
        print(
            f"Skipping plot for '{column_name}': No items met the frequency threshold of {threshold}."
        )
        return None

    fig, ax = plt.subplots(figsize=(14, 10))
    sns.scatterplot(
        data=merged_data,
        x="adoption_rate",
        y="frequency",
        hue=plot_column_name,
        alpha=0.7,
        ax=ax,
        legend=False,
    )

    ax.set_title(
        f"AI Adoption Rate vs. Project Frequency for {column_name.replace('_', ' ').title()} (Frequency >= {threshold})",
        fontsize=16,
    )
    ax.set_xlabel("AI Adoption Rate (%)", fontsize=12)
    ax.set_ylabel("Number of Projects (Frequency)", fontsize=12)
    ax.set_yscale("log")

    texts = []
    # Add text labels for each point
    for i, row in merged_data.iterrows():
        txt = ax.text(
            row["adoption_rate"], row["frequency"], row[plot_column_name], fontsize=9
        )
        texts.append(txt)

    adjust_text(texts, arrowprops=dict(arrowstyle="->", color="darkgray", lw=1))

    ax.axvline(
        overall_adoption_rate,
        color="red",
        linestyle="--",
        linewidth=2,
        label=f"Overall Adoption ({overall_adoption_rate:.1f}%)",
    )
    ax.legend()

    plt.tight_layout()
    return fig


def create_adoption_vs_frequency_plot_zoom(
    data: pd.DataFrame,
    column_name: str,
    threshold: int = 100,
    processing_type: str = "split",
    min_lang_percentage: float = 0.05,
    adoption_metric: str = "uses_any_tool",
    intensity_metric: Optional[str] = "estimated_numerical_intensity",
    languages_to_filter: Set[str] = set(),
    break_point: float = 20.0,  # New: where to break the x-axis (%)
    zoom_low: float = 3.0,  # New: horizontal zoom for low side
    zoom_high: float = 0.5,  # New: horizontal zoom for high side
):
    """
    Generic function to plot adoption rate vs. frequency for a given column,
    with an optional broken x-axis to emphasize low-adoption clustering.
    """

    df = data.copy()
    use_intensity = intensity_metric is not None
    required_cols = [adoption_metric]
    if use_intensity:
        required_cols.append(intensity_metric)

    if column_name not in df.columns:
        print(f"Skipping plot for '{column_name}' due to missing column.")
        return None
    if not all(col in df.columns for col in required_cols):
        print(
            f"Skipping plot for '{column_name}' due to missing required metrics columns."
        )
        return None

    overall_adoption_rate = df[adoption_metric].mean() * 100

    # ---- Data processing ----
    if processing_type == "split":
        df_col = df.dropna(subset=[column_name]).copy()
        df_col[column_name] = df_col[column_name].astype(str).str.split(";")
        df_exploded = df_col.explode(column_name)
        df_exploded[column_name] = df_exploded[column_name].str.strip()
        df_exploded = df_exploded[df_exploded[column_name] != ""]

        if languages_to_filter:
            df_exploded = df_exploded[
                ~df_exploded[column_name].str.lower().isin(languages_to_filter)
            ]
    elif processing_type == "language":
        plot_column_name = "significant_languages"
        df_lang = df.copy()
        df_lang[plot_column_name] = df_lang.apply(
            lambda row: get_significant_languages(row, min_lang_percentage), axis=1
        )
        df_exploded = df_lang.explode(plot_column_name).dropna(
            subset=[plot_column_name]
        )
        df_exploded = df_exploded[df_exploded[plot_column_name] != ""]
    else:
        raise ValueError(f"Unknown processing type: {processing_type}")

    if df_exploded is None or df_exploded.empty:
        print(f"No data to plot for '{column_name}' after processing.")
        return None

    item_frequency = (
        df_exploded.groupby(column_name).size().reset_index(name="frequency")
    )
    item_adoption = (
        df_exploded.groupby(column_name)[adoption_metric]
        .mean()
        .mul(100)
        .reset_index(name="adoption_rate")
    )

    merged_data = pd.merge(item_frequency, item_adoption, on=column_name)

    if use_intensity:
        adopters_exploded = df_exploded[df_exploded[adoption_metric] == True].copy()
        item_intensity = (
            adopters_exploded.groupby(column_name)[intensity_metric]
            .mean()
            .reset_index(name="avg_intensity")
        )
        merged_data = pd.merge(merged_data, item_intensity, on=column_name, how="left")
        merged_data["avg_intensity"] = merged_data["avg_intensity"].fillna(0)
    else:
        merged_data["avg_intensity"] = 1.0

    merged_data = merged_data[merged_data["frequency"] >= threshold]
    if merged_data.empty:
        print(
            f"Skipping plot for '{column_name}': No items met the frequency threshold of {threshold}."
        )
        return None

    # ---- Plot with broken x-axis ----
    fig, (ax_low, ax_high) = plt.subplots(
        1,
        2,
        sharey=True,
        figsize=(14, 20),
        gridspec_kw={"width_ratios": [zoom_low, zoom_high]},
    )

    sns.scatterplot(
        data=merged_data,
        x="adoption_rate",
        y="frequency",
        hue=column_name,
        alpha=0.7,
        ax=ax_low,
        legend=False,
    )
    sns.scatterplot(
        data=merged_data,
        x="adoption_rate",
        y="frequency",
        hue=column_name,
        alpha=0.7,
        ax=ax_high,
        legend=False,
    )

    # Set limits for each subplot
    ax_low.set_xlim(0, break_point)
    ax_high.set_xlim(break_point, merged_data["adoption_rate"].max() * 1.05)

    for ax in (ax_low, ax_high):
        ax.set_yscale("log")
        ax.set_ylabel("Number of Projects (Frequency)")
        ax.set_xlabel("AI Adoption Rate (%)")
        ax.grid(True, alpha=0.3)

    # Remove spines between the two x-axes
    ax_low.spines["right"].set_visible(False)
    ax_high.spines["left"].set_visible(False)
    ax_low.yaxis.tick_left()
    ax_high.yaxis.tick_right()

    # Draw slanted diagonal marks
    d = 0.015  # diagonal size
    kwargs = dict(transform=ax_low.transAxes, color="k", clip_on=False)
    ax_low.plot((1 - d, 1 + d), (-d, +d), **kwargs)
    ax_low.plot((1 - d, 1 + d), (1 - d, 1 + d), **kwargs)
    kwargs.update(transform=ax_high.transAxes)
    ax_high.plot((-d, +d), (-d, +d), **kwargs)
    ax_high.plot((-d, +d), (1 - d, 1 + d), **kwargs)

    # Add labels and adjust text for each subplot
    # ---- Separate adjust_text for each axis ----
    texts_low, texts_high = [], []
    for i, row in merged_data.iterrows():
        if row["adoption_rate"] <= break_point:
            texts_low.append(
                ax_low.text(
                    row["adoption_rate"],
                    row["frequency"],
                    row[column_name],
                    fontsize=9,
                    ha="center",
                    va="center",
                )
            )
        else:
            texts_high.append(
                ax_high.text(
                    row["adoption_rate"],
                    row["frequency"],
                    row[column_name],
                    fontsize=9,
                    ha="center",
                    va="center",
                )
            )

    # Adjust labels independently for each subplot
    from adjustText import adjust_text

    if texts_low:
        adjust_text(
            texts_low,
            ax=ax_low,
            only_move={"points": "y", "text": "xy"},
            expand_points=(1.2, 1.4),
            expand_text=(1.2, 1.4),
            arrowprops=dict(arrowstyle="->", color="darkgray", lw=1, alpha=0.7),
        )

    if texts_high:
        adjust_text(
            texts_high,
            ax=ax_high,
            only_move={"points": "y", "text": "xy"},
            expand_points=(1.2, 1.4),
            expand_text=(1.2, 1.4),
            arrowprops=dict(arrowstyle="->", color="darkgray", lw=1, alpha=0.7),
        )

    # Add overall adoption rate line
    for ax in (ax_low, ax_high):
        ax.axvline(
            overall_adoption_rate,
            color="red",
            linestyle="--",
            linewidth=2,
            label=f"Overall Adoption ({overall_adoption_rate:.1f}%)",
        )

    ax_high.legend(loc="upper right")
    # fig.suptitle(
    #    f'AI Adoption Rate vs. Project Frequency for {column_name.replace("_", " ").title()} (Frequency ≥ {threshold})',
    #    fontsize=16
    # )

    plt.tight_layout()
    return fig


def generate_figs(df, args, partial_path, commit_use=False):

    intensity_metric = args.intensity_metric
    if intensity_metric and intensity_metric.lower() == "none":
        intensity_metric = None

    # Calculate all significant languages for filtering topics/labels
    df_lang_temp = df.copy()
    df_lang_temp["significant_languages"] = df_lang_temp.apply(
        lambda row: get_significant_languages(row, args.lang_per_threshold), axis=1
    )
    all_languages = set(
        df_lang_temp.explode("significant_languages")["significant_languages"]
        .str.lower()
        .dropna()
        .unique()
    )
    print(
        f"Found {len(all_languages)} significant languages for filtering topics/labels."
    )

    figures = []
    captions = []
    labels = []

    # add this label that is not very informative to increase vertical resolution
    for l in ["hacktoberfest", "golang", "cpp", "python3", "csharp", "c-plus-plus"]:
        all_languages.add(l)

    # Plot for topics
    if commit_use:
        fig_topics = create_adoption_vs_frequency_plot_zoom(
            data=df,
            column_name="topics",
            threshold=args.topic_threshold,
            processing_type="split",
            adoption_metric=args.adoption_metric,
            intensity_metric=intensity_metric,
            languages_to_filter=all_languages,
            break_point=35.0,  # New: where to break the x-axis (%)
            zoom_low=2.0,  # New: horizontal zoom for low side
            zoom_high=0.5,  # New: horizontal zoom for high side
        )
    else:
        fig_topics = create_adoption_vs_frequency_plot_zoom(
            data=df,
            column_name="topics",
            threshold=args.topic_threshold,
            processing_type="split",
            adoption_metric=args.adoption_metric,
            intensity_metric=intensity_metric,
            languages_to_filter=all_languages,
        )
    if fig_topics:
        figures.append(fig_topics)
        captions.append("Adoption rate versus project frequency for repository topics meeting the frequency threshold, highlighting how specific categorizations correlate with AI tool adoption patterns.")
        labels.append("fig:topics-adoption-frequency")

    if False:
        # Plot for labels
        fig_labels = create_adoption_vs_frequency_plot(
            data=df,
            column_name="labels",
            threshold=args.label_threshold,
            processing_type="split",
            adoption_metric=args.adoption_metric,
            intensity_metric=intensity_metric,
            languages_to_filter=all_languages,
        )
        if fig_labels:
            figures.append(fig_labels)

    # Plot for languages
    fig_languages = create_adoption_vs_frequency_plot(
        data=df,
        column_name="languages",
        threshold=args.lang_threshold,
        processing_type="language",
        min_lang_percentage=args.lang_per_threshold,
        adoption_metric=args.adoption_metric,
        intensity_metric=intensity_metric,
        languages_to_filter=set(),  # No filtering for languages plot
    )
    if fig_languages:
        figures.append(fig_languages)
        captions.append("Distribution of AI tool adoption across programming languages by project frequency, showing adoption patterns stratified by language significance.")
        labels.append("fig:languages-adoption-frequency")

    if figures:
        generate_visualization_report(figures, partial_path, separate_files=True, captions=captions, labels=labels)
    else:
        print("No figures were generated.")


def main():
     parser = default_args("Computes basic adoption statistics ")
     parser.add_argument(
         "--metric-file",
         type=Path,
         default="../data/all-projects-labels-topics-languages.csv",
         help="Path to the CSV file with repo topics, languages, and labels.",
     )
     parser.add_argument(
         "--output",
         type=Path,
         default="repo_properties_report.pdf",
         help="Output PDF file name.",
     )
     parser.add_argument(
         "--topic-threshold",
         type=int,
         default=250,
         help="Minimum frequency for a topic to be plotted.",
     )
     parser.add_argument(
         "--label-threshold",
         type=int,
         default=150,
         help="Minimum frequency for a label to be plotted.",
     )
     parser.add_argument(
         "--lang-threshold",
         type=int,
         default=100,
         help="Minimum frequency for a language to be plotted.",
     )
     parser.add_argument(
         "--lang-per-threshold",
         type=float,
         default=0.05,
         help="Minimum percentage for a language to be considered significant in a project.",
     )
     parser.add_argument(
         "--adoption-metric",
         type=str,
         default="tool_use",
         help="Metric to use for adoption rate.",
     )
     parser.add_argument(
         "--intensity-metric",
         type=str,
         default="none",
         help="Metric to use for intensity (bubble size). Set to 'none' to use a uniform intensity of 1.",
     )
     parser.add_argument(
         "--size-by-frequency",
         action="store_true",
         help="If set, the bubble size will be proportional to the item's frequency instead of the intensity metric.",
     )
     parser.add_argument(
         "--second-adoption-metric",
         type=str,
         default=None,
         help="Optional second metric to compare against the first adoption metric in a dual-plot visualization.",
     )

     args = parser.parse_args()
     set_input_path(args.input)
     report = find_and_select_report(Path(args.input))

     df = merge_dataframes_by_github_url(args.metric_file, report)

     # filter by organizations if needed
     df = filter_df(df, args, "github_url")

     if df.empty:
         print("No data after merging. Exiting.")
         return

     # generate_figs(df, args, "rq3/repo_properties_report.pdf")

     # df_copy = df.copy()
     # df_copy["tool_use"] = df_copy["tool_use"] | df_copy["ignored_tools"]

     # generate_figs(df_copy, args, "rq3/ignored_repo_properties_report.pdf")

     df_copy2 = df.copy()
     df_copy2["tool_use"] = (
         df_copy2["tool_use"] | df_copy2["ignored_tools"] | df_copy2["commit_use"]
     )

     generate_figs(df_copy2, args, f"rq3/all_{args.output}", commit_use=True)

    # df_copy_2 = df.copy()
    # df_copy_2['tool_use'] = df_copy_2['tool_use'] | df_copy_2['ignored_tools'] | df_copy_2['commit_use']

    # generate_figs(df_copy_2, args, output_path / (f"all_{args.output}"))

    # args.adoption_metric = 'commit_use'
    # generate_figs(df, args, output_path / f"commit_{args.output}")


if __name__ == "__main__":
    main()

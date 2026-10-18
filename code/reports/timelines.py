import re
from pathlib import Path
from typing import Dict, List, Literal, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.figure import Figure
from reports.common import (
    default_args,
    generate_visualization_report,
    get_report_path,
    load_data_basic,
    load_from_report,
    set_input_path,
    write_file,
)
from reports.latex_utils import (
    build_sanitized_caption,
    number_to_word,
    sanitize_latex_command_name,
)

try:
    import upsetplot
except ImportError:
    print(
        "Warning: 'upsetplot' library not found. To generate the co-adoption plot, please install it with 'pip install upsetplot'"
    )
    upsetplot = None


def load_top_organizations() -> set:
    """Loads the top organizations from 'config/patterns/top-orgs.csv'."""
    top_orgs_path = "../config/patterns/top-orgs.csv"
    file_path = Path(top_orgs_path).resolve()

    try:
        with open(file_path, "r") as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        print(
            f"Warning: Top organizations file '{file_path}' not found. Proceeding without it."
        )
        return set()


def plot_overall_adoption(
    df: pd.DataFrame, min_date: pd.Timestamp
) -> Tuple[Figure, Dict[str, any]]:
    """Plots the cumulative adoption of any tool across all projects.

    Returns:
        (Figure, dict): Figure object and counts dictionary
    """
    earliest_adoptions = df.loc[df.groupby("project_id")["adoption"].idxmin()]
    earliest_adoptions = earliest_adoptions.sort_values("adoption").reset_index()
    earliest_adoptions["cumulative_projects"] = range(1, len(earliest_adoptions) + 1)

    counts = {
        "totalAdoptingProjects": len(earliest_adoptions),
        "firstAdoptionDate": "",
        "lastAdoptionDate": "",
        "adoptionTimeSpanDays": 0,
    }

    if not earliest_adoptions.empty:
        last_adoption_date = df["adoption"].max()
        first_adoption_date = df["adoption"].min()
        counts["firstAdoptionDate"] = first_adoption_date.strftime("%Y-%m-%d")
        counts["lastAdoptionDate"] = last_adoption_date.strftime("%Y-%m-%d")
        counts["adoptionTimeSpanDays"] = (last_adoption_date - first_adoption_date).days

        zero_point = pd.DataFrame([{"adoption": min_date, "cumulative_projects": 0}])
        end_point = pd.DataFrame(
            [
                {
                    "adoption": last_adoption_date,
                    "cumulative_projects": earliest_adoptions[
                        "cumulative_projects"
                    ].iloc[-1],
                }
            ]
        )
        plot_data = pd.concat(
            [zero_point, earliest_adoptions, end_point], ignore_index=True
        )
    else:
        plot_data = pd.DataFrame([{"adoption": min_date, "cumulative_projects": 0}])

    fig, ax = plt.subplots(figsize=(12, 7))
    ax.plot(
        plot_data["adoption"], plot_data["cumulative_projects"], drawstyle="steps-post"
    )
    ax.fill_between(
        plot_data["adoption"], plot_data["cumulative_projects"], step="post", alpha=0.3
    )
    ax.set_xlabel("Date", fontsize=12)
    ax.set_ylabel("Cumulative Number of Adopting Projects", fontsize=12)
    if not df.empty:
        ax.set_xlim(min_date, df["adoption"].max())
    plt.tight_layout()

    return fig, counts


def build_cumulative(df_subset: pd.DataFrame, group_col: str, min_date: pd.Timestamp):
    """Build cumulative adoption data for each category."""
    if df_subset.empty:
        return {}, min_date

    last_date = df_subset["adoption"].max()
    cumulative_data = {}

    for item in df_subset[group_col].unique():
        item_df = (
            df_subset[df_subset[group_col] == item]
            .sort_values("adoption")
            .reset_index(drop=True)
        )
        item_df["cumulative_count"] = range(1, len(item_df) + 1)
        final_count = len(item_df)
        anchored = pd.DataFrame(
            {
                "adoption": [min_date] + item_df["adoption"].tolist() + [last_date],
                "cumulative_count": [0]
                + item_df["cumulative_count"].tolist()
                + [final_count],
            }
        )
        cumulative_data[item] = anchored

    return cumulative_data, last_date


import matplotlib.dates as mdates


def plot_adoption_panel(
    ax,
    cumulative_data: dict,
    plot_type: str,
    label: str,
    min_date: pd.Timestamp,
    last_date: pd.Timestamp,
    title_suffix: str,
    color_map: dict,
):
    """Plot a single adoption panel (stacked or ridgeline)."""
    if not cumulative_data:
        ax.text(0.5, 0.5, f"No data for {title_suffix}", ha="center", va="center")
        return

    sorted_items = sorted(
        cumulative_data.keys(),
        key=lambda x: cumulative_data[x]["cumulative_count"].iloc[-1],
        reverse=True,
    )

    if plot_type == "stacked":
        all_dates = pd.date_range(start=min_date, end=last_date, freq="D")
        pivot_df = pd.DataFrame(index=all_dates)

        for item in sorted_items:
            item_data = (
                cumulative_data[item]
                .groupby("adoption", as_index=True)["cumulative_count"]
                .max()
            )
            pivot_df[item] = item_data.reindex(all_dates, method="ffill").fillna(0)

        if pivot_df.empty:
            ax.text(0.5, 0.5, f"No data ({title_suffix})", ha="center", va="center")
            return

        colors = [color_map.get(col, "#888888") for col in pivot_df.columns]
        pivot_df.plot.area(stacked=True, ax=ax, linewidth=0, color=colors)
        ax.set_xlim(min_date, last_date)
        ax.set_xlabel("Date")
        ax.set_ylabel("Cumulative Adoptions")
        ax.legend(title=label, loc="upper left")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

    elif plot_type == "ridgeline":
        overlap = 0.7
        y_ticks, y_labels = [], []
        max_adoptions = max(
            data["cumulative_count"].iloc[-1] for data in cumulative_data.values()
        )

        for i, item in enumerate(reversed(sorted_items)):
            plot_data = cumulative_data[item]
            y = plot_data["cumulative_count"] / max_adoptions
            vertical_offset = i * overlap
            color = color_map.get(item, "#888888")
            pretty_item = item.replace("_", " ").title()

            ax.plot(plot_data["adoption"], y + vertical_offset, color=color, lw=1.5)
            ax.fill_between(
                plot_data["adoption"],
                vertical_offset,
                y + vertical_offset,
                alpha=0.5,
                color=color,
            )

            final_count = plot_data["cumulative_count"].iloc[-1]
            ax.text(
                plot_data["adoption"].iloc[-1] + pd.Timedelta(days=5),
                y.iloc[-1] + vertical_offset,
                f"{final_count:,}",
                fontsize=9,
                fontweight="semibold",
                alpha=0.7,
                color=color,
                va="center",
            )

            y_ticks.append(vertical_offset + 0.05)
            y_labels.append(pretty_item)

        ax.set_yticks(y_ticks)
        ax.set_yticklabels(y_labels)
        ax.tick_params(axis="y", length=0)
        for spine in ["left", "right", "top"]:
            ax.spines[spine].set_visible(False)

        ax.set_xlim(min_date, last_date + pd.Timedelta(days=15))
        ax.set_xlabel("Date")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))

    else:
        raise ValueError("plot_type must be 'stacked' or 'ridgeline'.")


def plot_adoption_split(
    df: pd.DataFrame,
    group_col: str,
    label: str,
    min_date: pd.Timestamp,
    top_x: int = 5,
    top_y: int = 15,
    plot_type: str = "stacked",
    show_others: bool = True,
) -> Tuple[Figure, Dict[str, any]]:
    """
    Create two adoption plots side by side with extracted counts.

    Returns:
        (Figure, dict): Figure object and counts dictionary
    """
    df = df.copy()
    df["adoption"] = (
        pd.to_datetime(df["adoption"], utc=True, errors="coerce")
        .dt.tz_localize(None)
        .dt.floor("D")
    )
    min_date = pd.to_datetime(min_date, utc=True).tz_localize(None)

    # Rank all categories
    counts_series = df[group_col].value_counts()
    sorted_items = counts_series.index.tolist()

    top_x_items = sorted_items[:top_x]
    next_items = sorted_items[top_x:top_y]
    others_left = sorted_items[top_x:]
    others_right = sorted_items[top_y:]

    if show_others:
        displayedItems = len(sorted_items)
    else:
        displayedItems = top_x + top_y

    # Build counts dictionary
    counts = {
        f"total{label}Adoptions": len(df),
        f"unique{label}s": len(sorted_items),
        f"displayed{label}s": displayedItems,
    }

    # Top tier items
    if sorted_items:
        counts[f"top{label}Name"] = sorted_items[0]
        counts[f"top{label}Count"] = int(counts_series.iloc[0])

    # Individual top tier counts
    for item in top_x_items + next_items:
        safe_name = sanitize_latex_command_name(item)
        counts[f"{safe_name}Count"] = int(counts_series[item])

    # Others count
    if show_others and others_right:
        counts[f"other{label}sCount"] = sum(
            counts_series[item] for item in others_right
        )

    # ===== Build Subsets =====
    df_topx = df[df[group_col].isin(top_x_items)]
    df_next = df[df[group_col].isin(next_items)]

    if show_others:
        df_others_left = df[df[group_col].isin(others_left)].assign(
            **{group_col: "Other"}
        )
        df_others_right = df[df[group_col].isin(others_right)].assign(
            **{group_col: "Other"}
        )
        df_topx_with_other = pd.concat([df_topx, df_others_left])
        df_next_with_other = pd.concat([df_next, df_others_right])
    else:
        df_topx_with_other = df_topx
        df_next_with_other = df_next

    # ===== Build cumulative data =====
    data_left, last_date_left = build_cumulative(
        df_topx_with_other, group_col, min_date
    )
    data_right, last_date_right = build_cumulative(
        df_next_with_other, group_col, min_date
    )
    last_date = max(last_date_left, last_date_right)

    # ===== Color map =====
    unique_items = list(set(data_left.keys()) | set(data_right.keys()))
    colors = sns.color_palette("viridis", n_colors=len(unique_items))
    color_map = {item: color for item, color in zip(unique_items, colors)}
    if "Other" in unique_items:
        color_map["Other"] = "#888888"

    # ===== Create figure =====
    fig, axes = plt.subplots(1, 2, figsize=(12, 8), sharey=False)
    plot_adoption_panel(
        axes[0],
        data_left,
        plot_type,
        label,
        min_date,
        last_date,
        f"Top {top_x}" + (" + Others" if show_others else ""),
        color_map,
    )
    plot_adoption_panel(
        axes[1],
        data_right,
        plot_type,
        label,
        min_date,
        last_date,
        f"Ranks {top_x + 1}–{top_y}" + (" + Others" if show_others else ""),
        color_map,
    )

    plt.tight_layout()
    return fig, counts


def plot_stacked_adoption_by_tool(
    df: pd.DataFrame,
    min_date: pd.Timestamp,
    top_n: int = 10,
    plot_type: str = "stacked",
) -> Tuple[Figure, Dict[str, any]]:
    """Plots adoption for tools with extracted counts."""
    return plot_adoption_split(
        df,
        group_col="tool",
        label="Tool",
        min_date=min_date,
        top_x=6,
        top_y=20,
        plot_type=plot_type,
    )


def plot_stacked_adoption_by_organization(
    df: pd.DataFrame,
    min_date: pd.Timestamp,
    top_n: int = 10,
    plot_type: str = "stacked",
) -> Tuple[Figure, Dict[str, any]]:
    """Plots adoption for organizations with extracted counts."""
    return plot_adoption_split(
        df,
        group_col="org",
        label="Organization",
        min_date=min_date,
        top_x=10,
        top_y=25,
        plot_type=plot_type,
        show_others=False,
    )


def plot_coadoption_upset(
    df: pd.DataFrame,
    min_subset_size: int = 10,
    min_freq: int = 10,
    orientation: Literal["horizontal", "vertical"] = "vertical",
) -> Tuple[Figure, Dict[str, any]]:
    """Plots an upset plot of tool co-adoption with extracted counts."""

    counts = {
        "minSubsetSize": min_subset_size,
        "minFrequency": min_freq,
    }

    if upsetplot is None:
        fig, ax = plt.subplots()
        ax.text(
            0.5,
            0.5,
            "upsetplot library not installed.\\nRun 'pip install upsetplot'",
            ha="center",
            va="center",
            fontsize=12,
            color="red",
        )
        return fig, counts

    # Get all unique projects before filtering
    all_projects = df["project_id"].unique()
    counts["totalProjects"] = len(all_projects)

    # Calculate how many tools each project uses (before any filtering)
    tools_per_project = df.groupby("project_id")["tool"].nunique()

    # Store distribution of tool counts across all projects
    tool_count_distribution = tools_per_project.value_counts().sort_index()
    for num_tools, project_count in tool_count_distribution.items():
        counts[f"projectsUsing{num_tools}Tools"] = int(project_count)

    # Summary stats for tool usage
    counts["meanToolsPerProject"] = float(tools_per_project.mean())
    counts["medianToolsPerProject"] = float(tools_per_project.median())
    counts["maxToolsPerProject"] = int(tools_per_project.max())
    counts["minToolsPerProject"] = int(tools_per_project.min())

    # Filter to keep only tools above frequency threshold
    df = df[df["tool"].map(df["tool"].value_counts()) > min_freq]

    # Track how many projects were kept after filtering
    filtered_projects = df["project_id"].unique()
    counts["projectsAfterFiltering"] = len(filtered_projects)
    counts["projectsFilteredOut"] = (
        counts["totalProjects"] - counts["projectsAfterFiltering"]
    )

    # Map each tool to the list of project IDs
    tool_to_projects = {
        tool: df.loc[df["tool"] == tool, "project_id"].unique().tolist()
        for tool in df["tool"].unique()
    }

    counts["toolsAnalyzed"] = len(tool_to_projects)

    # Build upset data
    upset_data = upsetplot.from_contents(tool_to_projects)

    # Count how many projects are in each intersection (group by the multi-index)
    intersection_sizes = upset_data.groupby(
        level=list(range(len(tool_to_projects)))
    ).size()
    intersection_sizes = intersection_sizes.sort_values(ascending=False)

    counts["totalIntersections"] = len(intersection_sizes)

    # Filter intersections that meet the minimum subset size
    significant_intersections = intersection_sizes[
        intersection_sizes >= min_subset_size
    ].sort_values(ascending=False)
    filtered_out_intersections = intersection_sizes[
        intersection_sizes < min_subset_size
    ]

    # Track projects in filtered vs shown intersections
    counts["projectsInShownIntersections"] = int(significant_intersections.sum())
    counts["projectsInFilteredIntersections"] = int(filtered_out_intersections.sum())

    # Debug output
    print(f"Total intersections: {len(intersection_sizes)}")
    print(f"Min subset size: {min_subset_size}")
    print(
        f"Intersection sizes range: {intersection_sizes.min()} to {intersection_sizes.max()}"
    )
    print(f"Significant intersections: {len(significant_intersections)}")
    print(f"Projects in shown intersections: {counts['projectsInShownIntersections']}")
    print(
        f"Projects in filtered intersections: {counts['projectsInFilteredIntersections']}"
    )
    print(f"First few significant intersections:\n{significant_intersections.head()}")

    counts["projectsWithMultipleTools"] = len(significant_intersections)

    if len(intersection_sizes) > 0:
        largest_idx = intersection_sizes.idxmax()
        counts["largestIntersectionSize"] = int(intersection_sizes.max())
        # Get tool names from the multi-index
        if isinstance(largest_idx, tuple):
            tool_list = list(tool_to_projects.keys())
            tools_in_intersection = [
                tool_list[i] for i, val in enumerate(largest_idx) if val
            ]
            counts["largestIntersectionTools"] = ", ".join(
                str(t) for t in tools_in_intersection
            )
        else:
            counts["largestIntersectionTools"] = "N/A"

    # Save individual intersection data using sanitized tool names
    for intersection_idx, size in significant_intersections.items():
        # Get tool names for this intersection
        if isinstance(intersection_idx, tuple):
            tool_list = list(tool_to_projects.keys())
            tools = [tool_list[j] for j, val in enumerate(intersection_idx) if val]
        else:
            tools = [str(intersection_idx)]

        if len(tools) == 0:
            continue

        # Create sanitized command name from all tool names
        safe_name = sanitize_latex_command_name("_".join(tools))

        # Create the LaTeX formatted output: tool1 \cap tool2 (count)
        latex_tools = " $\\cap$ ".join(tools)
        latex_output = f"{latex_tools} ({int(size)})"
        print(latex_output)

        # Store with sanitized name as key
        counts[f"intersection{safe_name}"] = latex_output
        counts[f"intersection{safe_name}Size"] = int(size)
        counts[f"intersection{safe_name}ToolCount"] = len(tools)

    # Summary statistics for shown intersections
    if len(significant_intersections) > 0:
        counts["meanIntersectionSize"] = int(significant_intersections.mean())
        counts["medianIntersectionSize"] = int(significant_intersections.median())

        # Count by number of tools in intersection (only for shown intersections)
        intersection_tool_counts = {}
        for intersection_idx in significant_intersections.index:
            if isinstance(intersection_idx, tuple):
                num_tools = sum(intersection_idx)
            else:
                num_tools = 1
            intersection_tool_counts[num_tools] = (
                intersection_tool_counts.get(num_tools, 0) + 1
            )

        for num_tools, count in sorted(intersection_tool_counts.items()):
            counts[f"shownIntersections{num_tools}Tools"] = count

    # Create figure
    figsize = (15, 8) if orientation == "horizontal" else (8, 15)
    fig = plt.figure(figsize=figsize)

    # Generate the UpSet plot
    upsetplot.plot(
        upset_data,
        show_counts=True,
        min_subset_size=min_subset_size,
        orientation=orientation,
        fig=fig,
    )

    plt.tight_layout()
    return fig, counts


def generate_latex_commands(
    all_counts: Dict[str, Dict[str, any]], output_path: Path = None
):
    """Generate LaTeX commands file from counts dictionaries."""

    commands = []
    commands.append("% Auto-generated LaTeX commands from figure counts")
    commands.append("% Do not edit manually - regenerate using the plotting script\n")

    for fig_name, counts in all_counts.items():
        commands.append(f"% Commands from {fig_name}")
        for key, value in counts.items():
            # Sanitize the command name
            cmd_name = sanitize_latex_command_name(key)

            # Format the value appropriately
            if isinstance(value, (int, np.integer)):
                formatted_value = f"{value:,}".replace(
                    ",", "\\,"
                )  # LaTeX thin space for thousands
            elif isinstance(value, str):
                # Check if string already contains LaTeX math mode ($ symbols)
                if "$" in value:
                    # Don't escape $ signs that are part of math mode
                    formatted_value = (
                        value.replace("_", "\\_")
                        .replace("&", "\\&")
                        .replace("%", "\\%")
                    )
                    formatted_value = formatted_value.replace("#", "\\#")
                else:
                    # Escape special LaTeX characters including $
                    formatted_value = (
                        value.replace("_", "\\_")
                        .replace("&", "\\&")
                        .replace("%", "\\%")
                    )
                    formatted_value = formatted_value.replace("#", "\\#").replace(
                        "$", "\\$"
                    )
            else:
                formatted_value = str(value)

            commands.append(f"\\newcommand{{\\{cmd_name}}}{{{formatted_value}}}")
        commands.append("")  # Blank line between figures

    write_file("rq4/figure_counts.tex", "\n".join(commands))
    print(f"Generated LaTeX commands: rq4/figure_counts.tex")


def load_file_new(args):
    report = load_from_report(
        args,
        [
            "commit_use",
            "tool_use",
            "ignored_tools",
            "since_adoption_commits",
            "adoption_ratio",
        ],
    )
    dataframes = load_data_basic(args.input, "adoption_dates.csv")
    df = pd.concat(dataframes, ignore_index=True)
    df["adoption"] = pd.to_datetime(df["adoption"], format="mixed")
    now = pd.Timestamp.utcnow()
    df = df[(df["adoption"] >= args.min_date) & (df["adoption"] < now)].copy()

    df = pd.merge(df, report, on="project", how="inner")
    top_orgs = load_top_organizations()
    top_orgs = {x.lower() for x in top_orgs}

    orgs_to_groups = pd.read_csv("../data/org_repo_counts.csv")
    orgs_to_groups = orgs_to_groups.applymap(
        lambda x: x.lower() if isinstance(x, str) else x
    )

    orgs_with_groups = orgs_to_groups[orgs_to_groups["group"].notna()]
    group_dict = dict(zip(orgs_with_groups["organization"], orgs_with_groups["group"]))

    def get_org(github_url):
        return github_url.split("/")[3].lower()

    def org_or_group(github_url):
        org = get_org(github_url)
        if org in group_dict:
            return group_dict[org]
        return org

    df["org"] = df["github_url"].apply(lambda url: org_or_group(url))
    df["top_org"] = df["github_url"].apply(lambda url: get_org(url) in top_orgs)
    df["project_id"] = df["project"]
    df["adoption"] = pd.to_datetime(df["adoption"], format="mixed")
    df.to_csv(args.time_file)
    return df


def main():
    """Main function to generate individual PDF figures and LaTeX document."""
    parser = default_args("Make timeline figures and LaTeX report")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="figures",
        help="Output directory for figures and LaTeX.",
    )
    parser.add_argument(
        "--min-date",
        type=str,
        default="2025-01-01",
        help="Start date for analysis (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--min-upset-size",
        type=int,
        default=30,
        help="Minimum intersection size for upset plot.",
    )
    parser.add_argument("--read-file", action="store_true", default=False)
    parser.add_argument("--time-file", type=str, default="timeline_info.csv")

    args = parser.parse_args()
    set_input_path(args.input)
    min_date = pd.to_datetime(args.min_date).tz_localize("UTC")
    output_dir = get_report_path("rq4/timeline_report")
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.read_file:
        df = pd.read_csv(args.time_file)
        df["adoption"] = pd.to_datetime(df["adoption"], format="mixed")
    else:
        df = load_file_new(args)

    # Define all figures with metadata
    figure_specs = [
        {
            "name": "overall_adoption",
            "title": "Overall Tool Adoption Curve",
            "caption": "Cumulative adoption of any tool across all \\totalAdoptingProjects\\ projects from \\firstAdoptionDate\\ to \\lastAdoptionDate.",
            "required_keys": [
                "totalAdoptingProjects",
                "firstAdoptionDate",
                "lastAdoptionDate",
            ],
            "generator": lambda: plot_overall_adoption(df, min_date),
        },
        {
            "name": "tool_adoption",
            "title": "Tool Adoption by Category",
            "caption": "Ridgeline plot showing adoption patterns for the \\displayedTools\\ tools, with \\topToolName\\ being the most adopted (\\topToolCount\\ projects).",
            "required_keys": ["displayedTools", "topToolName", "topToolCount"],
            "generator": lambda: plot_stacked_adoption_by_tool(
                df, min_date, plot_type="ridgeline"
            ),
        },
        {
            "name": "organization_adoption",
            "title": "Organization Adoption Patterns",
            "caption": "Adoption patterns across the top \\displayedOrganizations\\ organizations, with \\topOrganizationName\\ leading at \\topOrganizationCount\\ adoptions.",
            "required_keys": [
                "displayedOrganizations",
                "topOrganizationName",
                "topOrganizationCount",
            ],
            "generator": lambda: plot_stacked_adoption_by_organization(
                df, min_date, plot_type="ridgeline"
            ),
        },
        {
            "name": "tool_coadoption",
            "title": "Tool Co-Adoption Matrix",
            "caption": "UpSet plot showing tool co-adoption patterns among \\toolsAnalyzed\\ tools with at least \\minSubsetSize\\ projects per intersection.",
            "required_keys": ["toolsAnalyzed", "minSubsetSize"],
            "generator": lambda: plot_coadoption_upset(
                df, min_subset_size=40, min_freq=250, orientation="horizontal"
            ),
            "width": "1.0\\textwidth",
        },
    ]

    all_counts = {}

    # Generate figures
    print("Generating figures...")
    figures = []
    captions = []
    labels = []
    for spec in figure_specs:
        print(f"  - {spec['name']}")
        fig, counts = spec["generator"]()
        figures.append(fig)

        # Store counts
        all_counts[spec["name"]] = counts

        # Build and sanitize caption from spec using counts
        caption_template = spec.get("caption", spec["title"])
        try:
            caption = build_sanitized_caption(caption_template, counts)
        except ValueError as e:
            raise ValueError(f"Figure '{spec['name']}' caption error: {e}")
        captions.append(caption)
        
        # Store label
        label = f"fig:{spec['name'].replace('_', '-')}"
        labels.append(label)

    # Save all figures using centralized function
    generate_visualization_report(
        figures,
        "rq4/timeline_report.pdf",
        separate_files=True,
        captions=captions,
        labels=labels,
    )

    # Generate LaTeX files
    generate_latex_commands(all_counts, None)

    print(f"\n✓ All outputs saved to: {output_dir}")
    print(f"  - {len(figure_specs)} PDF figures")
    print(f"  - figure_counts.tex (LaTeX commands)")


if __name__ == "__main__":
    main()

import subprocess
from pathlib import Path

import pandas as pd
from reports.common import (
    default_args,
    filter_df,
    load_and_aggregate_tool_metrics,
    load_from_report,
    set_input_path,
    write_file,
)
from reports.latex_utils import number_to_word, generate_nested_sparkline_commands


def render_binned_histogram_latex(
    df: pd.DataFrame,
    metrics_config: dict[str, str | list[str] | list[int | float]],
    caption: str = "Binned distributions of project metrics",
    label: str = "tab:binned_distributions",
    scale_factor: float = 2.0,
    command_prefix: str = "",
) -> str:
    """
    Render binned histogram data as a LaTeX table with sparklines.

    Parameters
    ----------
    df : pd.DataFrame
        Input dataframe containing the metrics
    metrics_config : Dict[str, Dict]
        Configuration for each metric. Format:
        {
            'metric_name': {
                'bins': [bin_edges],
                'labels': [bin_labels],
                'display_name': 'Display Name'
            }
        }
    caption : str
        Table caption
    label : str
        LaTeX label for referencing
    scale_factor : float
        Scale factor for sparkline values
    command_prefix : str
        Prefix for LaTeX command names to avoid collisions

    Returns
    -------
    str
        LaTeX table code with sparkline commands
    """

    # Find maximum number of bins across all metrics
    max_bins = max(len(config["labels"]) for config in metrics_config.values())

    # Compute binned data and percentages for each metric
    binned_data = {}
    for metric, config in metrics_config.items():
        if "filter" in config:
            filter_metric, threshold = config["filter"]
            df2 = df[df[filter_metric] > threshold]
            data = df2[metric].dropna()
        else:
            data = df[metric].dropna()
        binned = pd.cut(data, bins=config["bins"], labels=config["labels"], right=False)
        counts = binned.value_counts().reindex(config["labels"], fill_value=0)
        percentages = (counts / counts.sum()) * 100
        binned_data[metric] = {
            "labels": config["labels"],
            "percentages": percentages,
            "counts": counts,
        }

    # Generate LaTeX commands
    latex_commands = []

    # Generate sparkline commands using generic builder
    sparkline_data = {}
    for metric, data in binned_data.items():
        percentages = data["percentages"].values.tolist()
        sparkline_data[metric] = percentages
    
    def build_tool_sparkline_name(path):
        metric = path[0]
        return f"{command_prefix}{metric.replace('_', '').replace(' ', '')}Sparkline"
    
    sparkline_cmds = generate_nested_sparkline_commands(
        sparkline_data,
        build_tool_sparkline_name,
        scale_factor=scale_factor
    )
    latex_commands.extend(sparkline_cmds)
    
    # Continue with value commands for each metric
    for metric, data in binned_data.items():
        safe_metric = metric.replace("_", "").replace(" ", "")

        # Value commands for each bin
        percentages = data["percentages"].values
        for i, (bin_label, pct) in enumerate(zip(data["labels"], percentages)):
            safe_bin = number_to_word(
                i
            )  # bin_label.replace('-', 'to').replace('+', 'plus').replace(' ', '')
            cmd_name = f"\\{command_prefix}{safe_metric}{safe_bin}Value"
            value_cmd = f"\\newcommand{{{cmd_name}}}{{{pct:.1f}\\%}}"
            latex_commands.append(value_cmd)

    # Build table
    latex = "\n\n% Table\n"
    latex += "\\begin{table}[ht]\n"
    latex += "\\scriptsize\n"
    latex += "\\centering\n"
    latex += f"\\caption{{{caption}}}\n"
    latex += f"\\label{{{label}}}\n"
    latex += "\\begin{tabular}{l|" + "r" * max_bins + "}\n"
    latex += "\\hline\n"
    latex += "\\toprule\n"

    # Header row (bin labels)
    latex += "\\textbf{Metric} "
    for i in range(max_bins):
        latex += f"& \\textbf{{Bin {i + 1}}} "
    latex += "\\\\\n"
    latex += "\\midrule\n"

    # Data rows (two rows per metric)
    for idx, (metric, data) in enumerate(binned_data.items()):
        display_name = metrics_config[metric].get(
            "display_name", metric.replace("_", " ").title()
        )
        safe_metric = metric.replace("_", "").replace(" ", "")

        # First row: metric name and bin labels
        latex += f"{display_name} "
        for bin_label in data["labels"]:
            latex += f"& {bin_label} "
        # Fill remaining columns if this metric has fewer bins
        for _ in range(max_bins - len(data["labels"])):
            latex += "& "
        latex += "\\\\\n"

        # Second row: sparkline and percentage values
        latex += f"\\quad \\{command_prefix}{safe_metric}Sparkline "
        for i, bin_label in enumerate(data["labels"]):
            safe_bin = number_to_word(
                i
            )  # bin_label.replace('-', 'to').replace('+', 'plus').replace(' ', '')
            cmd_name = f"\\{command_prefix}{safe_metric}{safe_bin}Value"
            latex += f"& {cmd_name} "
        # Fill remaining columns
        for _ in range(max_bins - len(data["labels"])):
            latex += "& "
        latex += "\\\\\n"

        # Add spacing between metrics
        if idx < len(binned_data) - 1:
            latex += "\\midrule\n"

    # Footer
    latex += "\\bottomrule\n"
    latex += "\\end{tabular}\n"
    latex += "\\end{table}\n"

    # Combine commands and table
    latex_full = "% LaTeX Commands\n"
    latex_full += "\n".join(latex_commands)
    latex_full += latex

    return latex_full


def render_project_metrics_binned_table(df: pd.DataFrame) -> str:
    """
    Generate LaTeX table for project metrics with binned distributions.

    Uses predefined bins for total_files, total_lines, and total_changes.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame containing columns: total_files, total_lines, total_changes

    Returns
    -------
    str
        LaTeX table code with sparkline commands
    """
    # Define bins configuration
    metrics_config = {
        "total_files": {
            "bins": [1, 2, 6, 11, 21, float("inf")],
            "labels": ["1", "2-5", "6-10", "11-20", "21+"],
            "display_name": "Total Files",
        },
        "total_lines": {
            "bins": [0, 11, 51, 101, 251, 501, 1001, float("inf")],
            "labels": [
                "0-10",
                "11-50",
                "51-100",
                "101-250",
                "251-500",
                "501-1000",
                "1001+",
            ],
            "display_name": "Total Lines",
        },
        "total_changes": {
            "bins": [1, 2, 6, 11, 21, float("inf")],
            "labels": ["1", "2-5", "6-10", "11-20", "21+"],
            "display_name": "Total Changes",
        },
        "adoption_ratio": {
            "bins": [0, 0.0001, 1, 5, 20, 101],
            "labels": ["None", "Experimental", "Limited", "Consistent", "Pervasive"],
            "display_name": "Adoption Ratio",
            "filter": ["since_adoption_commits", -1],
        },
    }

    return render_binned_histogram_latex(
        df=df,
        metrics_config=metrics_config,
        caption="Binned distributions of file and commit level adoption metrics",
        label="tab:binned_adoption_metrics",
        scale_factor=2.0,
        command_prefix="binfile",
    )


def generate_report(base_dir: str, args):
    """Main function to generate the report."""
    print("Loading and aggregating data...")
    df = load_and_aggregate_tool_metrics(base_dir)
    report = load_from_report(args, ["adoption_ratio", "since_adoption_commits"])
    df = pd.merge(df, report, on="project", how="inner")

    # filter by organizations if needed
    df = filter_df(df, args, "github_url")

    if df.empty:
        print("No data to process.")
        return

    print(f"Aggregated data for {len(df)} projects.")
    print(df.head())

    latex = render_project_metrics_binned_table(df)
    print(latex)

    # Save using write_file
    write_file("rq2/file_adoption_table.tex", latex)
    print(f"\n\nLaTeX table saved to rq2/file_adoption_table.tex")


if __name__ == "__main__":
    parser = default_args("Generates file-based adoption metrics report")
    args = parser.parse_args()
    set_input_path(args.input)

    generate_report(args.input, args)

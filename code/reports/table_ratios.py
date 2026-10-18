import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import chisquare
from statsmodels.stats.gof import chisquare_effectsize

from reports.common import (
    default_args,
    filter_df,
    find_and_select_report,
    merge_dataframes_by_github_url,
    set_input_path,
    write_file,
)
from reports.latex_utils import (
    number_to_word,
    effect_size_interpretation,
    format_chi2_results_as_latex,
    sanitize_latex_command_name,
    generate_nested_sparkline_commands,
)


def _prepare_and_filter_data(
    df: pd.DataFrame,
    metrics: list[str],
    target: str | None,
    filter_dict: dict | None = None,
) -> pd.DataFrame:
    """
    Consolidates data cleaning, filtering, and type conversion.
    """
    df = df.copy()
    # Apply filters if provided
    if filter_dict:
        for col, val in filter_dict.items():
            if col not in df.columns:
                raise ValueError(f"Filter column '{col}' not found in DataFrame.")
            if isinstance(val, dict):
                df[col] = pd.to_numeric(df[col], errors="coerce")
                if "min" in val:
                    df = df[df[col] >= val["min"]]
                if "max" in val:
                    df = df[df[col] <= val["max"]]
            else:
                if isinstance(val, bool):
                    df[col] = df[col].astype(bool)
                df = df[df[col] == val]

    # Convert metric and target columns to appropriate types
    columns_to_process = metrics + ([target] if target else [])
    for col in columns_to_process:
        if df[col].dtype == "bool" or col == target:
            df[col] = df[col].astype(bool)
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows with NaN values in the specified columns
    df = df.dropna(subset=columns_to_process)

    if len(df) == 0:
        raise ValueError(
            f"No valid data after cleaning for metrics: {', '.join(columns_to_process)}."
        )

    return df


def _calculate_deciles(
    df: pd.DataFrame, decile_metric: str
) -> tuple[pd.DataFrame, list[str], list[float]]:
    """
    Bins a metric into deciles and generates corresponding labels.
    Returns the dataframe, range labels, and bin edges.

    Fix: Ensures unique category labels even when bin edges are close together.
    """
    bins = pd.qcut(df[decile_metric], q=10, duplicates="drop", retbins=True)
    decile_labels, bin_edges = bins[0], bins[1]

    # Create range labels with uniqueness guarantee
    range_labels = []
    seen_labels = set()

    for i in range(len(bin_edges) - 1):
        # Start with the formatted version
        label = f"{bin_edges[i]:,.0f}–{bin_edges[i + 1]:,.0f}"

        # If duplicate, add more precision
        if label in seen_labels:
            # Try with 1 decimal place
            label = f"{bin_edges[i]:,.1f}–{bin_edges[i + 1]:,.1f}"

            # If still duplicate, try 2 decimal places
            if label in seen_labels:
                label = f"{bin_edges[i]:,.2f}–{bin_edges[i + 1]:,.2f}"

                # Last resort: add index suffix
                if label in seen_labels:
                    label = f"{bin_edges[i]:,.2f}–{bin_edges[i + 1]:,.2f} ({i})"

        seen_labels.add(label)
        range_labels.append(label)

    decile_to_range = dict(zip(decile_labels.cat.categories, range_labels))
    df["range_label"] = decile_labels.map(decile_to_range)
    df["range_label"] = pd.Categorical(
        df["range_label"], categories=range_labels, ordered=True
    )

    return df, range_labels, bin_edges


def compute_adoption_by_metric(df, metric, target: str = "tool_use"):
     """
     Computes tool adoption rate across deciles of a given metric.
     Returns adoption data, bin edges, and chi-squared test results.
     """
     df = _prepare_and_filter_data(df, metrics=[metric], target=target)
     df, range_labels, bin_edges = _calculate_deciles(df, metric)

     adoption_by_decile = df.groupby("range_label")[target].mean().reset_index()
     adoption_by_decile["tool_adoption_rate"] = adoption_by_decile[target] * 100

     test_result = print_adoption_by_metric(df, metric, target)

     return adoption_by_decile, bin_edges, test_result





def run_chi2_test(metric_name, observed, expected):
    """Run chi-squared test and return results."""
    chi2_stat, p_value = chisquare(observed, expected)
    effect_size = chisquare_effectsize(observed, expected)
    return {
        "metric": metric_name,
        "chi2": chi2_stat,
        "p_value": p_value,
        "effect_size": effect_size,
        "interpretation": effect_size_interpretation(effect_size),
    }


def print_adoption_by_metric(df, metric, target):

     adoption_by_decile_full = (
         df.groupby("range_label")[target].agg(["mean", "count", "sum"]).reset_index()
     )

     projects_sum = adoption_by_decile_full["count"].sum()
     adoptors_sum = adoption_by_decile_full["sum"].sum()

     adoption_by_decile_full["expected_ratio"] = (
         adoption_by_decile_full["count"] / projects_sum
     )
     adoption_by_decile_full["expected_frequency"] = (
         adoption_by_decile_full["expected_ratio"] * adoptors_sum
     )

     # observed_sum = adoption_by_decile_full['sum'].sum()
     # expected_sum = adoption_by_decile_full['expected_ratio'].sum()
     # print(observed_sum, expected_sum)

     print(metric)
     observed_frequency = adoption_by_decile_full["sum"].tolist()
     expected_frequency = adoption_by_decile_full["expected_frequency"].tolist()

     print(sum(observed_frequency), sum(expected_frequency))

     print(f"observed_frequency_{metric}={observed_frequency}")
     print(f"expected_frequency_{metric}={expected_frequency}")
     print(adoption_by_decile_full)

     # Run chi-squared test
     test_result = run_chi2_test(metric, np.array(observed_frequency), np.array(expected_frequency))
     print(f"Chi-squared: {test_result['chi2']:.4f}, p-value: {test_result['p_value']:.6f}, Effect: {test_result['interpretation']}")

     return test_result








def format_number_compact(num: float) -> str:
    """
    Format numbers with K/M suffixes for large values.
    Examples:
        123 -> "123"
        1234 -> "1.2K"
        12345 -> "12K"
        123456 -> "123K"
        1234567 -> "1.2M"
        12345678 -> "12M"
    """
    if abs(num) < 10:
        return f"{num:.1f}"
    elif abs(num) < 1000:
        return f"{num:.0f}"
    elif abs(num) < 1_000_000:
        # Thousands
        k_value = num / 1000
        if k_value < 10:
            return f"{k_value:.1f}K"
        else:
            return f"{k_value:.0f}K"
    else:
        # Millions
        m_value = num / 1_000_000
        if m_value < 10:
            return f"{m_value:.1f}M"
        else:
            return f"{m_value:.0f}M"


def generate_latex_table(
     df: pd.DataFrame,
     metrics_to_plot: list[str],
     focus_metrics: list[str],
     screen_names: dict[str, str],
     scale_factor: float = 2,
     prefix: str = "",
     sparkonly: bool = False,
):
    """
    Generates a single LaTeX table with all metrics, including sparklines.
    Returns tuple of (latex_code, chi2_results).
    """
    latex_commands = []
    table_rows = []
    merge_size = len(focus_metrics) + 1
    chi2_results = []

    # Collect all data first
    all_data = {}
    plot_metric: list[str] = [sanitize_latex_command_name(m) for m in metrics_to_plot]
    foc_metric: list[str] = [sanitize_latex_command_name(f) for f in focus_metrics]

    for metric, sanitized_metric in zip(metrics_to_plot, plot_metric):
         all_data[sanitized_metric] = {}
         screen_names[sanitized_metric] = screen_names[metric]
         for focus_metric, sanitized_focus_metric in zip(focus_metrics, foc_metric):
             adoption_data, bin_edges, test_result = compute_adoption_by_metric(
                 df, metric, target=focus_metric
             )
             chi2_results.append(test_result)

             all_data[sanitized_metric][sanitized_focus_metric] = {
                 "adoption": adoption_data,
                 "bin_edges": bin_edges,
             }
             if focus_metric in screen_names:
                 screen_names[sanitized_focus_metric] = screen_names[focus_metric]
             else:
                 screen_names[sanitized_focus_metric] = focus_metric

    # Generate sparkline commands
    # Build a nested dict with values extracted from adoption DataFrames
    sparkline_data = {}
    for metric in plot_metric:
        sparkline_data[metric] = {}
        for focus_metric in foc_metric:
            adoption_df = all_data[metric][focus_metric]["adoption"]
            values = [
                row['tool_adoption_rate'] * scale_factor
                for _, row in adoption_df.iterrows()
            ]
            sparkline_data[metric][focus_metric] = values
    
    # Use generic sparkline builder
    def build_table_sparkline_name(path):
        metric, focus_metric = path
        return f"{prefix}{metric}{focus_metric}Sparkline"
    
    sparkline_cmds = generate_nested_sparkline_commands(
        sparkline_data,
        build_table_sparkline_name,
        scale_factor=1.0  # Already scaled above
    )
    latex_commands.extend(sparkline_cmds)

    if sparkonly:
        return "\n".join(latex_commands)

    # Generate LaTeX commands for values
    for metric in plot_metric:
        for focus_metric in foc_metric:
            data = all_data[metric][focus_metric]
            bin_edges = data["bin_edges"]
            adoption = data["adoption"]

            # Commands for bin edges (decile boundaries)
            for i in range(len(bin_edges) - 1):
                decile_word = number_to_word(i)
                cmd_name = (
                    f"\\{prefix}{metric}{focus_metric}Decile{decile_word}Boundary"
                )
                value = format_number_compact(bin_edges[i])  # i + 1 ? +1]:.0f}"
                latex_commands.append(f"\\newcommand{{{cmd_name}}}{{{value}}}")

            # Commands for adoption rates
            for i, row in adoption.iterrows():
                decile_word = number_to_word(i)
                cmd_name = f"\\{prefix}{metric}{focus_metric}Decile{decile_word}"
                value = f"{row['tool_adoption_rate']:.2f}\\%"
                latex_commands.append(f"\\newcommand{{{cmd_name}}}{{{value}}}")

    # Generate table header
    table_header = """
\\begin{table}[ht]
\\centering
\\caption{File adoption statistics versus project-level metrics, by deciles}
\\label{tab:file_adoption}
\\scriptsize
"""
    table_header += "\\begin{tabular}{l|l|l|" + "c" * 10 + "}\n\\hline\n"
    table_header += "\\toprule"
    table_header += "\\textbf{Metric} & \\textbf{Aspect} & "
    table_header += " \\textbf{Sparkline} & "
    table_header += " & ".join([f"\\textbf{{D{i + 1}}}" for i in range(10)])
    table_header += " \\\\\n\\midrule\n"

    # Generate table rows
    for metric in plot_metric:
        metric_display = (
            screen_names[metric]
            if metric in screen_names
            else metric.replace("_", " ").title()
        )

        # Get number of actual deciles (might be less than 10 due to duplicates)
        first_focus = foc_metric[0]
        n_deciles = len(all_data[metric][first_focus]["adoption"])

        # Row 1: Decile boundaries
        row1 = "\\midrule\n"
        row1 += f"\\multirow{{{merge_size}}}{{*}}{{{metric_display}}} & Deciles & "
        row1 += f" \\multirow{{{merge_size}}}{{*}}{{}} & "
        decile_cells = []
        for i in range(n_deciles):
            decile_word = number_to_word(i)
            decile_cells.append(
                f"\\{prefix}{metric}{first_focus}Decile{decile_word}Boundary"
            )
        # Pad with empty cells if fewer than 10 deciles
        decile_cells.extend([""] * (10 - n_deciles))
        row1 += " & ".join(decile_cells)
        # row1 += " \\\\\n\\cline{3-13}\n"
        row1 += " \\\\\n "  # \\cline{3-13}\n"

        for focus_metric in foc_metric:
            focus_metric_display = (
                screen_names[focus_metric]
                if focus_metric in screen_names
                else focus_metric.replace("_", " ").title()
            )

            # Row 2: File adoption ratio (total_files)
            row2 = f" & {focus_metric_display} & "
            row2 += f" \\{prefix}{metric}{focus_metric}Sparkline & "
            cells = []
            for i in range(n_deciles):
                decile_word = number_to_word(i)
                cells.append(f"\\{prefix}{metric}{focus_metric}Decile{decile_word}")
            cells.extend([""] * (10 - n_deciles))
            row2 += " & ".join(cells)
            # row2 += f" \\\\\n\\cline{{3-13}}\n"
            row2 += " \\\\\n "  # \\cline{3-13}\n"
            row1 += row2

        table_rows.append(row1)

    # Combine everything
    table_end = "\\bottomrule\n\\end{tabular}\n\\end{table}"

    full_latex = "% LaTeX Commands\n"
    full_latex += "\n".join(latex_commands)
    full_latex += "\n\n% Table\n"
    full_latex += table_header
    full_latex += "".join(table_rows)
    full_latex += table_end

    return full_latex, chi2_results


import pandas as pd


def classify_by_thresholds(
    df: pd.DataFrame, metric: str, thresholds: dict[str, float]
) -> pd.DataFrame:
    """
    Classify values in a DataFrame column into boolean columns based on thresholds.

    Parameters
    ----------
    df : pd.DataFrame
        The input DataFrame.
    metric : str
        The name of the numeric column to classify.
    thresholds : dict
        A dictionary mapping category names to maximum threshold values.
        Example: {'low': 10, 'medium': 20, 'high': 30}

    Returns
    -------
    pd.DataFrame
        A copy of the DataFrame with new boolean columns for each category.
    """
    # Sort thresholds by ascending value to ensure correct order
    thresholds = dict(sorted(thresholds.items(), key=lambda x: x[1]))

    df = df.copy()
    prev_max = float("-inf")

    for category, max_val in thresholds.items():
        # Boolean mask for the range
        mask = (df[metric] > prev_max) & (df[metric] <= max_val)
        # df[f"{metric}_{category}"] = mask
        df[category] = mask
        prev_max = max_val

    # Optional: handle values above the last threshold
    df[f"{metric}_above_max"] = df[metric] > prev_max

    return df


def exclude_young_projects(df: pd.DataFrame, age=1) -> pd.DataFrame:
     return df[df["age_years"] > age]





def all_metric_report(
    df, scale: float = 2, prefix="", focus_metrics=["tool_use"], sparkonly: bool = False, partial_path: str | None = None
):
    metrics_to_plot = [
        "code_lines",
        "contributors",
        "commits",
        "total_issues",
        "total_pull_requests",
        "age_years",
    ]
    # focus_metrics = ['tool_use'] #, 'commit_use']
    screen_names = {
        f"code_lines": "LOC",
        "age_years": "Age (years)",
        "contributors": "Contributors",
        "commits": "Commits",
        "total_issues": "Issues",
        "total_pull_requests": "Pull Request",
        "tool_use": "File",
        "commit_use": "Commit",
    }

    latex_output, chi2_results = generate_latex_table(
        df,
        metrics_to_plot,
        focus_metrics,
        screen_names,
        scale_factor=scale,
        prefix=prefix,
        sparkonly=sparkonly,
    )
    print(latex_output)

    # Save to file using write_file if partial_path is provided, else use old behavior
    if partial_path:
        write_file(partial_path, latex_output)
        print(f"\n\nLaTeX table saved to {partial_path}")

        # Also save chi-squared results
        chi2_latex = format_chi2_results_as_latex(chi2_results)
        write_file("rq2/file_effect_sizes.tex", chi2_latex)
        print(f"Chi-squared results saved to rq2/file_effect_sizes.tex")
    else:
        output_path = Path(f"{prefix}decile_report.tex")
        with open(output_path, "w") as f:
            f.write(latex_output)
        print(f"\n\nLaTeX table saved to {output_path}")
        subprocess.run(f"cat {prefix}decile_report.tex | pbcopy", shell=True, check=True)
        print("\n\n ready to paste!")


def main_report(df):
    all_metric_report(df, 3.5, partial_path="rq2/ratio_tables.tex")


def no_young_report(df):
     df = exclude_young_projects(df, 1)
     all_metric_report(df, 1.5, prefix="oneplus", sparkonly=True, partial_path="rq2/adoption_sparklines_oneplus.tex")


def no_young_report_2(df):
     df = exclude_young_projects(df, 2)
     all_metric_report(df, 1.5, prefix="twoplus", sparkonly=True, partial_path="rq2/adoption_sparklines_twoplus.tex")



if __name__ == "__main__":
     # need to account for .gitignored files here!!!...
     # maybe just take the original file for this, not the combined bla bla...
     # consolidated data does not have gitignore only files
     # or commit only files.
     # should have them with bools from the directories
     # change consolidate_metrics for this ...
     parser = default_args("Generates decile-based adoption statistics ")
     parser.add_argument(
         "--metric-file",
         default="../data/projects-29-08.csv",
         help="Path where project metrics are, if needed",
     )
     parser.add_argument(
         "--no-one-year",
         action="store_true",
         help="if set, exclude projects younger than one year",
     )
     parser.add_argument(
         "--no-two-year",
         action="store_true",
         help="if set, exclude projects younger than two years",
     )

     args = parser.parse_args()
     set_input_path(args.input)

     path = find_and_select_report(Path(args.input))
     metrics_path = Path(args.metric_file)
     df = merge_dataframes_by_github_url(metrics_path, path)

     # filter by organizations if needed
     df = filter_df(df, args, "github_url")
     # also include ignored tools
     df["tool_use"] = df["tool_use"] | df["ignored_tools"]

     if args.no_one_year:
         no_young_report(df)
     elif args.no_two_year:
         no_young_report_2(df)
     else:
         main_report(df)

     # commit_ratio_report(df)

import numpy as np
import pandas as pd
from pathlib import Path
from scipy.stats import chisquare
from statsmodels.stats.gof import chisquare_effectsize

from reports.common import default_args, filter_df, find_and_select_report, merge_dataframes_by_github_url, set_input_path, write_file
from reports.latex_utils import (
    effect_size_interpretation,
    format_chi2_results_as_latex,
    sanitize_latex_command_name,
    generate_nested_sparkline_commands,
)
import subprocess

def compute_ratio_combinations(
    df: pd.DataFrame,
    masks: dict[str, pd.Series],
    metrics: list[str],
    decile_metrics: list[str]|None = None,
    scale: float = 0.5
) -> tuple[pd.DataFrame, dict[str, list[float]]]:
    """
    Compute adoption ratios for all combinations of masks and metrics.

    Parameters
    ----------
    df : pd.DataFrame
        The input dataframe
    masks : Dict[str, pd.Series]
        Dictionary mapping mask names to boolean Series
    metrics : list[str]
        List of metric column names to calculate ratios for (table columns)
    decile_metrics : list[str], optional
        List of metrics to compute decile-based adoption for (adds sparkline rows)

    Returns
    -------
    tuple[pd.DataFrame, Dict]
        - DataFrame with masks as rows, metrics as columns, plus a 'count' column
        - Dictionary with sparkline data: {mask_name: {decile_metric: {table_metric: [values]}}}
    """
    results = []
    sparkline_data = {}

    print('########################')

    for mask_name, mask in masks.items():
        row_data = {'mask_name': mask_name}

        # Apply mask to dataframe
        df_filtered = df[mask]
        row_data['count'] = len(df_filtered)

        # Calculate ratio for each metric
        abs_values = []
        for metric in metrics:
            if metric not in df.columns:
                raise ValueError(f"Metric column '{metric}' not found in DataFrame.")

            if len(df_filtered) == 0:
                ratio = 0.0
            else:
                # Convert to boolean and calculate percentage
                metric_col = df_filtered[metric].astype(bool)
                ratio = metric_col.mean() * 100

                col_sum = int(metric_col.sum())
                print(mask_name, metric, col_sum)
                abs_values.append(col_sum)

            row_data[metric] = ratio

        results.append(row_data)
        print(mask_name, abs_values)

        # Compute decile-based sparklines if requested
        if decile_metrics:
            sparkline_data[mask_name] = {}

            for decile_metric in decile_metrics:
                if decile_metric not in df.columns:
                    raise ValueError(f"Decile metric '{decile_metric}' not found in DataFrame.")

                sparkline_data[mask_name][decile_metric] = {}

                for table_metric in metrics:
                    # Compute adoption by decile for this combination
                    adoption_rates = _compute_decile_adoption(
                        df_filtered,
                        decile_metric,
                        table_metric
                    )
                    sparkline_data[mask_name][decile_metric][table_metric] = [ar * scale for ar in adoption_rates]

    # Create DataFrame
    result_df = pd.DataFrame(results)

    # Reorder columns: mask_name, count, then metrics
    cols = ['mask_name', 'count'] + metrics
    result_df = result_df[cols]

    # print(result_df)

    return result_df, sparkline_data


def _compute_decile_adoption(
    df: pd.DataFrame,
    decile_metric: str,
    target_metric: str
) -> list[float]:
    """
    Helper function to compute adoption rates across deciles of a metric.

    Returns
    -------
    list[float]
        List of adoption rates (0-100) for each decile
    """
    if len(df) == 0:
        return []

    # Prepare data
    df_clean = df[[decile_metric, target_metric]].copy()
    df_clean[decile_metric] = pd.to_numeric(df_clean[decile_metric], errors='coerce')
    df_clean[target_metric] = df_clean[target_metric].astype(bool)
    df_clean = df_clean.dropna()

    if len(df_clean) < 10:
        # Not enough data for deciles, return overall rate
        overall_rate = df_clean[target_metric].mean() * 100
        return [overall_rate]

    # Create deciles
    try:
        decile_labels = pd.qcut(df_clean[decile_metric], q=10, duplicates='drop')
        df_clean['decile'] = decile_labels

        # Calculate adoption rate per decile
        adoption_by_decile = df_clean.groupby('decile')[target_metric].mean() * 100
        return adoption_by_decile.tolist()
    except:
        # If qcut fails, return overall rate
        overall_rate = df_clean[target_metric].mean() * 100
        return [overall_rate]





def run_chi2_test_on_ratio_df(ratio_df: pd.DataFrame, metric_cols: list[str]) -> list[dict]:
    """
    Run chi-squared tests on the ratio DataFrame.
    Tests whether the distribution of projects across metric categories is uniform.

    Parameters
    ----------
    ratio_df : pd.DataFrame
        DataFrame from compute_ratio_combinations
    metric_cols : list[str]
        List of metric columns to test

    Returns
    -------
    list[dict]
        List of test results for each mask
    """
    results = []

    for idx, row in ratio_df.iterrows():
        mask_name = row['mask_name']

        # Get the observed counts for this mask across all metrics
        observed = np.array([row[metric] for metric in metric_cols], dtype=float)

        # Only run test if we have data
        if observed.sum() == 0:
            continue

        # Expected uniform distribution
        expected = np.full_like(observed, observed.sum() / len(observed), dtype=float)

        chi2_stat, p_value = chisquare(observed, expected)
        effect_size = chisquare_effectsize(observed, expected)

        results.append({
            "mask_name": mask_name,
            "chi2": chi2_stat,
            "p_value": p_value,
            "effect_size": effect_size,
            "interpretation": effect_size_interpretation(effect_size),
        })

    return results





def render_ratio_table_latex(
     ratio_df: pd.DataFrame,
     sparkline_data: dict,
     decile_metrics: list[str] = None,
     metric_display_names: dict[str, str] = None,
     decile_metric_display_names: dict[str, str] = None,
     caption: str = "Overall adoption ratios by category",
     label: str = "tab:overall_commit_ratios",
     scale_factor: float = 2.0
 ) -> tuple[str, list[dict]]:
    """
    Render a ratio combinations table as LaTeX with sparklines.
    Returns tuple of (latex_code, chi2_results).

    Parameters
    ----------
    ratio_df : pd.DataFrame
         DataFrame from compute_ratio_combinations
    sparkline_data : Dict
         Sparkline data from compute_ratio_combinations
    decile_metrics : list[str], optional
         List of decile metrics (for row labels)
    metric_display_names : Dict[str, str], optional
         Mapping of metric column names to display names
    decile_metric_display_names : Dict[str, str], optional
         Mapping of decile metric names to display names
    caption : str
         Table caption
    label : str
         LaTeX label for referencing
    scale_factor : float
         Scale factor for sparkline values

    Returns
    -------
    tuple[str, list[dict]]
         LaTeX table code with sparkline commands and chi-squared test results
    """
    if metric_display_names is None:
        metric_display_names = {}
    if decile_metric_display_names is None:
        decile_metric_display_names = {}

    # Get metric columns (all except mask_name and count)
    metric_cols = [col for col in ratio_df.columns if col not in ['mask_name', 'count']]

    # Run chi-squared tests
    chi2_results = run_chi2_test_on_ratio_df(ratio_df, metric_cols)

    # Generate sparkline LaTeX commands
    latex_commands = []

    if decile_metrics and sparkline_data:
        # Use generic sparkline builder with custom naming
        def build_sparkline_name(path):
            mask_name, decile_metric, table_metric = path
            safe_mask = sanitize_latex_command_name(mask_name)
            safe_decile = sanitize_latex_command_name(decile_metric)
            safe_table = sanitize_latex_command_name(table_metric)
            return f"{safe_mask}{safe_decile}{safe_table}Sparkline"
        
        sparkline_cmds = generate_nested_sparkline_commands(
            sparkline_data,
            build_sparkline_name,
            scale_factor=scale_factor
        )
        latex_commands.extend(sparkline_cmds)


    latex = "\n\n% Table\n"

    latex += "\\begin{table}[ht]\n"
    latex += "\\scriptsize\n"
    latex += "\\centering\n"
    latex += f"\\caption{{{caption}}}\n"
    latex += f"\\label{{{label}}}\n"
    latex += "\\begin{tabular}{l|r|" + "r" * len(metric_cols) + "}\n"
    latex += "\\hline\n"
    latex += "\\toprule\n"

    # Header row
    latex += "\\textbf{Category} & \\textbf{N} "
    for metric in metric_cols:
        display_name = metric_display_names.get(metric, metric.replace('_', ' ').title())
        latex += f"& \\textbf{{{display_name}}} "
    latex += "\\\\\n"
    latex += "\\midrule\n"

    # Data rows
    for idx, row in ratio_df.iterrows():
        mask_name = row['mask_name']

        latex += f"{mask_name} & {row['count']:,} "
        for metric in metric_cols:
            # Build safe command name, same naming convention as before
            safe_mask = mask_name.replace(' ', '').replace('>', 'gt').replace('<', 'lt').replace('=', 'eq')
            safe_table = metric.replace('_', '')

            # Reference command for the main value
            cmd_name = f"\\{safe_mask}{safe_table}Value"

            # Generate the actual command definition once (above the table)
            value_cmd = f"\\newcommand{{{cmd_name}}}{{{row[metric]:.2f}\\%}}"
            latex_commands.append(value_cmd)

            # Use the command in the table
            latex += f"& {cmd_name} "
        latex += "\\\\\n"


        # Sparkline rows for each decile metric
        if decile_metrics and sparkline_data and mask_name in sparkline_data:
            for decile_metric in decile_metrics:
                decile_display = decile_metric_display_names.get(
                    decile_metric,
                    decile_metric.replace('_', ' ').title()
                )

                latex += f"\\quad {decile_display} & "

                for metric in metric_cols:
                    # Generate sparkline command reference
                    safe_mask = mask_name.replace(' ', '').replace('>', 'gt').replace('<', 'lt').replace('=', 'eq')
                    safe_decile = decile_metric.replace('_', '')
                    safe_table = metric.replace('_', '')

                    cmd_name = f"\\{safe_mask}{safe_decile}{safe_table}Sparkline"
                    latex += f"& {cmd_name} "

                latex += "\\\\\n"

        # Add spacing between mask groups
        if idx < len(ratio_df) - 1:
            latex += "\\midrule\n"

    # Footer
    latex += "\\bottomrule\n"
    latex += "\\end{tabular}\n"
    latex += "\\end{table}\n"

    # Build commands
    latex_full = "% LaTeX Commands\n"
    latex_full += "\n".join(latex_commands)
    latex_full += latex

    return latex_full, chi2_results


def classify_by_thresholds(df: pd.DataFrame, metric: str, thresholds: dict[str,float]) -> pd.DataFrame:
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
    thresholds = dict(sorted(thresholds.items(), key=lambda x: x[1]))
    df = df.copy()
    df[metric] = df[metric].fillna(-1)
    prev_max = float('-inf')

    for i, (category, max_val) in enumerate(thresholds.items()):
        if i == 0:
            mask = df[metric] <= max_val
        else:
            mask = (df[metric] > prev_max) & (df[metric] <= max_val)
        df[category] = mask
        prev_max = max_val

    # Handle NaNs and values above the last threshold
    df["Missing"] = df[metric].isna()
    df[f"{metric}_above_max"] = df[metric] > prev_max

    return df

if __name__ == "__main__":

     parser = default_args("Generates decile-based adoption statistics ")
     parser.add_argument("--metric-file", default="../data/projects-29-08.csv", help="Path where project metrics are, if needed")

     args = parser.parse_args()
     set_input_path(args.input)

     path = find_and_select_report(Path(args.input))
     metrics_path = Path(args.metric_file)
     df = merge_dataframes_by_github_url(metrics_path, path)

     # filter by organizations if needed
     df = filter_df(df, args, "github_url")
     # don't include ignored tools by default
     # df['tool_use'] = df['tool_use'] | df['ignored_tools']

     df = df[df['since_adoption_commits'] > 10]

     thresholds = {'None': 0.0001, 'Experimental': 1, 'Limited': 5, 'Consistent': 20, 'Pervasive': 101}
     # thresholds = {'None': 0.0001, 'Experimental': 1, 'Limited': 5, 'Consistent': 20, 'Pervasive': 50, 'Majority': 75, 'Extreme': 101}

     df = classify_by_thresholds(df, 'adoption_ratio', thresholds)

     # Define masks as dictionary
     masks = {
          "All Files": df['tool_use'] | df['ignored_tools'],
          "All Files with commits": (df['adoption_ratio'] > 0) & (df['tool_use'] | df['ignored_tools']),
          "File level": (df['adoption_ratio'] > 0) & df['tool_use'],
          "Files ignored only": (df['adoption_ratio'] > 0) &  df['ignored_tools']  & ~df['tool_use'],
          "Commits only": df['commit_use'] & ~df['tool_use'] & ~df['ignored_tools'],
           # "All": df['commit_use'] | df['tool_use'] | df['ignored_tools']
     }

     metrics = list(thresholds.keys())
     decile_metrics = ['code_lines', 'contributors', 'age_years']
     # decile_metrics = ['code_lines', 'contributors', 'commits', 'total_issues', 'total_pull_requests', 'age_years']
     decile_names = {'code_lines': 'by LOC', 'contributors': 'by Contributors', 'age_years': 'by Age'}
     decile_names = {f'code_lines' : "by LOC", 'age_years': "by Age", 'contributors': "by Contributors", 'commits': "by Commits", 'total_issues': "by Issues", 'total_pull_requests': "by Pull Request"}



     # Compute ratios
     result, sparklines = compute_ratio_combinations(df, masks, metrics, decile_metrics)
     print(result)
     print("\n")


     latex_output, chi2_results = render_ratio_table_latex(
         result,
         sparklines,
         decile_metrics=decile_metrics,
         decile_metric_display_names=decile_names,
         caption="Commit adoption across different project categories"
     )
     print(latex_output)

     # Save using write_file
     write_file("rq2/adoption_ratio_table.tex", latex_output)
     print(f"\n\nLaTeX table saved to rq2/adoption_ratio_table.tex")

     # Also save chi-squared results
     chi2_latex = format_chi2_results_as_latex(chi2_results)
     write_file("rq2/commit_effect_sizes.tex", chi2_latex)
     print(f"Chi-squared results saved to rq2/commit_effect_sizes.tex")

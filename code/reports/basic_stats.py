from pathlib import Path

import pandas as pd
from reports.common import default_args, filter_df, find_and_select_report, set_input_path, write_file


def calculate_metrics_for_group(
    df: pd.DataFrame, group_name: str
) -> dict[str, int | float | str]:
    """
    Calculate all metrics from print_basic_report for a given group.

    Returns a dictionary with all metric values.
    """

    def percent_df(dataframe: pd.DataFrame, bool_metric: str | pd.Series, name: str):
        if not dataframe.empty:
            # Check if bool_metric is a string (column name) or a boolean array/Series
            if isinstance(bool_metric, str):
                n_matching = dataframe[bool_metric].sum()
            else:
                # bool_metric is already a boolean Series/array
                n_matching = bool_metric.sum()

            metrics[f"{name}_denominator"] = len(dataframe)
            metrics[f"{name}_count"] = n_matching
            metrics[f"{name}_percent"] = (n_matching / len(dataframe)) * 100
        else:
            metrics[f"{name}_count"] = 0
            metrics[f"{name}_percent"] = 0

    # Ensure boolean columns
    bool_columns = ["tool_use", "commit_use", "sampled", "ignored_tools"]
    for col in bool_columns:
        if col in df.columns:
            df[col] = df[col].astype(bool)

    metrics: dict[str, int | float | str] = {"group": group_name}

    total_projects = len(df)
    metrics["total_projects"] = total_projects

    if total_projects == 0:
        # Return zeros for empty groups
        return {**metrics, **{f"metric_{i}": 0.0 for i in range(1, 10)}}

    # Metric 1: Percent of projects where tool_use is True
    percent_df(df, "tool_use", "tool_use")

    # Metric 2: Percent of tool_use=True projects where commit_use is also True
    tool_use_true_df = df[df["tool_use"]]
    percent_df(tool_use_true_df, "commit_use", "commit_use_in_tool")

    # Metric 3: Percent of tool_use=False projects where ignored_tools is True
    hidden_users = (~df["tool_use"]) & (df["ignored_tools"])
    percent_df(df, hidden_users, "ignored_tools_only")

    # Metric 4: Percent of ignored_tools is True
    percent_df(df, "ignored_tools", "ignored_tools_all")

    # metric 4: total tool users (ignored and files)
    # total_tool_users = df[(df['tool_use']) | (df['ignored_tools'])]
    # percent_df(total_tool_users, 'ignored_tools', 'all_file_based_use')
    total_tool_users_mask = (df["tool_use"]) | (df["ignored_tools"])
    percent_df(df, total_tool_users_mask, "all_file_based_use")

    total_tool_users_mask = (df["tool_use"]) | (df["ignored_tools"])
    percent_df(df[total_tool_users_mask], "commit_use", "commit_use_all_file_users")

    # Metric 5: Percent of sampled=True and tool_use=False projects where commit_use is True
    # sampled_and_not_tool_use = df[(df['sampled']) & (~df['tool_use'])]
    # percent_df(sampled_and_not_tool_use, 'commit_use', 'sampled_commit_use')

    # Metric 5: Percent of sampled=True, tool_use=False, and ignored_tools=False projects where commit_use is True
    sampled_and_not_tool_use_all = df[
        (df["sampled"]) & (~df["tool_use"]) & (~df["ignored_tools"])
    ]
    percent_df(sampled_and_not_tool_use_all, "commit_use", "sampled_commit_use")

    # Metric 8: Refined extrapolated overall % of users
    known_users_count = df[df["tool_use"] | df["ignored_tools"]].shape[0]
    extrapolation_group_total = df[(~df["tool_use"]) & (~df["ignored_tools"])].shape[0]
    metrics["extrapolation_group_count"] = extrapolation_group_total
    sampled_extrapolation_group = df[
        (df["sampled"]) & (~df["tool_use"]) & (~df["ignored_tools"])
    ]

    commit_use_rate_in_sample = 0.0
    if not sampled_extrapolation_group.empty:
        commit_use_rate_in_sample = sampled_extrapolation_group[
            "commit_use"
        ].sum() / len(sampled_extrapolation_group)

    extrapolated_commit_users = extrapolation_group_total * commit_use_rate_in_sample
    metrics["extrapolated_commit_users"] = extrapolated_commit_users

    total_users_refined = known_users_count + extrapolated_commit_users
    metrics["known_users_count"] = known_users_count
    metrics["all_total_users_estimate"] = total_users_refined
    metrics["overall_adoption_rate_percent"] = (
        total_users_refined / total_projects
    ) * 100

    high_estimate_commit_users = extrapolated_commit_users / (
        metrics["commit_use_all_file_users_percent"] / 100
    )
    high_estimate_total_users_refined = known_users_count + high_estimate_commit_users
    metrics["high_estimate_commit_users"] = high_estimate_commit_users
    metrics["high_estimate_total_users_estimate"] = high_estimate_total_users_refined
    metrics["high_estimate_overall_adoption_rate_percent"] = (
        high_estimate_total_users_refined / total_projects
    ) * 100

    return metrics


def metrics_to_latex(metrics):

    # -*- coding: utf-8 -*-

    output = []
    output.append("\n%" + "=" * 80)
    output.append("% LATEX COMMAND DEFINITIONS")
    output.append("% GENERATED BY reports/basic_stats.py, can be updated")
    output.append("\n%" + "=" * 80)

    def to_camel_case(s: str) -> str:
        """Convert snake_case to CamelCase."""
        return "".join(word.capitalize() for word in s.split("_"))

    # Generate LaTeX command definitions
    for key, value in metrics.items():
        if key == "group":
            continue
        cmd_name = to_camel_case(key)
        if key.endswith("percent"):
            # float formatting with commas and 2 decimal places (e.g. 12,345.67)
            val_str = f"{value:,.2f}\\%"
        else:
            # integer-style formatting with commas (e.g. 12,345)
            val_str = f"{int(round(value)):,}"

        output.append(f"\\newcommand\\{cmd_name}{{{val_str}\\xspace}}")

    output.append("\n%" + "=" * 80)
    output.append("% LATEX TABLE")
    output.append(
        "% GENERATED BY reports/basic_stats.py, but manually modified, best not to update if possible"
    )
    output.append("\n%" + "=" * 80)

    # Define boolean column mapping logic
    def bool_flags(name):
        """Return (tool, ignored, sampled, commit) with checkmarks or crosses."""
        tool = "\\checkmark" if "tool" in name else "\\xmark"
        ignored = "\\checkmark" if "ignored" in name else "\\xmark"
        sampled = "\\checkmark" if "sampled" in name else "\\xmark"
        commit = "\\checkmark" if "commit" in name else "\\xmark"
        return tool, ignored, sampled, commit

    # Group metrics into denominator/count/percent sets
    groups = {}
    for key in metrics.keys():
        if key == "group":
            continue
        base = key
        for suffix in ["_denominator", "_count", "_percent"]:
            if key.endswith(suffix):
                base = key[: -len(suffix)]
                groups.setdefault(base, {})[suffix[1:]] = key
                break
        else:
            groups.setdefault(key, {})["single"] = key

    # Split into grouped and single metrics
    grouped_metrics = {
        b: g
        for b, g in groups.items()
        if any(k in g for k in ["denominator", "count", "percent"])
    }
    single_metrics = {b: g for b, g in groups.items() if list(g.keys()) == ["single"]}

    output.append(r"\begin{tabular}{lccccccc}")
    output.append(r"\toprule")
    output.append(
        r"Metric & Tool & Ignored & Sampled & Commit & Count & Denominator & Percent \\"
    )
    output.append(r"\midrule")

    # Add grouped metrics
    for base, parts in sorted(grouped_metrics.items()):
        tool, ignored, sampled, commit = bool_flags(base)
        count_cmd = f"\\{to_camel_case(parts['count'])}" if "count" in parts else "-"
        denom_cmd = (
            f"\\{to_camel_case(parts['denominator'])}"
            if "denominator" in parts
            else "-"
        )
        percent_cmd = (
            f"\\{to_camel_case(parts['percent'])}" if "percent" in parts else "-"
        )
        metric_name = base.replace("_", " ").title()
        output.append(
            f"{metric_name} & {tool} & {ignored} & {sampled} & {commit} & {count_cmd} & {denom_cmd} & {percent_cmd} \\\\"
        )

    output.append(r"\midrule")
    output.append(r"\multicolumn{8}{l}{\textbf{Other Metrics}} \\")
    for base, parts in sorted(single_metrics.items()):
        cmd_name = to_camel_case(base)
        metric_name = base.replace("_", " ").title()
        output.append(f"{metric_name} & - & - & - & - & \\{cmd_name} & - & - \\\\")

    output.append(r"\bottomrule")
    output.append(r"\end{tabular}")
    output.append("=" * 80)
    
    return "\n".join(output)


if __name__ == "__main__":
    parser = default_args("Computes basic adoption statistics ")
    args = parser.parse_args()
    set_input_path(args.input)
    path = find_and_select_report(Path(args.input))

    df: pd.DataFrame = pd.read_csv(path)
    total_analyzed_projects = len(df)
    df = filter_df(df, args, "github_url")

    # Print basic report using the common function
    metrics = calculate_metrics_for_group(df, "Overall")
    metrics["total_analyzed_projects"] = total_analyzed_projects
    print("\n" + "=" * 80)
    print("BASIC REPORT")
    print("=" * 80)
    for key, value in metrics.items():
        if key != "group":
            print(f"{key.replace('_', ' ').title()}: {value:.2f}")
    print("=" * 80)

    latex_output = metrics_to_latex(metrics)
    write_file("rq1/statistics.tex", latex_output)

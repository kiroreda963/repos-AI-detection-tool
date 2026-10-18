#!/usr/bin/env python3
"""
Commit sizes analysis script.

This script loads commit statistics from two CSV file types:
- commit_stats_files.csv: File-level commit statistics
- commit_stats_main.csv: Main (aggregate) commit statistics

It separates data into two categories:
1. Non-adopters: Projects in the "non_adopters" directory
2. Adopters: Projects in all other directories (excluding tool_commit_adopters,
             ignored_tools_commit_adopters, and commit_only_adopters)

Results in 4 lists of DataFrames:
- file_stats_non_adopters: File-level stats for non-adopters
- main_stats_non_adopters: Aggregate stats for non-adopters
- file_stats_adopters: File-level stats for adopters
- main_stats_adopters: Aggregate stats for adopters
"""

import os
import pandas as pd
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from reports.common import default_args, load_data_basic, generate_visualization_report
from utils.commit_type_classifier import extract_commit_type, get_commit_type_categories

from scipy.stats import shapiro, normaltest, mannwhitneyu
from cliffs_delta import cliffs_delta


def get_include_dirs(base_dir: str, exclude_list: list[str]) -> list[str]:
    """
    List all directories in base_dir and filter out the ones in exclude_list.

    Args:
        base_dir: Root directory to scan
        exclude_list: List of directory names to exclude

    Returns:
        List of directory paths to include
    """
    include_dirs = []
    try:
        for entry in os.scandir(base_dir):
            if entry.is_dir() and entry.name not in exclude_list:
                include_dirs.append(entry.name)
    except PermissionError:
        pass
    return include_dirs


def prepare_distribution_data(commits_adopters: pd.DataFrame) -> dict:
    """
    Prepare data for distribution comparison.

    For non-adopters: separates into author_type == "Human" and "Bot"
    For adopters: separates into author_type == "AI", "Bot", and "Human"

    Also computes total files involved (sum of all file type counts).

    Args:
        commits_adopters: DataFrame with adopter commits
        commits_non_adopters: DataFrame with non-adopter commits

    Returns:
        Dictionary with filtered DataFrames for each category
    """
    data = {}

    # Adopters: separate AI, Bot, and Human commits
    if commits_adopters is not None and not commits_adopters.empty:
        adopters_ai = commits_adopters[
            commits_adopters['auth_type'] == 'AI'
        ].copy()
        adopters_bot = commits_adopters[
            commits_adopters['auth_type'] == 'Bot'
        ].copy()
        adopters_human = commits_adopters[
            commits_adopters['auth_type'] == 'Human'
        ].copy()

        # Calculate total files involved
        file_cols = [col for col in commits_adopters.columns if col.startswith('files_')]
        if file_cols:
            adopters_ai['total_files'] = adopters_ai[file_cols].sum(axis=1)
            adopters_bot['total_files'] = adopters_bot[file_cols].sum(axis=1)
            adopters_human['total_files'] = adopters_human[file_cols].sum(axis=1)

        data['adopters_ai'] = adopters_ai
        data['adopters_bot'] = adopters_bot
        data['adopters_human'] = adopters_human

    return data


def filter_outliers_iqr(data: np.ndarray, multiplier: float = 1.5) -> np.ndarray:
    """
    Filter outliers using IQR (Interquartile Range) method.

    Args:
        data: Array of values
        multiplier: IQR multiplier (default 1.5 for standard boxplot definition)

    Returns:
        Array with outliers removed
    """
    if len(data) == 0:
        return data

    q1 = np.percentile(data, 25)
    q3 = np.percentile(data, 75)
    iqr = q3 - q1
    lower_bound = q1 - multiplier * iqr
    upper_bound = q3 + multiplier * iqr

    return data[(data >= lower_bound) & (data <= upper_bound)]

def generate_latex_commands(box_data: list, labels: list, metrics: list) -> str:
    """
    Generate LaTeX commands for boxplot statistics.

    Args:
        box_data: List of arrays for each metric and category
        labels: Category labels
        metrics: Metric names

    Returns:
        String containing LaTeX command definitions
    """
    latex_commands = []
    latex_commands.append("% LaTeX commands for boxplot statistics")
    latex_commands.append("% Usage: \\commandname in your LaTeX document\n")

    # Mapping for clean names
    label_map = {
        'Human': 'Human',
        'Bot': 'Bot',
        'AI Assisted': 'AI'
    }

    metric_map = {
        'added_lines': 'AddedLines',
        'deleted_lines': 'DeletedLines',
        'total_files': 'TotalFiles'
    }

    for metric_idx, metric in enumerate(metrics):
        metric_clean = metric_map.get(metric, metric.replace('_', ''))

        for cat_idx, label in enumerate(labels):
            label_clean = label_map.get(label, label.replace(' ', ''))

            # Get the data for this category and metric
            data_idx = metric_idx * len(labels) + cat_idx
            if data_idx < len(box_data):
                values = box_data[data_idx]

                if len(values) > 0:
                    q1 = np.percentile(values, 25)
                    median = np.percentile(values, 50)
                    q3 = np.percentile(values, 75)
                    iqr = q3 - q1
                    # Upper whisker: Q3 + 1.5*IQR or max value, whichever is smaller
                    upper_whisker = min(np.max(values), q3 + 1.5 * iqr)

                    # Generate commands
                    prefix = f"{metric_clean}{label_clean}"
                    latex_commands.append(f"\\newcommand{{\\{prefix}Median}}{{{median:.0f}}}")
                    latex_commands.append(f"\\newcommand{{\\{prefix}QOne}}{{{q1:.0f}}}")
                    latex_commands.append(f"\\newcommand{{\\{prefix}QThree}}{{{q3:.0f}}}")
                    latex_commands.append(f"\\newcommand{{\\{prefix}UpperWhisker}}{{{upper_whisker:.0f}}}")
                    latex_commands.append("")

    return "\n".join(latex_commands)

# ============================================================================
# PHASE 1: Core Data Extraction Helper
# ============================================================================

def extract_metric_values(
    data: dict,
    key: str,
    metric: str,
    filter_positives: bool = True,
    filter_outliers: bool = True,
    threshold: float = 0
) -> tuple[np.ndarray, int]:
    """
    Extract and filter metric values from a category.

    Args:
        data: Dictionary with category DataFrames
        key: Category key (e.g., 'non_adopters_human')
        metric: Metric name (e.g., 'added_lines')
        filter_positives: If True, keep only positive values (for log scale)
        filter_outliers: If True, apply IQR outlier filtering
        threshold: Minimum threshold to apply (values > threshold)

    Returns:
        (filtered_values_array, original_count)
    """
    if key not in data:
        return np.array([]), 0

    df = data[key]
    if metric not in df.columns:
        return np.array([]), 0

    values = df[metric].dropna().values

    # Apply threshold filter
    if threshold > 0:
        values = values[values > threshold]

    # Filter positive values for log scale
    if filter_positives:
        values = values[values > 0]

    # Filter outliers
    if filter_outliers:
        values = filter_outliers_iqr(values)

    count = len(values)
    return values, count

def get_default_categories() -> list[tuple[str, str, str]]:
    """
    Get default category configuration.

    Returns:
        List of (key, label, color) tuples
    """
    return [
        ('adopters_human', 'Human', '#ff7f0e'),
        ('adopters_bot', 'Bot', '#d62728'),
        ('adopters_ai', 'AI Assisted', '#9467bd')
    ]

def iterate_categories_for_metric(
    data: dict,
    metric: str,
    categories: list[tuple] = None,
    filter_outliers: bool = True,
    threshold: float = 0
) -> list[tuple]:
    """
    Iterate categories and extract data for a metric.

    Args:
        data: Dictionary with category DataFrames
        metric: Metric name to extract
        categories: List of (key, label, color) tuples. If None, uses defaults.
        filter_outliers: If True, apply IQR outlier filtering
        threshold: Minimum threshold to apply

    Returns:
        List of (values, label, color, count) tuples
    """
    if categories is None:
        categories = get_default_categories()

    results = []
    for key, label, color in categories:
        values, count = extract_metric_values(
            data, key, metric,
            filter_positives=True,
            filter_outliers=filter_outliers,
            threshold=threshold
        )
        results.append((values, label, color, count))

    return results


def plot_distribution_comparison(
    data: dict,
    filter_outliers: bool = True,
    thresholds: dict[str, float]|None = None,
    categories: list[tuple]|None = None
) -> tuple[plt.Figure, str]:
    """
    Create boxplots comparing distributions with log scale.

    Args:
        data: Dictionary with filtered DataFrames from prepare_distribution_data()
        filter_outliers: If True, filter outliers using IQR method
        thresholds: Dict mapping metric names to minimum thresholds
                   e.g., {'added_lines': 10, 'total_files': 1}
        categories: List of (key, label, color) tuples. If None, uses defaults.

    Returns:
        Tuple of (matplotlib Figure object, LaTeX commands string)
    """
    if thresholds is None:
        thresholds = {}

    if categories is None:
        categories = get_default_categories()

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    metrics = ['added_lines', 'deleted_lines', 'total_files']

    all_box_data = []
    all_labels = []

    for ax, metric in zip(axes, metrics):
        threshold = thresholds.get(metric, 0)

        # Extract data for all categories
        category_data = iterate_categories_for_metric(
            data, metric, categories, filter_outliers, threshold
        )

        # Prepare for boxplot
        box_data = [values for values, _, _, _ in category_data]
        labels = [label for _, label, _, _ in category_data]
        colors = [color for _, _, color, _ in category_data]
        counts = [count for _, _, _, count in category_data]

        # Store for LaTeX generation
        all_box_data.extend(box_data)
        if not all_labels:
            all_labels = labels

        # Create boxplot
        bp = ax.boxplot(box_data, labels=labels, patch_artist=True, showfliers=False)

        # Apply colors
        for patch, color in zip(bp['boxes'], colors):
            patch.set_facecolor(color)

        # Style median lines
        for median in bp['medians']:
            median.set_color('black')
            median.set_linewidth(2)

        # Configure axis
        ax.set_yscale('log')
        labels_with_counts = [f"{label}\n(n={count})" for label, count in zip(labels, counts)]
        ax.set_xticklabels(labels_with_counts)
        ax.set_ylabel(metric.replace('_', ' ').title(), fontsize=12)

        # Title with threshold info
        title = metric.replace('_', ' ').title()
        if threshold > 0:
            title += f' (> {threshold})'
        ax.set_title(title, fontsize=13, fontweight='bold')
        ax.grid(axis='y', alpha=0.3, which='both')

    plt.tight_layout()

    # Generate LaTeX commands
    latex_commands = generate_latex_commands(all_box_data, all_labels, metrics)


    return fig, latex_commands

def plot_ai_commits_by_period(
    data: dict,
    periods: list[tuple[str, str]],
    date_column: str = 'date',
    period_labels: list[str] | None = None,
    filter_outliers: bool = True,
    thresholds: dict[str, float] | None = None,
    figsize: tuple = (18, 6)
) -> plt.Figure:
    """
    Create boxplots of AI commit metrics grouped by time periods.

    For each metric (added_lines, deleted_lines, total_files), one boxplot
    is drawn per time period, showing only AI commits within that period.

    Args:
        data: Dictionary with filtered DataFrames from prepare_distribution_data().
              Must contain 'adopters_ai' key.
        periods: List of (start, end) tuples in 'YYYY-MM' format, inclusive.
                 e.g., [("2023-01", "2023-06"), ("2023-07", "2023-12")]
        date_column: Column name in the AI DataFrame containing commit dates.
                     Will be parsed to datetime automatically.
        period_labels: Optional custom labels for each period.
                       If None, labels are auto-generated as "YYYY-MM → YYYY-MM".
        filter_outliers: If True, apply IQR outlier filtering per period.
        thresholds: Dict mapping metric names to minimum thresholds
                    e.g., {'added_lines': 1}. Only values > threshold are kept.
        figsize: Figure size (width, height).

    Returns:
        matplotlib Figure object
    """
    if thresholds is None:
        thresholds = {}

    if 'adopters_ai' not in data or data['adopters_ai'].empty:
        raise ValueError("No AI commit data found in 'adopters_ai' key.")

    ai_df = data['adopters_ai'].copy()

    # Parse date column
    if date_column not in ai_df.columns:
        raise ValueError(
            f"Date column '{date_column}' not found in AI DataFrame. "
            f"Available columns: {list(ai_df.columns)}"
        )
    ai_df[date_column] = pd.to_datetime(ai_df[date_column], utc=True, errors='coerce')
    ai_df = ai_df.dropna(subset=[date_column])

    # Build period labels
    if period_labels is None:
        period_labels = [f"{start} → {end}" for start, end in periods]
    elif len(period_labels) != len(periods):
        raise ValueError("period_labels must have the same length as periods.")

    metrics = ['added_lines', 'deleted_lines', 'total_files']
    metric_titles = {
        'added_lines': 'Added Lines',
        'deleted_lines': 'Deleted Lines',
        'total_files': 'Total Files',
    }

    # --- Collect data per period per metric ---
    period_data: dict[str, list[np.ndarray]] = {m: [] for m in metrics}
    period_counts: dict[str, list[int]] = {m: [] for m in metrics}

    for (start, end) in periods:
        # Convert YYYY-MM bounds to timestamps (start = first day, end = last day)
        start_ts = pd.Timestamp(start + "-01", tz='UTC')
        # End: last day of the end month
        end_ts = (pd.Timestamp(end + "-01", tz='UTC') + pd.offsets.MonthEnd(1))

        mask = (ai_df[date_column] >= start_ts) & (ai_df[date_column] <= end_ts)
        period_df = ai_df[mask]

        for metric in metrics:
            threshold = thresholds.get(metric, 0)

            if metric not in period_df.columns or period_df.empty:
                period_data[metric].append(np.array([]))
                period_counts[metric].append(0)
                continue

            values = period_df[metric].dropna().values

            # Apply threshold
            if threshold > 0:
                values = values[values > threshold]

            # Keep only positive values (needed for log scale)
            values = values[values > 0]

            # Apply IQR outlier filtering
            if filter_outliers:
                values = filter_outliers_iqr(values)

            period_data[metric].append(values)
            period_counts[metric].append(len(values))

    # --- Plot ---
    fig, axes = plt.subplots(1, len(metrics), figsize=figsize)
    if len(metrics) == 1:
        axes = [axes]

    # Use a colormap to give each period a distinct color
    period_colors = plt.cm.tab10(np.linspace(0, 0.9, len(periods)))

    for ax, metric in zip(axes, metrics):
        box_data = period_data[metric]
        counts = period_counts[metric]

        # Filter out completely empty periods for cleaner boxplot
        non_empty = [(bd, lbl, cnt, col)
                     for bd, lbl, cnt, col
                     in zip(box_data, period_labels, counts, period_colors)
                     if len(bd) > 0]

        if not non_empty:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                    transform=ax.transAxes, fontsize=12)
            ax.set_title(metric_titles[metric], fontsize=13, fontweight='bold')
            continue

        plot_arrays = [x[0] for x in non_empty]
        plot_labels = [x[1] for x in non_empty]
        plot_counts = [x[2] for x in non_empty]
        plot_colors = [x[3] for x in non_empty]

        bp = ax.boxplot(
            plot_arrays,
            labels=plot_labels,
            patch_artist=True,
            showfliers=False
        )

        # Color each box by period
        for patch, color in zip(bp['boxes'], plot_colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.75)

        for median in bp['medians']:
            median.set_color('black')
            median.set_linewidth(2)

        # Log scale + labels with counts
        ax.set_yscale('log')
        labels_with_counts = [
            f"{lbl}\n(n={cnt})" for lbl, cnt in zip(plot_labels, plot_counts)
        ]
        ax.set_xticklabels(labels_with_counts, rotation=30, ha='right', fontsize=9)
        ax.set_ylabel(metric_titles[metric], fontsize=12, fontweight='bold')
        ax.set_title(metric_titles[metric], fontsize=13, fontweight='bold')
        ax.grid(axis='y', alpha=0.3, which='both')

    fig.suptitle('AI Commit Size Distribution by Time Period',
                 fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    return fig

def get_default_bins_config() -> dict:
    """
    Get default bin configurations for each metric.

    Returns:
        Dict mapping metric names to {'bins': [...], 'labels': [...]}
    """
    return {
        'added_lines': {
            'bins': [1, 5, 20, 50, 100, 500, 1000, float('inf')],
            'labels': ['1-5', '6-20', '21-50', '51-100', '101-500', '501-1000', '1001+']
        },
        'deleted_lines': {
            'bins': [1, 5, 20, 50, 100, 500, 1000, float('inf')],
            'labels': ['1-5', '6-20', '21-50', '51-100', '101-500', '501-1000', '1001+']
        },
        'total_files': {
            'bins': [1, 2, 3, 5, 10, 20, 50, float('inf')],
            'labels': ['1', '2', '3-5', '6-10', '11-20', '21-50', '51+']
        }
    }

def plot_distribution_comparison_binned(
    data: dict,
    bins_config: dict = None,
    categories: list[tuple] = None
) -> tuple[plt.Figure, str]:
    """
    Create binned bar charts comparing distributions.

    Args:
        data: Dictionary with filtered DataFrames from prepare_distribution_data()
        bins_config: Dict mapping metric to {'bins': [...], 'labels': [...]}
                    If None, uses default configuration.
        categories: List of (key, label, color) tuples. If None, uses defaults.

    Returns:
        Tuple of (matplotlib Figure object, LaTeX commands string)
    """
    if bins_config is None:
        bins_config = get_default_bins_config()

    if categories is None:
        categories = get_default_categories()

    sns.set_style("white")
    fig, axes = plt.subplots(1, 3, figsize=(18, 7))
    metrics = ['added_lines', 'deleted_lines', 'total_files']

    # Storage for LaTeX commands
    latex_commands = []
    latex_commands.append("% LaTeX commands for binned distribution statistics")
    latex_commands.append("% Usage: \\commandname in your LaTeX document\n")

    # Mapping for clean names
    label_map = {
        'Human': 'Human',
        'Bot': 'Bot',
        'AI Assisted': 'AI'
    }

    metric_map = {
        'added_lines': 'AddedLines',
        'deleted_lines': 'DeletedLines',
        'total_files': 'TotalFiles'
    }

    # Digit to word mapping
    digit_map = {
        '0': 'Zero', '1': 'One', '2': 'Two', '3': 'Three', '4': 'Four',
        '5': 'Five', '6': 'Six', '7': 'Seven', '8': 'Eight', '9': 'Nine'
    }

    for ax, metric in zip(axes, metrics):
        config = bins_config[metric]
        bins = config['bins']
        bin_labels = config['labels']
        metric_clean = metric_map.get(metric, metric.replace('_', ''))

        # Collect binned data for each category
        bin_percentages = {label: [] for label in bin_labels}
        category_labels = []

        for key, category_label, color in categories:
            values, _ = extract_metric_values(
                data, key, metric,
                filter_positives=True,
                filter_outliers=False,
                threshold=0
            )

            if len(values) == 0:
                continue

            # Bin the values
            binned = pd.cut(values, bins=bins, labels=bin_labels, right=False)
            counts = binned.value_counts().reindex(bin_labels, fill_value=0)
            percentages = (counts / len(values) * 100).values

            for label, pct in zip(bin_labels, percentages):
                bin_percentages[label].append(pct)

            category_labels.append(category_label)

            # Generate LaTeX commands for this category
            label_clean = label_map.get(category_label, category_label.replace(' ', ''))
            for bin_label, pct in zip(bin_labels, percentages):
                # Clean bin label for command name (remove special characters)
                bin_clean = bin_label.replace('<', 'Lt').replace('>', 'Gt').replace('-', 'To').replace('+', 'Plus')
                # Replace digits with words
                for digit, word in digit_map.items():
                    bin_clean = bin_clean.replace(digit, word)
                command_name = f"{metric_clean}{label_clean}Bin{bin_clean}"
                latex_commands.append(f"\\newcommand{{\\{command_name}}}{{{pct:.2f}}}")

        latex_commands.append("")  # Blank line between metrics

        # Create grouped bar chart
        if not category_labels:
            continue

        x = np.arange(len(category_labels))
        bar_width = 0.85 / len(bin_labels)

        # Use viridis for bins
        bin_colors = plt.cm.viridis(np.linspace(0, 1, len(bin_labels)))

        for i, bin_label in enumerate(bin_labels):
            values = bin_percentages[bin_label]
            x_offset = x + (i - len(bin_labels) / 2 + 0.5) * bar_width
            ax.bar(
                x_offset, values, bar_width,
                label=bin_label,
                color=bin_colors[i],
                edgecolor='black',
                linewidth=0.5
            )

        ax.set_xticks(x)
        ax.set_xticklabels(category_labels, fontsize=11)
        ax.set_ylabel('Percentage of Commits (%)', fontsize=13, fontweight='bold')
        ax.set_title(metric.replace('_', ' ').title(), fontsize=14, fontweight='bold')
        ax.legend(
            title=f'{metric.replace("_", " ").title()} Bins',
            fontsize=9,
            title_fontsize=10,
            ncol=2
        )
        ax.grid(axis='y', alpha=0.3, linestyle='--')
        ax.set_ylim(0, 100)

    plt.tight_layout()

    latex_str = "\n".join(latex_commands)
    return fig, latex_str

def plot_distribution_comparison_binned_grouped(
    data: dict,
    bins_config: dict|None = None,
    categories: list[tuple]|None = None
) -> plt.Figure:
    """
    Create binned bar charts with bins grouped together (Human/Bot/AI for each bin).

    Args:
        data: Dictionary with filtered DataFrames from prepare_distribution_data()
        bins_config: Dict mapping metric to {'bins': [...], 'labels': [...]}
                    If None, uses default configuration.
        categories: List of (key, label, color) tuples. If None, uses defaults.

    Returns:
        matplotlib Figure object
    """
    if bins_config is None:
        bins_config = get_default_bins_config()

    if categories is None:
        categories = get_default_categories()

    sns.set_style("white")
    fig, axes = plt.subplots(1, 3, figsize=(18, 7))
    metrics = ['added_lines', 'deleted_lines', 'total_files']

    for ax, metric in zip(axes, metrics):
        config = bins_config[metric]
        bins = config['bins']
        bin_labels = config['labels']

        # Collect binned data for each category
        category_data = {}
        for key, category_label, color in categories:
            values, _ = extract_metric_values(
                data, key, metric,
                filter_positives=True,
                filter_outliers=False,
                threshold=0
            )

            if len(values) == 0:
                category_data[category_label] = {'percentages': [0] * len(bin_labels), 'color': color}
                continue

            # Bin the values
            binned = pd.cut(values, bins=bins, labels=bin_labels, right=False)
            counts = binned.value_counts().reindex(bin_labels, fill_value=0)
            percentages = (counts / len(values) * 100).values

            category_data[category_label] = {'percentages': percentages, 'color': color}

        if not category_data:
            continue

        # Create grouped bar chart (bins on x-axis, categories grouped within each bin)
        num_bins = len(bin_labels)
        num_categories = len(category_data)
        x = np.arange(num_bins)
        bar_width = 0.8 / num_categories

        for cat_idx, (category_label, cat_info) in enumerate(category_data.items()):
            x_offset = x + (cat_idx - num_categories / 2 + 0.5) * bar_width
            ax.bar(
                x_offset,
                cat_info['percentages'],
                bar_width,
                label=category_label,
                color=cat_info['color'],
                edgecolor='black',
                linewidth=0.5,
                alpha=0.8
            )

        ax.set_xticks(x)
        ax.set_xticklabels(bin_labels, fontsize=10)
        ax.set_xlabel(f'{metric.replace("_", " ").title()} Bins', fontsize=12, fontweight='bold')
        ax.set_ylabel('Percentage of Commits (%)', fontsize=13, fontweight='bold')
        ax.set_title(metric.replace('_', ' ').title(), fontsize=14, fontweight='bold')
        ax.legend(fontsize=10, title='Category', title_fontsize=11)
        ax.grid(axis='y', alpha=0.3, linestyle='--')
        ax.set_ylim(0, max(100, ax.get_ylim()[1]))

    plt.tight_layout()
    return fig

def compute_bin_percentage_differences(
    data: dict,
    bins_config: dict = None,
    categories: list[tuple] = None
) -> dict:
    """
    Compute relative percentage differences between AI and Human bins.

    Relative difference = ((AI_pct - Human_pct) / Human_pct) * 100
    Positive values mean AI has higher percentage in that bin.

    Args:
        data: Dictionary with filtered DataFrames from prepare_distribution_data()
        bins_config: Dict mapping metric to {'bins': [...], 'labels': [...]}
        categories: List of (key, label, color) tuples

    Returns:
        Dict with structure: {
            'added_lines': {
                'bin_labels': ['<1', '1-5', ...],
                'human_pct': [10.5, 20.3, ...],
                'ai_pct': [5.2, 25.8, ...],
                'relative_diff': [-50.5, 26.9, ...]  # relative % change
            },
            'deleted_lines': {...},
            'total_files': {...}
        }
    """
    if bins_config is None:
        bins_config = get_default_bins_config()

    metrics = ['added_lines', 'deleted_lines', 'total_files']
    results = {}

    for metric in metrics:
        config = bins_config[metric]
        bins = config['bins']
        bin_labels = config['labels']

        # Get Human data
        human_values, _ = extract_metric_values(
            data, 'adopters_human', metric,
            filter_positives=True,
            filter_outliers=False,
            threshold=0
        )

        # Get AI data
        ai_values, _ = extract_metric_values(
            data, 'adopters_ai', metric,
            filter_positives=True,
            filter_outliers=False,
            threshold=0
        )

        if len(human_values) == 0 or len(ai_values) == 0:
            continue

        # Bin the values
        human_binned = pd.cut(human_values, bins=bins, labels=bin_labels, right=False)
        human_counts = human_binned.value_counts().reindex(bin_labels, fill_value=0)
        human_pct = (human_counts / len(human_values) * 100).values

        ai_binned = pd.cut(ai_values, bins=bins, labels=bin_labels, right=False)
        ai_counts = ai_binned.value_counts().reindex(bin_labels, fill_value=0)
        ai_pct = (ai_counts / len(ai_values) * 100).values

        # Compute relative difference: ((AI - Human) / Human) * 100
        # Handle division by zero
        relative_diff = np.zeros_like(human_pct, dtype=float)
        for i in range(len(human_pct)):
            if human_pct[i] > 0:
                relative_diff[i] = ((ai_pct[i] - human_pct[i]) / human_pct[i]) * 100
            elif ai_pct[i] > 0:
                # If human is 0 but AI isn't, it's effectively infinite increase
                relative_diff[i] = float('inf')
            else:
                # Both are 0
                relative_diff[i] = 0.0

        results[metric] = {
            'bin_labels': bin_labels,
            'human_pct': human_pct,
            'ai_pct': ai_pct,
            'relative_diff': relative_diff,
            'absolute_diff': ai_pct - human_pct  # Also include absolute difference
        }

    return results

def plot_bin_percentage_differences(
    diff_results: dict,
    figsize: tuple = (18, 6)
) -> plt.Figure:
    """
    Plot relative percentage differences between AI and Human bins.

    Creates a bar chart showing relative percentage change for each bin,
    with positive values (AI > Human) and negative values (AI < Human).

    Args:
        diff_results: Output from compute_bin_percentage_differences()
        figsize: Figure size (width, height)

    Returns:
        matplotlib Figure object
    """
    sns.set_style("white")
    fig, axes = plt.subplots(1, 3, figsize=figsize)
    metrics = ['added_lines', 'deleted_lines', 'total_files']

    metric_titles = {
        'added_lines': 'Added Lines',
        'deleted_lines': 'Deleted Lines',
        'total_files': 'Total Files'
    }

    for ax, metric in zip(axes, metrics):
        if metric not in diff_results:
            continue

        data = diff_results[metric]
        bin_labels = data['bin_labels']
        relative_diff = data['relative_diff']

        # Filter out infinite values for plotting
        plot_diff = np.copy(relative_diff)
        has_inf = np.isinf(plot_diff)
        if np.any(has_inf):
            # Replace inf with a large value for visualization
            max_finite = np.max(np.abs(plot_diff[~has_inf])) if np.any(~has_inf) else 100
            plot_diff[has_inf] = np.sign(plot_diff[has_inf]) * max_finite * 1.5

        # Color bars: green for positive (AI higher), red for negative (AI lower)
        colors = ['#2ca02c' if x >= 0 else '#d62728' for x in plot_diff]

        # Create bar chart
        x_pos = np.arange(len(bin_labels))
        bars = ax.bar(x_pos, plot_diff, color=colors, edgecolor='black', linewidth=0.8, alpha=0.8)

        # Add a horizontal line at y=0
        ax.axhline(y=0, color='black', linestyle='-', linewidth=1.5)

        # Add value labels on bars
        for i, (bar, val) in enumerate(zip(bars, relative_diff)):
            height = bar.get_height()
            if np.isinf(val):
                label = '∞' if val > 0 else '-∞'
            else:
                label = f'{val:.1f}%'

            # Position label above/below bar depending on sign
            va = 'bottom' if height >= 0 else 'top'
            y_offset = 2 if height >= 0 else -2
            ax.text(bar.get_x() + bar.get_width()/2., height + y_offset,
                    label, ha='center', va=va, fontsize=9, fontweight='bold')

        # Styling
        ax.set_xticks(x_pos)
        ax.set_xticklabels(bin_labels, fontsize=10, rotation=45, ha='right')
        ax.set_ylabel('Relative Difference (%)', fontsize=12, fontweight='bold')
        ax.set_title(f'{metric_titles[metric]}\n(AI vs Human)',
                    fontsize=13, fontweight='bold')
        ax.grid(axis='y', alpha=0.3, linestyle='--')

        # Extend ylim to fit labels (add 15% margin)
        current_ylim = ax.get_ylim()
        y_range = current_ylim[1] - current_ylim[0]
        ax.set_ylim(current_ylim[0] - 0.15 * y_range, current_ylim[1] + 0.15 * y_range)

        # Add legend in bottom right
        from matplotlib.patches import Patch
        legend_elements = [
            Patch(facecolor='#2ca02c', edgecolor='black', label='AI > Human'),
            Patch(facecolor='#d62728', edgecolor='black', label='AI < Human')
        ]
        ax.legend(handles=legend_elements, loc='lower right', fontsize=9)

    plt.tight_layout()
    return fig

def plot_commit_type_distribution(
    commits_df: pd.DataFrame,
    include_none: bool = True,
    figsize: tuple = (16, 5)
) -> plt.Figure:
    """
    Create histograms showing commit type distribution by author type.

    Creates 3 side-by-side bar charts (Human, Bot, AI Assisted) showing
    the percentage of commits by conventional commit type.

    Args:
        commits_df: DataFrame with 'auth_type' and 'commit_type' columns
        include_none: If True, include 'none' category for unmatched commits
        figsize: Figure size (width, height)

    Returns:
        matplotlib Figure object
    """
    sns.set_style("white")
    fig, axes = plt.subplots(1, 3, figsize=figsize)

    categories = ['Human', 'Bot', 'AI Assisted']
    type_categories = get_commit_type_categories()
    commit_types = type_categories['all'] if include_none else type_categories['types']

    # Map 'none' category to empty string in the DataFrame
    commits_df_copy = commits_df.copy()
    commits_df_copy['commit_type'] = commits_df_copy['commit_type'].fillna('').replace('', 'none')
    commits_df_copy.loc[commits_df_copy['commit_type'].isnull(), 'commit_type'] = 'none'

    # Color scheme for commit types
    colors = plt.cm.Set3(np.linspace(0, 1, len(commit_types)))
    color_map = {ct: colors[i] for i, ct in enumerate(commit_types)}

    for ax, category in zip(axes, categories):
        # Filter by auth_type
        auth_type_map = {'Human': 'Human', 'Bot': 'Bot', 'AI Assisted': 'AI'}
        filtered_df = commits_df_copy[commits_df_copy['auth_type'] == auth_type_map[category]]

        if filtered_df.empty:
            ax.text(0.5, 0.5, f'No data for {category}',
                   ha='center', va='center', transform=ax.transAxes)
            continue

        # Count commits by type
        type_counts = filtered_df['commit_type'].value_counts().reindex(
            commit_types, fill_value=0
        )

        # Convert to percentages
        type_pct = (type_counts / type_counts.sum() * 100).values

        # Create bar chart
        bar_colors = [color_map[ct] for ct in commit_types]
        bars = ax.bar(range(len(commit_types)), type_pct, color=bar_colors,
                     edgecolor='black', linewidth=0.8, alpha=0.85)

        # Add percentage labels on bars
        for bar, pct in zip(bars, type_pct):
            height = bar.get_height()
            if height > 0:
                ax.text(bar.get_x() + bar.get_width()/2., height + 1,
                       f'{pct:.1f}%', ha='center', va='bottom', fontsize=9, fontweight='bold')

        # Styling
        ax.set_xticks(range(len(commit_types)))
        ax.set_xticklabels(commit_types, rotation=45, ha='right', fontsize=10)
        ax.set_ylabel('Percentage of Commits (%)', fontsize=11, fontweight='bold')
        ax.set_title(f'{category}\n(n={len(filtered_df)})', fontsize=12, fontweight='bold')
        ax.set_ylim(0, max(type_pct) * 1.15)
        ax.grid(axis='y', alpha=0.3, linestyle='--')

    title = 'Commit Type Distribution by Author Type'
    if not include_none:
        title += ' (Excluding Unmatched)'
    fig.suptitle(title, fontsize=14, fontweight='bold', y=1.02)

    plt.tight_layout()
    return fig

# ============================================================================
# Data Processing Functions (Unchanged)
# ============================================================================

def compute_commit_statistics(files_df: pd.DataFrame, main_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute commit-level statistics by aggregating file-level data.

    For each commit (identified by commit_sha):
    - Sum added_lines and deleted_lines across all files
    - Count files by type (added, modified, renamed, deleted)
    - Join with main_df to get auth_type information

    Args:
        files_df: File-level statistics DataFrame
        main_df: Main (aggregate) statistics DataFrame

    Returns:
        DataFrame with commit-level statistics including auth_type
    """
    if files_df.empty or main_df.empty:
        return pd.DataFrame()

    # Group by commit_sha and aggregate file-level statistics
    commit_stats = files_df.groupby('commit_sha').agg(
        added_lines=('added_lines', 'sum'),
        deleted_lines=('deleted_lines', 'sum'),
    ).reset_index()

    # Count files by type for each commit
    file_counts = files_df.groupby(['commit_sha', 'type']).size().unstack(fill_value=0)
    file_counts = file_counts.reset_index()

    # Rename columns to indicate they are file counts
    for col in file_counts.columns:
        if col != 'commit_sha':
            file_counts.rename(columns={col: f'files_{col}'}, inplace=True)

    # Merge the two aggregated DataFrames
    commit_stats = commit_stats.merge(file_counts, on='commit_sha', how='left')

    if 'commit_sha' not in main_df.columns:
        main_df['commit_sha'] = main_df['sha']

    # Join with main_df to get auth_type and message
    merge_cols = ['commit_sha', 'auth_type']
    if 'message' in main_df.columns:
        merge_cols.append('message')

    if 'date' in main_df.columns:
        merge_cols.append('date')

    commit_stats = commit_stats.merge(
        main_df[merge_cols],
        on='commit_sha',
        how='left'
    )

    # Extract commit type from message
    if 'message' in commit_stats.columns:
        commit_stats['commit_type'] = commit_stats['message'].apply(extract_commit_type)
    else:
        commit_stats['commit_type'] = ''

    return commit_stats



def generate_visualizations(args, commits_adopters):
    print("="*80)
    print("Generating distribution comparisons...")
    print("="*80 + "\n")

    dist_data = prepare_distribution_data(commits_adopters)

    figures = []
    captions = []
    fig_labels = []
    latex_commands = ""

    if dist_data:
        # Standard distribution (with LaTeX commands)
        fig_boxplot, latex_cmds = plot_distribution_comparison(dist_data)
        figures.append(fig_boxplot)
        captions.append("Boxplot comparison of added lines, deleted lines, and total files across human, bot, and AI-assisted commits.")
        fig_labels.append("fig:commit-sizes-boxplot-comparison")
        latex_commands = latex_cmds

        # Binned plot (original style)
        fig_binned, latex_cmds_binned = plot_distribution_comparison_binned(dist_data)
        figures.append(fig_binned)
        captions.append("Percentage distribution of commits across size bins for human, bot, and AI-assisted authorship categories.")
        fig_labels.append("fig:commit-sizes-binned-distribution")
        latex_commands += "\n\n" + latex_cmds_binned

        # Binned plot (grouped by bins)
        fig_binned_grouped = plot_distribution_comparison_binned_grouped(dist_data)
        figures.append(fig_binned_grouped)
        captions.append("Per-bin analysis comparing how each size category is populated across authorship types.")
        fig_labels.append("fig:commit-sizes-grouped-bins")

        diff_results = compute_bin_percentage_differences(dist_data)
        fig_diff = plot_bin_percentage_differences(diff_results)
        figures.append(fig_diff)
        captions.append("Absolute and relative percentage differences in bin distributions between AI-assisted and human commits, highlighting divergent patterns.")
        fig_labels.append("fig:commit-sizes-bin-differences")

        periods = [
            ("2025-01", "2025-05"),
            ("2025-06", "2025-10"),
            ("2025-11", "2026-03"),
        ]

        fig = plot_ai_commits_by_period(
            data=dist_data,
            periods=periods,
            date_column='date',          # adjust to your actual column name
            filter_outliers=True,
            thresholds={'added_lines': 1},
        )
        figures.append(fig)
        captions.append("Temporal evolution of AI-assisted commit characteristics across three six-month periods, showing metric distributions over time.")
        fig_labels.append("fig:ai-commits-temporal-trends")

    # Commit type distributions
    if commits_adopters is not None and not commits_adopters.empty:
        if 'commit_type' in commits_adopters.columns:
            print("="*80)
            print("Generating commit type distributions...")
            print("="*80 + "\n")

            # With 'none' category
            fig_commit_type_with_none = plot_commit_type_distribution(
                commits_adopters,
                include_none=True
            )
            figures.append(fig_commit_type_with_none)
            captions.append("Distribution of commit types identified from commit messages, including unclassified commits.")
            fig_labels.append("fig:commit-types-distribution-all")

            # Without 'none' category
            fig_commit_type_without_none = plot_commit_type_distribution(
                commits_adopters,
                include_none=False
            )
            figures.append(fig_commit_type_without_none)
            captions.append("Distribution of classified commit types, excluding unclassified commits for focused analysis.")
            fig_labels.append("fig:commit-types-distribution-classified")

    if figures:
        generate_visualization_report(figures, Path(args.output_pdf), separate_files=True, captions=captions, labels=fig_labels)
        print(f"Saved visualizations to {args.output_pdf}\n")

    # Print LaTeX commands
    if latex_commands:
        print("="*80)
        print("LaTeX Commands for Boxplot Statistics")
        print("="*80)
        print(latex_commands)
        print("="*80 + "\n")

    # In your main code or generate_visualizations:

    # Print the results
    for metric, data in diff_results.items():
        print(f"\n{metric.upper()}:")
        print(f"{'Bin':<15} {'Human %':>10} {'AI %':>10} {'Abs Diff':>10} {'Rel Diff %':>12}")
        print("-" * 60)
        for i, bin_label in enumerate(data['bin_labels']):
            print(f"{bin_label:<15} {data['human_pct'][i]:>10.2f} {data['ai_pct'][i]:>10.2f} "
                  f"{data['absolute_diff'][i]:>10.2f} {data['relative_diff'][i]:>12.2f}")

    # Run statistical tests
    run_statistical_tests(dist_data)


def run_statistical_tests(dist_data):

    print("##### Running statistical tests #####")
    print(dist_data.keys())
    adopters_human = dist_data['adopters_human']
    adopters_ai = dist_data['adopters_ai']

    metrics = ['added_lines', 'deleted_lines', 'total_files']

    for metric in metrics:

        print(f"=== Metric: {metric} ===")

        human = adopters_human[metric]
        ai_assisted = adopters_ai[metric]

        # Normality tests
        stat, p = shapiro(human)
        print(f"Shapiro-Wilk human: stat={stat:.3f}, p={p}")
        stat, p = shapiro(ai_assisted)
        print(f"Shapiro-Wilk AI-assisted: stat={stat:.3f}, p={p}")
        stat, p = normaltest(human)
        print(f"Normaltest human: stat={stat:.3f}, p={p}")
        stat, p = normaltest(ai_assisted)
        print(f"Normaltest AI-assisted: stat={stat:.3f}, p={p}")

        # Mann-Whitney U test
        stat, p = mannwhitneyu(ai_assisted, human)
        print(f"Mannwhitneyu: t={stat:.3f}, p={p}")

        # Cliffs delta effect size
        delta, interpretation = cliffs_delta(ai_assisted, human)
        print(f"Cliffs delta: delta={delta:.3f} {interpretation}")
        # ef = pg.compute_effsize(ai_assisted, human, eftype='cohen')
        # print(f"Cohen's d: {ef}\n")


def main():
    parser = default_args("Analyzes commit sizes by category (Human vs Bot vs AI))")
    parser.add_argument('--cache-dir', type=str, default='./commit_stats_cache',
                        help='Directory to cache computed commit statistics')
    parser.add_argument('--no-cache', action='store_true',
                        help='Skip loading from cache and recompute statistics')
    parser.add_argument('--output-pdf', type=str, default='commit_sizes_report.pdf',
                        help='Output PDF file path for visualizations')
    args = parser.parse_args()

    base_dir = args.input
    cache_dir = Path(args.cache_dir)
    cache_adopters = cache_dir / 'commits_adopters.csv'
    cache_non_adopters = cache_dir / 'commits_non_adopters.csv'

    print(f"Loading commit statistics from {base_dir}\n")

    # Try to load from cache if available
    commits_adopters = None
    commits_non_adopters = None

    if not args.no_cache and cache_adopters.exists() and cache_non_adopters.exists():
        print("Loading from cache...\n")
        try:
            commits_adopters = pd.read_csv(cache_adopters)
            print(f"Loaded adopters commits: {commits_adopters.shape[0]} rows")
        except Exception as e:
            print(f"Warning: Could not load from cache: {e}\n")
            print("Recomputing statistics...\n")

    # If data was loaded from cache, generate visualizations and return
    if commits_adopters is not None:
        generate_visualizations(args, commits_adopters)

        return

    # Get directories to include for adopters (everything except the excluded ones)
    exclude_dirs_1 = get_include_dirs(base_dir, ["tool_commit_adopters","ignored_tools_commit_adopters","commit_only_adopters"])

    # Load file-level stats for adopters
    print("Loading commit_stats_files.csv for adopters...")
    file_stats_adopters = load_data_basic(
        base_dir,
        filename="commit_stats_files.csv",
        exclude_dirs=exclude_dirs_1
    )
    print(f"Loaded {len(file_stats_adopters)} file stat DataFrames from adopters\n")

    # Load aggregate stats for adopters
    print("Loading commit_stats_main.csv for adopters...")
    main_stats_adopters = load_data_basic(
        base_dir,
        filename="commit_stats_main.csv",
        exclude_dirs=exclude_dirs_1
    )
    print(f"Loaded {len(main_stats_adopters)} main stat DataFrames from adopters\n")

    # Combine and display basic stats

    if file_stats_adopters:
        file_adopters_combined = pd.concat(file_stats_adopters, ignore_index=True)
        print(f"Adopters (file stats): {file_adopters_combined.shape[0]} rows")
        print(f"  Columns: {list(file_adopters_combined.columns)}\n")

    if main_stats_adopters:
        main_adopters_combined = pd.concat(main_stats_adopters, ignore_index=True)
        print(f"Adopters (main stats): {main_adopters_combined.shape[0]} rows")
        print(f"  Columns: {list(main_adopters_combined.columns)}\n")

    # Compute commit statistics
    print("="*80)
    print("Computing commit-level statistics...")
    print("="*80 + "\n")

    if file_stats_adopters and main_stats_adopters:
        print("Computing commit statistics for adopters...")
        commits_adopters = compute_commit_statistics(
            file_adopters_combined,
            main_adopters_combined
        )
        print(f"Adopters (commit stats): {commits_adopters.shape[0]} commits")
        print(f"  Columns: {list(commits_adopters.columns)}\n")

    # Save to cache
    print("="*80)
    print("Saving results to cache...")
    print("="*80 + "\n")

    cache_dir.mkdir(parents=True, exist_ok=True)

    if commits_adopters is not None and not commits_adopters.empty:
        commits_adopters.to_csv(cache_adopters, index=False)
        print(f"Saved adopters commits to {cache_adopters}\n")

    # Generate distribution comparisons
    generate_visualizations(args, commits_adopters)

if __name__ == "__main__":
    main()

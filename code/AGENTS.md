### Project Overview

This project is a comprehensive tool designed to analyze Git repositories and measure the adoption of AI-powered development tools. It operates by fetching repository data, including file structures, pull requests, and commit histories from GitHub. The tool then classifies commits as either human, AI, or bot-generated, and identifies the specific AI tools being used. By aggregating this data, it calculates a variety of metrics and ratios, which are then used to generate detailed reports and visualizations that illustrate the extent and nature of AI tool adoption within software projects.

### Core Functionality

The project's workflow is orchestrated through a series of scripts that perform the following key functions:

1.  **Data Ingestion**: Clones repositories and fetches metadata, pull requests, and commit histories.
2.  **Tool Detection**: Scans repository files to identify the presence of known development tools and frameworks.
3.  **Commit Classification**: Applies heuristics and pattern matching to classify commits, distinguishing between human, AI, and bot authors.
4.  **Metric Computation**: Calculates detailed metrics, such as the ratio of AI-to-human contributions (e.g., commit counts, lines of code), and analyzes these metrics over various time periods.
5.  **Reporting and Visualization**: Generates reports and visualizations, including time-series analyses of AI adoption and comparisons across different projects.

### File Breakdown

| File | Description |
| :--- | :--- |
| `run.py` | The main entry point for running the analysis pipeline on a list of repositories in parallel. |
| `repo_analyzer.py` | A central class that orchestrates the analysis of a single repository. |
| `find_tools.py` | Analyzes repository file lists to detect which development tools are used. |
| `get_pull_requests.py` | Fetches pull request data from GitHub using the GraphQL API. |
| `compute_metrics.py` | Computes various metrics and ratios from the classified commits. |
| `heuristics.py` | Defines the heuristics and patterns used to classify commits and pull requests. |
| `match_authors.py` | Provides functionality to match and deduplicate author names and emails. |
| `pr_merge_types.py` | Classifies how a pull request was merged (e.g., merge, squash, rebase). |
| `tag_commits.py` | Tags commits within a pull request based on AI tool usage patterns. |

### `utils/` Directory

| File | Description |
| :--- | :--- |
| `cloning.py` | Provides functions for cloning Git repositories. |
| `commit_hash.py` | A simple utility to validate commit hashes. |
| `data.py` | Defines the data structures (dataclasses) used throughout the project. |
| `file_types.py` | Classifies files based on their extension and content. |
| `filters.py` | Provides filters to exclude certain files and commits from the analysis. |
| `github.py` | A client for interacting with the GitHub API. |
| `import_export.py` | Provides functions for reading and writing dataclasses to and from CSV files. |
| `logging.py` | A thread-local logging module for repository analysis. |
| `metrics.py` | Defines dataclasses for storing metrics. |
| `parallel.py` | A utility for running tasks in parallel. |
| `repository.py` | A wrapper for a local Git repository, providing methods to get commit information. |

### `reports/` Directory

The reports directory contains scripts for generating various analyses and visualizations from computed metrics.

| File | Description |
| :--- | :--- |
| `common.py` | Shared utilities for report generation: data loading, filtering, dataframe operations, and PDF output handling. |
| `consolidate_metrics.py` | Consolidates metrics from multiple sources and time periods into unified datasets. |
| `tool_metrics_report.py` | Generates detailed metrics and visualizations about tool adoption and usage patterns. |
| `ratio_pl_report.py` | Analyzes and visualizes AI adoption ratios across programming languages. |
| `commit_ratio_report.py` | Analyzes commit-based ratios and generates related visualizations. |
| `ratio_category_report.py` | Analyzes adoption ratios categorized by repository characteristics (topics, languages, etc.). |
| `timelines.py` | Generates timeline analyses showing how AI adoption evolves over time. |
| `basic_stats.py` | Computes and visualizes basic statistical summaries of the dataset. |
| `commit_sizes.py` | Analyzes and visualizes commit size distributions and patterns. |
| `top_organizations.py` | Identifies and analyzes adoption patterns in top organizations. |
| `repo_properties_report.py` | Analyzes adoption patterns based on repository properties. |
| `table_ratios.py` | Generates tabular summaries of adoption ratios. |
| `full_report.py` | Generates a comprehensive report combining multiple analyses. |
| `latex_utils.py` | Utility functions for LaTeX-compatible output. |

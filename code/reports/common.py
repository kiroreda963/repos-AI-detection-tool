# reports/common.py
import argparse
import glob
import os
import platform
import re
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from pandas.core.reshape.merge import MergeHow, Suffixes
from tqdm import tqdm
from utils.parallel import run_parallel

# Module-level variable to store input path
_input_path: Path | None = None


def set_input_path(path: str | Path) -> None:
    """Store the input path at module initialization."""
    global _input_path
    _input_path = Path(path)


def write_file(partial_path: str | Path, content: str) -> None:
    """
    Write content to a file in the report directory.

    Args:
        partial_path: Relative path including filename (e.g., 'output/metrics.csv')
        content: File content to write

    Raises:
        RuntimeError: If input path has not been set
    """
    if _input_path is None:
        raise RuntimeError("Input path not set. Call set_input_path() first.")

    full_path = _input_path / "report" / partial_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content)


def get_report_path(partial_path: str | Path) -> Path:
    """
    Get the full path for a file in the report directory.

    Args:
        partial_path: Relative path including filename (e.g., 'rq3/metrics.csv')

    Returns:
        Full Path object

    Raises:
        RuntimeError: If input path has not been set
    """
    if _input_path is None:
        raise RuntimeError("Input path not set. Call set_input_path() first.")

    return _input_path / "report" / partial_path


def write_dataframe(partial_path: str | Path, df: pd.DataFrame) -> None:
    """
    Write DataFrame to CSV in the report directory.

    Args:
        partial_path: Relative path including filename (e.g., 'output/metrics.csv')
        df: DataFrame to write

    Raises:
        RuntimeError: If input path has not been set
    """
    full_path = get_report_path(partial_path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(full_path, index=False)


def read_dataframe(partial_path: str | Path) -> pd.DataFrame:
    """
    Read DataFrame from CSV in the report directory.

    Args:
        partial_path: Relative path including filename (e.g., 'rq3/metrics.csv')

    Returns:
        DataFrame loaded from the CSV

    Raises:
        RuntimeError: If input path has not been set
    """
    full_path = get_report_path(partial_path)
    return pd.read_csv(full_path)


def find_and_select_report(experiment_dir: Path, use_default=True) -> Path | None:
    """Finds analysis reports and lets the user select one."""
    reports = list(experiment_dir.glob("analysis_report_*.csv")) + list(
        experiment_dir.glob("tmp_analysis_*.csv")
    )

    if not reports:
        print("No analysis reports found to retry from.", file=sys.stderr)
        return None

    def sort_key(path: Path) -> int:
        try:
            # Extracts the number from filenames like 'analysis_report_12345.csv'
            return int(path.stem.split("_")[-1])
        except (ValueError, IndexError):
            return 0

    reports.sort(key=sort_key, reverse=True)

    if len(reports) == 1:
        print(f"Found one report: {reports[0].name}")
        return reports[0]

    if use_default:
        print(f"Using default report: {reports[0].name}")
        return reports[0]

    print("Multiple reports found. Please choose one to use as a baseline:")
    for i, report in enumerate(reports):
        default_str = " (default)" if i == 0 else ""
        print(f"  [{i + 1}] {report.name}{default_str}")

    while True:
        try:
            choice = input(
                f"Enter number (1-{len(reports)}) or press Enter for default: "
            ).strip()
            if not choice:
                return reports[0]
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(reports):
                return reports[choice_idx]
            else:
                print("Invalid number.", file=sys.stderr)
        except ValueError:
            print("Please enter a valid number.", file=sys.stderr)
        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.", file=sys.stderr)
            return None



def process_subdirectory(task: dict[str]) -> list[pd.DataFrame]:
    """
    Worker function to process all CSV files in a single subdirectory.

    Args:
        task: Dict with keys 'subdirectory', 'filename', 'exclude_dirs'

    Returns:
        List of processed DataFrames from this subdirectory
    """
    subdirectory = task["subdirectory"]
    filename = task["filename"]
    exclude_dirs = task.get("exclude_dirs", [])
    in_subfolder = task.get("in_subfolder", False)
    keep_only_monthly = task.get("keep_only_monthly", False)
    df_list = []

    for dirpath, dirnames, filenames in os.walk(subdirectory):
        # Filter out excluded directories in-place to prevent walking into them
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]

        if filename in filenames:
            file_path = os.path.join(dirpath, filename)
            subfolder_name = os.path.basename(dirpath)
            parent_folder_name = os.path.basename(os.path.dirname(dirpath))

            if parent_folder_name == "working":
                continue

            df = pd.read_csv(file_path)

            # if "name" in df.columns and "monthly" in df["name"].values:
            if keep_only_monthly:
                df = df[df["name"] == "monthly"].copy()
                df.drop(
                    columns=["name", "type", "period_end"],
                    inplace=True,
                    errors="ignore",
                )

            if in_subfolder:
                # Shift: project = parent, class = grandparent
                df["project"] = parent_folder_name
                df["class"] = os.path.basename(
                    os.path.dirname(os.path.dirname(dirpath))
                )
            else:
                df["project"] = subfolder_name
                df["class"] = parent_folder_name
            df_list.append(df)

    return df_list


def load_data_basic(
    folder: str,
    filename: str = "ratios.csv",
    max_workers: int = 8,
    exclude_dirs: list[str] | None = None,
    in_subfolder: bool = False,
) -> list[pd.DataFrame]:
    """
    Loads and merges data from CSV files in a directory structure using parallel I/O.
    Parallelizes at the subdirectory level for faster task collection.

    Args:
        folder: Root directory to search
        filename: Target CSV filename to look for
        max_workers: Number of parallel workers for directory processing
        exclude_dirs: List of directory names to skip (default: ["non_adopters", "analysis_error"])
        in_subfolder: If True, set project to the parent folder of the file's directory

    Returns:
        Merged and processed DataFrame with all data
    """
    if exclude_dirs is None:
        exclude_dirs = ["non_adopters", "analysis_error"]

    # Phase 1: Get top-level subdirectories (fast)
    subdirectories = []
    try:
        for entry in os.scandir(folder):
            if entry.is_dir(follow_symlinks=False) and entry.name not in exclude_dirs:
                subdirectories.append(entry.path)
    except PermissionError:
        pass

    if not subdirectories:
        # Fallback: treat folder as single directory
        subdirectories = [folder]

    # Phase 2: Process each subdirectory in parallel
    tasks = [
        {
            "subdirectory": subdir,
            "filename": filename,
            "exclude_dirs": exclude_dirs,
            "in_subfolder": in_subfolder,
        }
        for subdir in subdirectories
    ]

    results = run_parallel(
        worker_func=process_subdirectory,
        tasks=tasks,
        max_workers=max_workers,
        executor_type="thread",
        description=f"Processing subdirectories for {filename}",
        timeout=300,  # 5 minutes per subdirectory
    )

    # Phase 3: Flatten results (each result is a list of DataFrames)
    df_list: list[pd.DataFrame] = []
    for result in results:
        if result:  # result is a list of DataFrames
            df_list.extend(result)

    if not df_list:
        return [pd.DataFrame()]

    return df_list


def load_data(
    folder: str,
    filename: str = "ratios.csv",
    max_workers: int = 8,
    exclude_dirs: list[str] | None = None,
    in_subfolder: bool = False,
) -> pd.DataFrame:
    dataframes = load_data_basic(
        folder, filename, max_workers, exclude_dirs, in_subfolder
    )

    # Phase 4: Merge all DataFrames
    combined_df = pd.concat(dataframes, ignore_index=True)
    return post_process(combined_df)


def post_process(combined_df):
    # Phase 5: Post-processing
    if "commit_ratio" in combined_df.columns:
        combined_df["commit_ratio"].fillna(0, inplace=True)

    if "period_start" in combined_df.columns:
        combined_df["period_start"] = pd.to_datetime(
            combined_df["period_start"], format="mixed"
        )

    if "add_minus_del" in combined_df.columns:
        combined_df["velocity"] = combined_df["add_minus_del"]
        combined_df.drop(columns=["add_minus_del"], inplace=True)

    if "add_minus_del_ratio" in combined_df.columns:
        combined_df["velocity_ratio"] = combined_df["add_minus_del_ratio"]
        combined_df.drop(columns=["add_minus_del_ratio"], inplace=True)

    return combined_df


def load_and_aggregate_tool_metrics(base_dir: str) -> pd.DataFrame:
    """
    Load tool_metrics.csv files from subdirectories and aggregate metrics per project.

    Args:
        base_dir: Root directory to search for tool_metrics.csv files

    Returns:
        DataFrame with columns: project, total_files, total_lines, total_changes
    """
    # Load data using common.load_data, but override filename, and go in tools subfolder
    df = load_data(base_dir, filename="tool_metrics.csv", in_subfolder=True)

    if df.empty:
        print("No tool_metrics.csv files found.")
        return pd.DataFrame()

    # Aggregate per project (project column is added by load_data based on subfolder_name)
    if "project" not in df.columns:
        print("Error: 'project' column not found in loaded data.")
        return pd.DataFrame()
    return aggregate_tool_metrics(df)


def aggregate_tool_metrics(df):
    # Group by project and compute metrics
    aggregated = (
        df.groupby("project")
        .agg(
            total_files=("file_path", "count"),  # Count of rows (files)
            total_lines=("lines", "sum"),
            total_changes=("modifications", "sum"),
        )
        .reset_index()
    )

    return aggregated


def _sanitize_latex_caption(caption: str) -> str:
    """
    Sanitize caption text for LaTeX while preserving \command references.
    
    Escapes special LaTeX characters except within \command patterns.
    """
    # Extract all \command patterns to preserve them
    command_pattern = r'\\[a-zA-Z]+(?:[a-zA-Z0-9]*)?'
    commands = re.findall(command_pattern, caption)
    
    # Replace commands with placeholders
    sanitized = caption
    placeholders = {}
    for i, cmd in enumerate(commands):
        placeholder = f"__CMD_{i}__"
        placeholders[placeholder] = cmd
        sanitized = sanitized.replace(cmd, placeholder, 1)
    
    # Escape LaTeX special characters
    special_chars = [
        ("_", "\\_"),
        ("&", "\\&"),
        ("%", "\\%"),
        ("#", "\\#"),
        ("$", "\\$"),
    ]
    for char, escaped in special_chars:
        sanitized = sanitized.replace(char, escaped)
    
    # Restore commands
    for placeholder, cmd in placeholders.items():
        sanitized = sanitized.replace(placeholder, cmd)
    
    return sanitized


def generate_visualization_report(
    figures: list[Figure], partial_path: str | Path, separate_files: bool = False, captions: list[str] | None = None, labels: list[str] | None = None, open_result: bool = False
):
    """
    Generates a PDF report with matplotlib Figures.

    Args:
        figures: List of matplotlib Figure objects
        partial_path: Relative path including filename (e.g., 'output/report.pdf')
        separate_files: If True, save each figure in an individual PDF file.
                       If False (default), save all figures in a single multi-page PDF.
        captions: Optional list of caption strings matching figures (saved as .txt files when separate_files=True)
        labels: Optional list of figure labels matching figures (saved on line 1 of .txt files when separate_files=True)
        open_result: If True, open the generated PDF(s) or directory automatically. Default is False.

    Raises:
        RuntimeError: If input path has not been set
    """
    if _input_path is None:
        raise RuntimeError("Input path not set. Call set_input_path() first.")

    output_path = _input_path / "report" / partial_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(figures, list) or not all(
        isinstance(fig, plt.Figure) for fig in figures
    ):
        raise TypeError("figures must be a list of matplotlib Figure objects.")
    if not figures:
        raise ValueError("No figures provided. Cannot generate empty report.")

    if separate_files:
        # Create a directory for individual PDFs
        pdf_dir = output_path.parent / output_path.stem
        pdf_dir.mkdir(parents=True, exist_ok=True)
        print(f"📊 Generating {len(figures)} individual PDF files...")

        for i, fig in enumerate(figures):
            pdf_path = pdf_dir / f"{output_path.stem}_{i + 1:03d}.pdf"
            print(f"🖼️  Saving figure {i + 1}/{len(figures)} to {pdf_path.name}...")
            fig.savefig(pdf_path, bbox_inches="tight", dpi=300)
            plt.close(fig)
            
            # Save caption file if provided (label on line 1, then caption)
            if captions and i < len(captions) and captions[i]:
                caption_path = pdf_path.with_suffix(".txt")
                caption_lines = []
                # Add label on first line if provided
                if labels and i < len(labels):
                    caption_lines.append(labels[i])
                # Add caption
                caption_lines.append(captions[i])
                caption_path.write_text("\n".join(caption_lines))

        print(f"✅ Individual PDF files saved to: {pdf_dir}")

        if open_result:
            try:
                if platform.system() == "Darwin":
                    os.system(f"open '{pdf_dir}'")
                elif platform.system() == "Windows":
                    os.startfile(str(pdf_dir))
                elif platform.system() == "Linux":
                    os.system(f"xdg-open '{pdf_dir}'")
                print("🔓 PDF directory opened automatically.")
            except Exception as e:
                print(f"📄 Could not open directory automatically. You can open it at: {e}")
    else:
        # Original behavior: single multi-page PDF
        print(f"📊 Generating PDF report with {len(figures)} visualizations...")
        with PdfPages(output_path) as pdf:
            for i, fig in enumerate(figures):
                print(f"🖼️  Adding page {i + 1}...")
                pdf.savefig(fig, bbox_inches="tight", dpi=300)
                plt.close(fig)
        print(f"✅ Multi-page report saved as: {output_path}")

        if open_result:
            try:
                if platform.system() == "Darwin":
                    os.system(f"open '{output_path}'")
                elif platform.system() == "Windows":
                    os.startfile(str(output_path))
                elif platform.system() == "Linux":
                    os.system(f"xdg-open '{output_path}'")
                print("🔓 PDF opened automatically.")
            except Exception as e:
                print(f"📄 Could not open PDF automatically. You can open it at: {e}")


def filter_df(
    df: pd.DataFrame, args: argparse.Namespace, col: str = "github_url"
) -> pd.DataFrame:
    # Parse the lists
    exclude_list: list[str] = [
        item.strip() for item in args.exclude.split(",") if item.strip()
    ]
    select_list: list[str] = [
        item.strip() for item in args.select.split(",") if item.strip()
    ]

    print(f"Original dataframe shape: {df.shape}")
    print(f"Select list: {select_list}")
    print(f"Exclude list: {exclude_list}")

    # Apply select filter first (if specified)
    if select_list:
        # Escape special regex characters and join with |
        import re

        pattern = "|".join(re.escape(item) for item in select_list)
        mask = df[col].str.contains(pattern, case=False, na=False, regex=True)
        df = df[mask]
        print(f"After select filter: {df.shape}")

    # Apply exclude filter (if specified)
    if exclude_list:
        import re

        pattern = "|".join(re.escape(item) for item in exclude_list)
        mask = ~df[col].str.contains(pattern, case=False, na=False, regex=True)
        df = df[mask]
        print(f"After exclude filter: {df.shape}")

    return df


def import_metric_file(df: pd.DataFrame, file_path: Path | str) -> pd.DataFrame:
    """
    Prepare a metric file for merging by:
    1. Normalizing column names from camelCase to snake_case
    2. Ensuring github_url exists (constructing from 'name' or 'project_name' if needed)
    3. Calculating age_years from createdAt column if present
    """

    # Helper function to convert camelCase to snake_case
    def camel_to_snake(name: str) -> str:
        """Convert camelCase to snake_case."""
        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    # Normalize column names to snake_case
    df.columns = [camel_to_snake(col) for col in df.columns]

    # Ensure github_url exists
    if "github_url" not in df.columns:
        # Try to construct from 'name' or 'project_name'
        if "name" in df.columns:
            df["github_url"] = "https://www.github.com/" + df["name"]
            print(f"Constructed 'github_url' from 'name' column in {file_path}")
            print("Sample URLs:")
            for url in df["github_url"].head(3):
                print(f"  {url}")
        elif "project_name" in df.columns:
            df["github_url"] = "https://www.github.com/" + df["project_name"]
            print(f"Constructed 'github_url' from 'project_name' column in {file_path}")
            print("Sample URLs:")
            for url in df["github_url"].head(3):
                print(f"  {url}")
        else:
            raise ValueError(
                f"'github_url' column not found in {file_path}, and no 'name' or 'project_name' column to construct it from"
            )

    # Calculate age_years from createdAt column if present
    if "created_at" in df.columns:

        def calculate_age_years(created_at_str):
            """Calculate repository age in years from creation date string."""
            try:
                created_date = datetime.fromisoformat(
                    created_at_str.replace("Z", "+00:00")
                )
                current_date = datetime.now(created_date.tzinfo)
                age_years = (current_date - created_date).days / 365.25
                return round(age_years, 1)
            except:
                return 0

        df["age_years"] = df["created_at"].apply(calculate_age_years)
        print(f"Calculated age_years from 'created_at' column")

    # Verify github_url is present
    if "github_url" not in df.columns:
        raise ValueError(
            f"'github_url' column not found after processing in {file_path}"
        )

    return df


def merge_dataframes_by_github_url(
    file1_path: Path,
    file2_path: Path,
    how: MergeHow = "inner",
    suffixes: Suffixes = ("_left", "_right"),
):
    """
    Reads two CSV files as pandas DataFrames and merges them on the 'github_url' column.
    Applies import_metric_file processing to the second dataframe (metrics file).
    """
    df1 = pd.read_csv(file1_path)
    df2 = pd.read_csv(file2_path)

    print(f"DataFrame 1 ({file1_path}): shape {df1.shape}")
    print(f"DataFrame 2 ({file2_path}): shape {df2.shape}")

    # Process the metric file (df2)
    df1 = import_metric_file(df1, file2_path)

    merged = pd.merge(df1, df2, on="github_url", how=how, suffixes=suffixes)
    print(f"Merged result: shape {merged.shape}")
    return merged


# Compile regex once at module level
NON_ALNUM = re.compile(r"[^a-z0-9]")
GITHUB_PREFIX = re.compile(r"https?://github\.com/", re.IGNORECASE)


@lru_cache(maxsize=None)
def normalize(s: str) -> str:
    """Normalize a string for comparison: lowercase, remove non-alphanumerics."""
    return NON_ALNUM.sub("", s.lower())


def best_match(name, urls_data):
    """Find the best match for a given name among GitHub URLs.
    Returns (url, index) or ('', -1) if no match."""
    norm_name = normalize(name)

    for idx, (url, norm_repo, norm_repo_name) in enumerate(urls_data):
        if norm_repo in norm_name or norm_name in norm_repo:
            return url, idx
        if norm_repo_name == norm_name:
            return url, idx
    return "", -1


def match_projects_to_urls(projects, urls):
    """Match project directory names to GitHub URLs."""
    # Preprocess all URLs once
    urls_data = []
    for url in urls:
        repo_path = GITHUB_PREFIX.sub("", url)
        norm_repo = normalize(repo_path)
        repo_name = repo_path.split("/")[-1] if "/" in repo_path else repo_path
        norm_repo_name = normalize(repo_name)
        urls_data.append((url, norm_repo, norm_repo_name))

    # Match with progress bar and URL removal
    matches = []
    unmatched = []
    for project in tqdm(projects, desc="Matching projects"):
        match_url, idx = best_match(project, urls_data)
        matches.append({"project": project, "github_url": match_url})

        # Remove matched URL to avoid duplicate matches
        if idx != -1:
            urls_data.pop(idx)
        else:
            unmatched.append(project)

    print(f"unmatched projects: {len(unmatched)}")
    print(unmatched)
    return pd.DataFrame(matches)


def load_from_report(args, columns=list[str] | None) -> pd.DataFrame:
    path = find_and_select_report(Path(args.input))

    df: pd.DataFrame = pd.read_csv(path)
    df = filter_df(df, args, "github_url")
    df["project"] = df["path"].apply(lambda path: path.split("/")[-1])
    if columns:
        columns.extend(["project", "github_url"])
        df = df[columns]
    return df


def default_args(script_help: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=script_help)
    _ = parser.add_argument(
        "input",
        default="../temp/october-31",
        help="CSV file or dir containing overall metrics of projects to analyse",
    )
    _ = parser.add_argument(
        "--exclude",
        type=str,
        default="",
        help="Comma-separated list of patterns to exclude from github_url",
    )
    _ = parser.add_argument(
        "--select",
        type=str,
        default="",
        help="Comma-separated list of patterns to select from github_url",
    )
    return parser


if __name__ == "__main__":
    parser = default_args("Computes basic adoption statistics ")
    args = parser.parse_args()
    set_input_path(args.input)
    # you could add report-specific args here
    path = find_and_select_report(Path(args.input))

    df: pd.DataFrame = pd.read_csv(path)
    df = filter_df(df, args, "github_url")

from collections import defaultdict
from repo_analyzer import OverallResults, AnalysisResult, RepoAnalyzer
from compute_metrics import RatioMetrics
from pr_merge_types import PullRequestStats
from utils.parallel import run_parallel
from utils.github import GITHUB_API
from utils.cloning import move_directory_robust, _clear_readonly
import csv
import os
import argparse
import sys
import signal
import shutil
from pathlib import Path
from typing import Any, TypeVar
from utils import import_export

# Global list to store the results from the analysis workers.
analysis_results: list[OverallResults] = []
# Global flag to handle graceful shutdown
shutdown_flag = False

def find_and_select_report(experiment_dir: Path) -> Path | None:
    """Finds analysis reports and lets the user select one."""
    reports = list(experiment_dir.glob('analysis_report_*.csv')) + \
              list(experiment_dir.glob('tmp_analysis_*.csv'))

    if not reports:
        print("No analysis reports found to retry from.", file=sys.stderr)
        return None

    def sort_key(path: Path) -> int:
        try:
            # Extracts the number from filenames like 'analysis_report_12345.csv'
            return int(path.stem.split('_')[-1])
        except (ValueError, IndexError):
            return 0

    reports.sort(key=sort_key, reverse=True)

    if len(reports) == 1:
        print(f"Found one report: {reports[0].name}")
        return reports[0]

    print("Multiple reports found. Please choose one to use as a baseline:")
    for i, report in enumerate(reports):
        default_str = " (default)" if i == 0 else ""
        print(f"  [{i+1}] {report.name}{default_str}")

    while True:
        try:
            choice = input(f"Enter number (1-{len(reports)}) or press Enter for default: ").strip()
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

def save_results_to_csv(overall_results: list[OverallResults], experiment_name: str, resume: bool, base_dir: str, final:bool=False):
    """Saves the collected analysis results to a CSV file."""
    T = TypeVar("T")
    def write(fname:str,data:list[Any],cls:type[T], num_name:bool=True):
        resume_str : str = "_resume" if resume else ""
        if num_name:
            summary_file = output_path / f'{fname}_{len(data)}{resume_str}.csv'
        else:
            summary_file = output_path / f'{fname}{resume_str}.csv'
        try:
            print(f"\nWriting {len(data)} results to temporary {summary_file}...")
            import_export.write_dataclasses_to_csv(summary_file, data, cls)
            print(f"Successfully saved results to {summary_file}")
        except IOError as e:
            print(f"Error writing to file {summary_file}: {e}", file=sys.stderr)

    if not overall_results:
        print("No results to save.")
        return
    output_path = Path(base_dir) / experiment_name
    output_path.mkdir(parents=True, exist_ok=True)
    results: list[AnalysisResult] = [r[0] for r in overall_results]

    if not final:
        # just dump the first one
        write("tmp_analysis", results, AnalysisResult)
    else:
        successes = [r for r in results if not r.error]
        write("analysis_report", successes, AnalysisResult)
        retry = [r for r in results if r.error]
        write("retry_list", retry, AnalysisResult, False)
        stats: list[PullRequestStats]= [r[1] for r in overall_results]
        write("pull_requests_stats", stats, PullRequestStats)
        all_ratios: dict[str, list[RatioMetrics]] = defaultdict(list)
        for _,_,ratios in overall_results:
            for name, ratio in ratios.items():
                all_ratios[name].append(ratio)
        for name, ratios in all_ratios.items():
            write(f"{name}_ratios", ratios, RatioMetrics)



def get_projects_to_retry(project_list_path: str, report_path: Path) -> list[str]:
    """Determines which projects to retry based on a previous report."""
    all_urls = read_urls_from_csv(project_list_path)
    if not all_urls:
        return []

    try:
        report_df = import_export.read_dataclasses_from_csv(report_path, AnalysisResult)
        analyzed_urls = {row.github_url for row in report_df}
    except FileNotFoundError:
        print(f"Report file not found at {report_path}. Retrying all projects.", file=sys.stderr)
        return list(all_urls)
    except Exception as e:
        print(f"Error reading report {report_path}: {e}. Retrying all projects.", file=sys.stderr)
        return list(all_urls)


    # 1. Projects in the input list but not in the report
    missing_urls = all_urls - analyzed_urls
    print(f"Found {len(missing_urls)} projects from the input list that are not in the report.")

    # 2. Projects with errors (excluding 404s)
    failed_urls = set()
    for row in report_df:
        if row.error and "404 Client Error: Not Found" not in row.error:
            failed_urls.add(row.github_url)
    print(f"Found {len(failed_urls)} projects with non-404 errors in the report.")

    projects_to_retry = list(missing_urls | failed_urls)
    print(f"Total projects to retry: {len(projects_to_retry)}")
    return projects_to_retry

def save_combined_results(
    new_results: list[OverallResults],
    original_report_path: Path,
    experiment_name: str,
    base_dir: str
):
    """Combines new results with an existing report and saves them."""
    if not new_results:
        print("No new results to save.")
        return

    exp_dir = Path(base_dir) / experiment_name

    # --- 1. Determine original file names and counts ---
    try:
        original_count = int(original_report_path.stem.split('_')[-1])
        base_name = original_report_path.stem.rsplit('_', 1)[0]
    except (ValueError, IndexError):
        print(f"Could not parse original count from {original_report_path.name}. Aborting save.", file=sys.stderr)
        return

    # --- 2. Prepare new data ---
    new_analysis_results = [r[0] for r in new_results]
    new_pr_stats = [r[1] for r in new_results]
    new_ratios_dict: dict[str, list[RatioMetrics]] = defaultdict(list)
    for _, _, ratios in new_results:
        for name, ratio in ratios.items():
            new_ratios_dict[name].append(ratio)

    # --- 3. Function to process each file type ---
    T = TypeVar("T")
    def process_file(
        base_fname: str,
        new_data: list[Any],
        data_class: type[T],
        is_analysis_report: bool = False
    ):
        original_file = exp_dir / f"{base_fname}_{original_count}.csv"
        if not original_file.exists():
            print(f"Warning: Original file {original_file.name} not found. Creating a new one.", file=sys.stderr)
            old_data = []
        else:
            try:
                old_data = import_export.read_dataclasses_from_csv(original_file, data_class)
            except Exception as e:
                print(f"Error reading old data from {original_file.name}: {e}. Skipping.", file=sys.stderr)
                return

        # For the main analysis report, filter out the retried projects from the old data
        if is_analysis_report:
            new_urls = {r.github_url for r in new_data}
            old_data = [r for r in old_data if r.github_url not in new_urls]

        combined_data = old_data + new_data
        new_count = len(combined_data)
        new_file = exp_dir / f"{base_fname}_{new_count}.csv"

        try:
            print(f"Writing {len(combined_data)} combined results to {new_file.name}...")
            import_export.write_dataclasses_to_csv(new_file, combined_data, data_class)
            if original_file.exists() and original_file != new_file:
                original_file.unlink() # remove old file
        except Exception as e:
            print(f"Error writing combined file {new_file.name}: {e}", file=sys.stderr)

    # --- 4. Process all file types ---
    process_file(base_name, new_analysis_results, AnalysisResult, is_analysis_report=True)
    process_file("pull_requests_stats", new_pr_stats, PullRequestStats)
    for ratio_name, ratio_data in new_ratios_dict.items():
        process_file(f"{ratio_name}_ratios", ratio_data, RatioMetrics)


def signal_handler(sig, frame):
    """Handle Ctrl+C to save progress and exit gracefully."""
    global shutdown_flag
    if not shutdown_flag:
        print('\nCtrl+C detected. The program will save progress and exit after current tasks complete.', file=sys.stderr)
        shutdown_flag = True
    # The main finally block will handle the saving.
    # We don't exit here immediately to allow run_parallel to finish gracefully.
    raise KeyboardInterrupt

def read_urls_from_csv(filepath: str) -> set[str]:
    """Reads a set of URLs from a CSV file, assuming a 'github_url' column.
    If github_url is missing, reconstructs from 'project_name' or 'name' column."""
    if not os.path.exists(filepath):
        print(f"Error: Input file not found at {filepath}", file=sys.stderr)
        return set()

    urls: set[str] = set()
    try:
        with open(filepath, 'r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            fieldnames = reader.fieldnames or []
            
            # Check available columns
            has_github_url = "github_url" in fieldnames
            has_project_name = "project_name" in fieldnames
            has_name = "name" in fieldnames
            
            if not has_github_url and not has_project_name and not has_name:
                print(f"Error: CSV file {filepath} must have 'github_url', 'project_name', or 'name' column", file=sys.stderr)
                return set()
            
            if not has_github_url:
                source_col = "project_name" if has_project_name else "name"
                print(f"Warning: 'github_url' column not found in {filepath}. Reconstructing from '{source_col}'.", file=sys.stderr)
            
            for row in reader:
                url = row.get('github_url')
                if url:
                    urls.add(url)
                else:
                    # Try project_name first, then name
                    project_name = row.get('project_name') if has_project_name else row.get('name')
                    if project_name:
                        reconstructed_url = f"https://www.github.com/{project_name}"
                        urls.add(reconstructed_url)
    except Exception as e:
        print(f"Error reading {filepath}: {e}", file=sys.stderr)
    return urls


def read_github_urls_and_paths(filepath: str) -> set[tuple[str, str]]:
    if not os.path.exists(filepath):
        print(f"Error: Input file not found at {filepath}", file=sys.stderr)
        return set()

    result:set[tuple[str,str]] = set()
    try:
        with open(filepath, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            required_cols = {"github_url", "path"}
            if not required_cols.issubset(reader.fieldnames or []):
                print(f"Error: CSV file {filepath} must have columns: {required_cols}", file=sys.stderr)
                return set()

            for row in reader:
                url = row.get("github_url")
                path = row.get("path")
                if url and path:
                    result.add((url, path))

    except Exception as e:
        print(f"Error reading {filepath}: {e}", file=sys.stderr)

    return result


def analysis_worker(task: dict[str, Any]) -> OverallResults:
    """
    Worker function that records timing and calls the main analysis function.
    """
    analyzer = RepoAnalyzer(
        exp_name=task['exp_name'],
        github_url=task['github_url'],
        analysis_period=task['analysis_period'],
        sample=task['sample'],
        base_dir=task['base_dir'],
        resume = task['resume']
    )
    return analyzer.do_analysis()

def get_classification_dir(result: AnalysisResult) -> str:
    """Determines the classification directory based on the analysis status."""
    if result.error:
        return "analysis_error"

    if result.tool_use and result.commit_use:
        return "tool_commit_adopters"
    elif result.tool_use:
        return "tool_only_adopters"
    elif result.ignored_tools and result.commit_use:
        return "ignored_tools_commit_adopters"
    elif result.commit_use:
        return "commit_only_adopters"
    elif result.ignored_tools:
        return "ignored_tools_only_adopters"
    else:
        return "non_adopters"

def main():
    """Main function to parse arguments and run analysis."""
    signal.signal(signal.SIGINT, signal_handler)

    parser = argparse.ArgumentParser(description="Run the data analysis experiment")
    parser.add_argument("project_list", help="CSV file containing github_url of projects to analyse")
    parser.add_argument("sample_list", nargs='?', help="CSV file containing github_url of projects to sample for further analysis (optional with --analyze-all)")
    parser.add_argument("experiment_name", help="name of the experiment")
    parser.add_argument('--max-workers', type=int, default=os.cpu_count(), help="Number of projects to process in parallel.")
    parser.add_argument('--analysis-date', type=str, default='2025-01-01', help="Default start of analysis date in YYYY-MM-DD format.")
    parser.add_argument('--output-dir', type=str, default='../temp', help="Base directory for experiment output.")
    parser.add_argument("--analyze-all", action="store_true", help="Analyze all projects, not just a sample. If set, sample_list is optional and defaults to project_list.")
    parser.add_argument("--resume", action="store_true",help="Resume the analysis")
    parser.add_argument("--retry", action="store_true", help="Retry failed or missing projects from a previous run.")

    args = parser.parse_args()

    if not GITHUB_API.token_pool.tokens:
        parser.error(
            "No GitHub tokens available. Set AIE_GITHUB_TOKENS or create "
            "config/tokens.ini before starting the analysis."
        )

    # Determine sample list
    if args.analyze_all:
        if not args.sample_list:
            args.sample_list = args.project_list
    elif not args.sample_list:
        print("Error: sample_list is required unless --analyze-all is specified", file=sys.stderr)
        return

    sample_urls = read_urls_from_csv(args.sample_list)
    tasks = []
    original_report_path = None


    if args.retry or args.resume:
        # find which report to start with
        exp_dir = Path(args.output_dir) / args.experiment_name
        original_report_path = find_and_select_report(exp_dir)
        if not original_report_path:
            return

        if args.retry:
            project_urls = get_projects_to_retry(args.project_list, original_report_path)
            if not project_urls:
                print("No projects to retry. Exiting.")
                return
            tasks = [
                {
                    'github_url': url,
                    'exp_name': args.experiment_name,
                    'analysis_period': args.analysis_date,
                    'sample': sample_urls,
                    'base_dir': args.output_dir,
                    'resume': False # Retry a fresh analysis for the selected projects, unlike resume
                }
                for url in project_urls
            ]
        else: # we resume
            project_urls_paths = list(read_github_urls_and_paths(str(original_report_path)))
            tasks = [
                {
                    'github_url': url,
                    'exp_name': args.experiment_name,
                    'analysis_period': args.analysis_date,
                    'sample': sample_urls,
                    'base_dir':path,
                    'resume': True # we don't download stuff
                }
                for url, path in project_urls_paths
            ]

    else:
        project_urls = list(read_urls_from_csv(args.project_list))

        if not project_urls:
            print("No projects to analyze. Exiting.")
            return

        tasks = [
            {
                'github_url': url,
                'exp_name': args.experiment_name,
                'analysis_period': args.analysis_date,
                'sample': sample_urls,
                'base_dir': args.output_dir,
                'resume': False
            }
            for url in project_urls
        ]

    def result_callback(results: tuple[AnalysisResult, PullRequestStats, RatioMetrics]):
        """
            Appends a result, moves the output dir, and saves progress.
            We move into subdirectories only one the first run,
            not if we resume the analysis to recompute metrics.
        """
        if not results:
            return

        result, stats, ratio = results
        classification = get_classification_dir(result)
        old_path = Path(result.path)

        # We also do this for args.retry
        if old_path.exists() and not args.resume:
            repo_name = old_path.name
            # remove /working/dirname
            exp_path = old_path.parent.parent

            new_parent_dir = exp_path / classification
            new_parent_dir.mkdir(parents=True, exist_ok=True)
            new_path = new_parent_dir / repo_name
            result.path = str(new_path)

            try:
                # If the destination already exists (e.g., from a previous run), remove it.
                if new_path.exists():
                    shutil.rmtree(new_path, onexc=_clear_readonly)

                move_directory_robust(old_path, new_path)

                new_log_path = new_path / Path(result.log_path).name
                result.log_path = str(new_log_path)

            except Exception as e:
                print(f"Error moving directory for {repo_name} from {old_path} to {new_path}: {e}", file=sys.stderr)
                # Keep original result if move fails

        analysis_results.append((result, stats, ratio))
        if not args.resume and len(analysis_results) > 0 and len(analysis_results) % 500 == 0:
            print(f"\n--- Reached {len(analysis_results)} projects. Checking rate limits ... ---")
            GITHUB_API.print_rate_limit_status()


        if len(analysis_results) > 0 and len(analysis_results) % 25000 == 0:
            print(f"\n--- Reached {len(analysis_results)} projects. Saving intermediate results... ---")
            save_results_to_csv(list(analysis_results), args.experiment_name, args.resume, args.output_dir)
            print(f"\n--- Intermediate save complete. Resuming analysis... ---")

    try:
        print(f"Starting analysis with {args.max_workers} workers... Press Ctrl+C to interrupt and save progress.")
        _ = run_parallel(
            worker_func=analysis_worker,
            tasks=tasks,
            max_workers=args.max_workers,
            executor_type='process' if args.resume else 'thread',
            description=f"Analyzing {len(tasks)} projects",
            result_callback=result_callback
        )
    except Exception as e:
        print(f"An unexpected error occurred during parallel execution: {e}", file=sys.stderr)
    finally:
        if shutdown_flag:
            print("Analysis interrupted.")
        else:
            print("Analysis run finished.")

        if args.retry and original_report_path:
            save_combined_results(analysis_results, original_report_path, args.experiment_name, args.output_dir)
        else:
            save_results_to_csv(analysis_results, args.experiment_name, args.resume, args.output_dir, final=True)

if __name__ == "__main__":
    main()

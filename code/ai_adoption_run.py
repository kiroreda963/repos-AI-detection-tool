"""
Entry point for the cheapest AI-adoption check: no clones, no fix/revert
attribution (that step is ~95% of the lean pipeline's API cost and isn't
needed here) - just commit history + file-manifest + .gitignore checks.

This is fully standalone and does not import or modify any lean_* file.

Usage:
    python ai_adoption_run.py project_list.csv my_experiment --analysis-date 2025-01-01
    (run it again with the same arguments to resume - finished repos are skipped)

Output, under <output-dir>/<experiment_name>/:
  - ai_adoption_summary.csv   one row per repo
  - run_summary.csv           one row, aggregate totals for the whole run (incl. API call counts)
"""
import argparse
import csv
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ai_adoption_analyzer import AIAdoptionAnalyzer, AIAdoptionSummary
from utils import import_export
from utils.github import GITHUB_API
from utils.parallel import run_parallel

summary_lock = threading.Lock()
summaries_by_url: dict[str, AIAdoptionSummary] = {}
shutdown_flag = False
run_started_at = time.time()


@dataclass
class RunSummary:
    experiment_name: str
    total_repos: int
    ai_adopter_repos: int
    non_adopter_repos: int
    repos_with_errors: int
    total_commits: int
    total_ai_commits: int
    total_human_commits: int
    total_bot_commits: int
    rest_api_calls: int
    graphql_api_calls: int
    rate_limit_retries: int
    api_errors: int
    started_at: str
    updated_at: str
    elapsed_seconds: float


def signal_handler(sig, frame):
    global shutdown_flag
    if not shutdown_flag:
        print("\nCtrl+C detected. Finishing in-flight repos, then stopping - "
              "already-finished repos are already saved and will be skipped next run.", file=sys.stderr)
        shutdown_flag = True
    raise KeyboardInterrupt


def read_urls_from_csv(filepath: str) -> set[str]:
    """Reads github_url values from a CSV (falls back to project_name/name)."""
    if not os.path.exists(filepath):
        print(f"Error: Input file not found at {filepath}", file=sys.stderr)
        return set()

    urls: set[str] = set()
    with open(filepath, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = row.get("github_url")
            if url:
                urls.add(url)
                continue
            name = row.get("project_name") or row.get("name")
            if name:
                urls.add(f"https://www.github.com/{name}")
    if not urls:
        print(f"Error: {filepath} must have a 'github_url', 'project_name', or 'name' column", file=sys.stderr)
    return urls


def per_repo_csv_path(output_dir: Path) -> Path:
    return output_dir / "ai_adoption_summary.csv"


def run_summary_csv_path(output_dir: Path) -> Path:
    return output_dir / "run_summary.csv"


def load_existing_summaries(output_dir: Path) -> dict[str, AIAdoptionSummary]:
    path = per_repo_csv_path(output_dir)
    if not path.exists():
        return {}
    try:
        rows = import_export.read_dataclasses_from_csv(path, AIAdoptionSummary)
        existing = {r.github_url: r for r in rows}
        print(f"Resuming: {len(existing)} repos already completed in {path}, will be skipped.")
        return existing
    except Exception as e:
        print(f"Warning: could not read existing {path} ({e}). Starting fresh.", file=sys.stderr)
        return {}


def build_run_summary(experiment_name: str, rows: list[AIAdoptionSummary]) -> RunSummary:
    with GITHUB_API._call_counts_lock:
        calls = dict(GITHUB_API._call_counts)

    return RunSummary(
        experiment_name=experiment_name,
        total_repos=len(rows),
        ai_adopter_repos=sum(1 for r in rows if r.is_ai_adopter),
        non_adopter_repos=sum(1 for r in rows if not r.is_ai_adopter and not r.error),
        repos_with_errors=sum(1 for r in rows if r.error),
        total_commits=sum(r.total_commits for r in rows),
        total_ai_commits=sum(r.ai_commits for r in rows),
        total_human_commits=sum(r.human_commits for r in rows),
        total_bot_commits=sum(r.bot_commits for r in rows),
        rest_api_calls=calls.get("rest", 0),
        graphql_api_calls=calls.get("graphql", 0),
        rate_limit_retries=calls.get("rate_limited", 0),
        api_errors=calls.get("errors", 0),
        started_at=datetime.fromtimestamp(run_started_at, tz=timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=round(time.time() - run_started_at, 1),
    )


def write_outputs(output_dir: Path, experiment_name: str):
    with summary_lock:
        rows = list(summaries_by_url.values())
    import_export.write_dataclasses_to_csv(per_repo_csv_path(output_dir), rows, AIAdoptionSummary)
    import_export.write_dataclasses_to_csv(
        run_summary_csv_path(output_dir), [build_run_summary(experiment_name, rows)], RunSummary
    )


def analysis_worker(task: dict) -> AIAdoptionSummary:
    analyzer = AIAdoptionAnalyzer(task["github_url"], analysis_period=task["analysis_period"])
    return analyzer.analyze()


def make_result_callback(output_dir: Path, experiment_name: str):
    def result_callback(summary: AIAdoptionSummary):
        with summary_lock:
            summaries_by_url[summary.github_url] = summary
        write_outputs(output_dir, experiment_name)  # rewritten after every repo - progress is never lost
    return result_callback


def main():
    signal.signal(signal.SIGINT, signal_handler)

    parser = argparse.ArgumentParser(description="Cheapest possible AI-adoption check: one CSV, no clones")
    parser.add_argument("project_list", help="CSV with a github_url column")
    parser.add_argument("experiment_name", help="name of the experiment")
    parser.add_argument("--max-workers", type=int, default=min(32, (os.cpu_count() or 4) * 4),
                         help="Parallel repos to analyze (I/O-bound: higher than CPU count is fine).")
    parser.add_argument("--analysis-date", type=str, default="2025-01-01",
                         help="Only fetch commits on/after this date (YYYY-MM-DD).")
    parser.add_argument("--output-dir", type=str, default="../temp", help="Base directory for output.")

    args = parser.parse_args()

    if not GITHUB_API.token_pool.tokens:
        parser.error("No GitHub tokens available. Set AIE_GITHUB_TOKENS or create config/tokens.ini.")

    project_urls = list(read_urls_from_csv(args.project_list))
    if not project_urls:
        print("No projects to analyze. Exiting.")
        return

    output_dir = Path(args.output_dir) / args.experiment_name
    output_dir.mkdir(parents=True, exist_ok=True)

    existing = load_existing_summaries(output_dir)
    summaries_by_url.update(existing)

    remaining_urls = [u for u in project_urls if u not in existing]
    skipped = len(project_urls) - len(remaining_urls)
    if skipped:
        print(f"Skipping {skipped} already-completed repos, {len(remaining_urls)} left to analyze.")

    if not remaining_urls:
        print("Nothing left to do - all repos already completed.")
        write_outputs(output_dir, args.experiment_name)
        return

    tasks = [{"github_url": url, "analysis_period": args.analysis_date} for url in remaining_urls]

    try:
        print(f"Starting analysis with {args.max_workers} workers... Press Ctrl+C to interrupt and save progress.")
        run_parallel(
            worker_func=analysis_worker,
            tasks=tasks,
            max_workers=args.max_workers,
            executor_type="thread",
            description=f"Analyzing {len(tasks)} projects",
            result_callback=make_result_callback(output_dir, args.experiment_name),
        )
    finally:
        print("Analysis interrupted." if shutdown_flag else "Analysis run finished.")
        print(f"{len(summaries_by_url)} repos completed total, saved to {output_dir}")


if __name__ == "__main__":
    main()

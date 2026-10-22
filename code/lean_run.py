"""
Entry point for the no-clone, API-only pipeline:
  - identifies repos as-is (no cloning, no directory moves/classification)
  - classifies every commit as Human/AI/Bot
  - flags fix / revert commits
  - attributes each fix/revert to the commit it targets, and the time elapsed (in days)
  - shows every GitHub API call live in the terminal
  - is resumable: each repo's output is written as soon as that repo finishes,
    so a crash or Ctrl+C partway through does not lose already-finished repos

Usage:
    python lean_run.py project_list.csv my_experiment --analysis-date 2025-01-01
    (run it again with the same arguments to resume - finished repos are skipped)

Output, under <output-dir>/<experiment_name>/:
  - repo_summary.csv                  one row per repo (incl. how it was identified as an AI adopter)
  - <owner>-<repo>/commits.csv         one row per commit, for that repo
  - <owner>-<repo>/fix_revert_links.csv  one row per fix/revert commit, with its target + timing
  - <owner>-<repo>/.done                marker written only once that repo's two CSVs are safely on disk
"""
import argparse
import csv
import os
import signal
import sys
import threading
from pathlib import Path

from lean_analyzer import ClassifiedCommit, FixRevertLink, LeanAnalyzer, RepoCommitSummary
from utils import import_export
from utils.github import GITHUB_API, get_repo_name_from_url
from utils.parallel import run_parallel

summary_lock = threading.Lock()
summaries_by_url: dict[str, RepoCommitSummary] = {}
shutdown_flag = False


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


def load_existing_summaries(output_dir: Path) -> dict[str, RepoCommitSummary]:
    """Resume support: repo_summary.csv is only ever updated AFTER a repo's
    per-repo CSVs + .done marker are written, so any url already in it is a
    genuinely finished repo that can be skipped."""
    summary_path = output_dir / "repo_summary.csv"
    if not summary_path.exists():
        return {}
    try:
        rows = import_export.read_dataclasses_from_csv(summary_path, RepoCommitSummary)
        existing = {r.github_url: r for r in rows}
        print(f"Resuming: {len(existing)} repos already completed in {summary_path}, will be skipped.")
        return existing
    except Exception as e:
        print(f"Warning: could not read existing {summary_path} ({e}). Starting fresh.", file=sys.stderr)
        return {}


def write_repo_summary(output_dir: Path):
    with summary_lock:
        rows = list(summaries_by_url.values())
    import_export.write_dataclasses_to_csv(output_dir / "repo_summary.csv", rows, RepoCommitSummary)


def analysis_worker(task: dict) -> tuple[RepoCommitSummary, list[ClassifiedCommit], list[FixRevertLink]]:
    analyzer = LeanAnalyzer(task["github_url"], analysis_period=task["analysis_period"])
    return analyzer.analyze()


def make_result_callback(output_dir: Path):
    def result_callback(result):
        summary, commits, links = result
        repo_folder = output_dir / (get_repo_name_from_url(summary.github_url) or "unknown-repo")
        repo_folder.mkdir(parents=True, exist_ok=True)

        import_export.write_dataclasses_to_csv(repo_folder / "commits.csv", commits, ClassifiedCommit)
        import_export.write_dataclasses_to_csv(repo_folder / "fix_revert_links.csv", links, FixRevertLink)
        (repo_folder / ".done").write_text(summary.github_url, encoding="utf-8")

        with summary_lock:
            summaries_by_url[summary.github_url] = summary
        write_repo_summary(output_dir)  # rewritten after every repo, so progress is never lost

    return result_callback


def main():
    signal.signal(signal.SIGINT, signal_handler)

    parser = argparse.ArgumentParser(description="No-clone, API-only commit/fix/revert analysis")
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
            result_callback=make_result_callback(output_dir),
        )
    finally:
        print("Analysis interrupted." if shutdown_flag else "Analysis run finished.")
        print(f"{len(summaries_by_url)} repos completed total, saved under {output_dir}")


if __name__ == "__main__":
    main()

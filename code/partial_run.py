"""
Entry point for the partial-clone pipeline: commit AI/Human/Bot detection +
lines added/deleted, fix/revert detection and linking to their original
commits with days-to-action, repo adoption_date, and AI tool identification -
using a blobless partial clone (git log/diff/blame locally) instead of the
REST+GraphQL blame calls, plus the cheap GraphQL commit-history walk for the
bulk per-commit data. See partial_analyzer.py and partial_clone_helpers.py
for the mechanics. Nothing here modifies any existing script.

Usage:
    python partial_run.py project_list.csv my_experiment --analysis-date 2025-01-01
    (run it again with the same arguments to resume - finished repos are skipped)

Output, under <output-dir>/<experiment_name>/:
  - repo_summary.csv                     one row per repo
  - run_analytics.csv                    one row, aggregate analytics for the whole run
  - error_log.csv                        one row per failure anywhere in the run, for post-run review
  - <owner>-<repo>/commits.csv           one row per commit, for that repo
  - <owner>-<repo>/fix_revert_links.csv  one row per fix/revert commit, with its target + timing
  - <owner>-<repo>/.done                  marker written once that repo's two CSVs are safely on disk
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

from error_log import ErrorLogEntry
from partial_analyzer import CommitRecord, DAY_BUCKETS, FixRevertLink, PartialCloneAnalyzer, RepoSummary
from utils import import_export
from utils.github import GITHUB_API, get_repo_name_from_url
from utils.parallel import run_parallel

summary_lock = threading.Lock()
summaries_by_url: dict[str, RepoSummary] = {}
shutdown_flag = False
run_started_at = time.time()

error_log_lock = threading.Lock()
error_log_entries: list[ErrorLogEntry] = []


@dataclass
class RunAnalytics:
    experiment_name: str
    total_repos: int
    ai_adopter_repos: int
    human_only_repos: int
    repos_with_errors: int
    total_ai_additions: int
    total_human_additions: int
    overall_ai_loc_percentage: float | None
    overall_human_loc_percentage: float | None
    # pooled rate = sum(fix+revert commits in group) / sum(total commits in group)
    fix_revert_rate_ai_adopters: float | None
    fix_revert_rate_human_only: float | None
    # exclusive day-of-action buckets, split by which repo GROUP the fix/revert happened in
    ai_adopters_bucket_0_1: int
    ai_adopters_bucket_1_3: int
    ai_adopters_bucket_3_7: int
    ai_adopters_bucket_7_30: int
    ai_adopters_bucket_30_plus: int
    human_only_bucket_0_1: int
    human_only_bucket_1_3: int
    human_only_bucket_3_7: int
    human_only_bucket_7_30: int
    human_only_bucket_30_plus: int
    rest_api_calls: int
    graphql_api_calls: int
    rate_limit_retries: int
    api_errors: int
    started_at: str
    updated_at: str
    elapsed_seconds: float


# bucket counts accumulated as repos finish (fix_revert_links aren't all kept
# in memory for the whole run - only their bucket counts are, per repo group)
bucket_counts_lock = threading.Lock()
bucket_counts = {
    "ai_adopters": {b: 0 for b in DAY_BUCKETS},
    "human_only": {b: 0 for b in DAY_BUCKETS},
}


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


def repo_summary_csv_path(output_dir: Path) -> Path:
    return output_dir / "repo_summary.csv"


def run_analytics_csv_path(output_dir: Path) -> Path:
    return output_dir / "run_analytics.csv"


def error_log_csv_path(output_dir: Path) -> Path:
    return output_dir / "error_log.csv"


def load_existing_summaries(output_dir: Path) -> dict[str, RepoSummary]:
    """Resume support: a repo's row only ever lands in repo_summary.csv AFTER
    its per-repo CSVs + .done marker are written, so anything already in
    here is a genuinely finished repo."""
    path = repo_summary_csv_path(output_dir)
    if not path.exists():
        return {}
    try:
        rows = import_export.read_dataclasses_from_csv(path, RepoSummary)
        existing = {r.github_url: r for r in rows}
        print(f"Resuming: {len(existing)} repos already completed in {path}, will be skipped.")
        return existing
    except Exception as e:
        print(f"Warning: could not read existing {path} ({e}). Starting fresh.", file=sys.stderr)
        return {}


def load_existing_errors(output_dir: Path) -> list[ErrorLogEntry]:
    """On resume, prior sessions' errors are reloaded too, so error_log.csv
    stays a complete record across however many resumes a run takes -
    unlike repo_summary.csv/run_analytics.csv, this file isn't used to
    decide what to skip, so nothing re-derives it on its own."""
    path = error_log_csv_path(output_dir)
    if not path.exists():
        return []
    try:
        return import_export.read_dataclasses_from_csv(path, ErrorLogEntry)
    except Exception as e:
        print(f"Warning: could not read existing {path} ({e}). Starting fresh.", file=sys.stderr)
        return []


def accumulate_day_buckets(summary: RepoSummary, links: list[FixRevertLink]):
    group = "ai_adopters" if summary.is_ai_adopter else "human_only"
    with bucket_counts_lock:
        for link in links:
            if link.day_bucket in bucket_counts[group]:
                bucket_counts[group][link.day_bucket] += 1


def build_run_analytics(experiment_name: str, rows: list[RepoSummary]) -> RunAnalytics:
    with GITHUB_API._call_counts_lock:
        calls = dict(GITHUB_API._call_counts)
    with bucket_counts_lock:
        buckets = {g: dict(v) for g, v in bucket_counts.items()}

    adopters = [r for r in rows if r.is_ai_adopter and not r.error]
    human_only = [r for r in rows if not r.is_ai_adopter and not r.error]

    total_ai_add = sum(r.ai_additions for r in rows)
    total_human_add = sum(r.human_additions for r in rows)
    ai_human_total = total_ai_add + total_human_add
    overall_ai_pct = round(100 * total_ai_add / ai_human_total, 2) if ai_human_total else None
    overall_human_pct = round(100 - overall_ai_pct, 2) if overall_ai_pct is not None else None

    def pooled_rate(group: list[RepoSummary]) -> float | None:
        total_commits = sum(r.total_commits for r in group)
        if not total_commits:
            return None
        total_fix_revert = sum(r.fix_revert_count for r in group)
        return round(100 * total_fix_revert / total_commits, 3)

    return RunAnalytics(
        experiment_name=experiment_name,
        total_repos=len(rows),
        ai_adopter_repos=sum(1 for r in rows if r.is_ai_adopter),
        human_only_repos=sum(1 for r in rows if not r.is_ai_adopter and not r.error),
        repos_with_errors=sum(1 for r in rows if r.error),
        total_ai_additions=total_ai_add, total_human_additions=total_human_add,
        overall_ai_loc_percentage=overall_ai_pct, overall_human_loc_percentage=overall_human_pct,
        fix_revert_rate_ai_adopters=pooled_rate(adopters),
        fix_revert_rate_human_only=pooled_rate(human_only),
        ai_adopters_bucket_0_1=buckets["ai_adopters"]["0-1"],
        ai_adopters_bucket_1_3=buckets["ai_adopters"]["1-3"],
        ai_adopters_bucket_3_7=buckets["ai_adopters"]["3-7"],
        ai_adopters_bucket_7_30=buckets["ai_adopters"]["7-30"],
        ai_adopters_bucket_30_plus=buckets["ai_adopters"]["30+"],
        human_only_bucket_0_1=buckets["human_only"]["0-1"],
        human_only_bucket_1_3=buckets["human_only"]["1-3"],
        human_only_bucket_3_7=buckets["human_only"]["3-7"],
        human_only_bucket_7_30=buckets["human_only"]["7-30"],
        human_only_bucket_30_plus=buckets["human_only"]["30+"],
        rest_api_calls=calls.get("rest", 0), graphql_api_calls=calls.get("graphql", 0),
        rate_limit_retries=calls.get("rate_limited", 0), api_errors=calls.get("errors", 0),
        started_at=datetime.fromtimestamp(run_started_at, tz=timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=round(time.time() - run_started_at, 1),
    )


def write_run_level_outputs(output_dir: Path, experiment_name: str):
    with summary_lock:
        rows = list(summaries_by_url.values())
    with error_log_lock:
        error_rows = list(error_log_entries)
    import_export.write_dataclasses_to_csv(repo_summary_csv_path(output_dir), rows, RepoSummary)
    import_export.write_dataclasses_to_csv(
        run_analytics_csv_path(output_dir), [build_run_analytics(experiment_name, rows)], RunAnalytics
    )
    import_export.write_dataclasses_to_csv(error_log_csv_path(output_dir), error_rows, ErrorLogEntry)


def analysis_worker(task: dict) -> tuple[RepoSummary, list[CommitRecord], list[FixRevertLink], list[ErrorLogEntry]]:
    analyzer = PartialCloneAnalyzer(task["github_url"], analysis_period=task["analysis_period"])
    return analyzer.analyze()


def make_result_callback(output_dir: Path, experiment_name: str):
    def result_callback(result):
        summary, commits, links, errors = result
        repo_folder = output_dir / (get_repo_name_from_url(summary.github_url) or "unknown-repo")
        repo_folder.mkdir(parents=True, exist_ok=True)

        import_export.write_dataclasses_to_csv(repo_folder / "commits.csv", commits, CommitRecord)
        import_export.write_dataclasses_to_csv(repo_folder / "fix_revert_links.csv", links, FixRevertLink)
        (repo_folder / ".done").write_text(summary.github_url, encoding="utf-8")

        accumulate_day_buckets(summary, links)
        with summary_lock:
            summaries_by_url[summary.github_url] = summary
        with error_log_lock:
            error_log_entries.extend(errors)
        write_run_level_outputs(output_dir, experiment_name)  # rewritten after every repo - progress is never lost

    return result_callback


def main():
    signal.signal(signal.SIGINT, signal_handler)

    parser = argparse.ArgumentParser(description="Partial-clone-based commit/fix-revert analysis with run analytics")
    parser.add_argument("project_list", help="CSV with a github_url column")
    parser.add_argument("experiment_name", help="name of the experiment")
    parser.add_argument("--max-workers", type=int, default=min(16, (os.cpu_count() or 4) * 2),
                         help="Parallel repos to analyze. Still somewhat lower than the API-only "
                              "pipelines, since each worker also clones a repo - but the clone is "
                              "now blobless (small/fast), so this no longer needs to be as conservative.")
    parser.add_argument("--analysis-date", type=str, default="2025-01-01",
                         help="Only fetch/classify commits on/after this date (YYYY-MM-DD). The clone itself is "
                              "full history regardless of this date (fine for young repos), so fix/revert "
                              "attribution is unaffected by where this is set.")
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
    error_log_entries.extend(load_existing_errors(output_dir))

    remaining_urls = [u for u in project_urls if u not in existing]
    skipped = len(project_urls) - len(remaining_urls)
    if skipped:
        print(f"Skipping {skipped} already-completed repos, {len(remaining_urls)} left to analyze.")

    if not remaining_urls:
        print("Nothing left to do - all repos already completed.")
        write_run_level_outputs(output_dir, args.experiment_name)
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
        print(f"{len(summaries_by_url)} repos completed total, saved under {output_dir}")


if __name__ == "__main__":
    main()

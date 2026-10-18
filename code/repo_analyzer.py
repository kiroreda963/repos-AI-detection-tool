from __future__ import annotations

import os
import shutil
import traceback
from collections import defaultdict
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from pathlib import Path

from compute_metrics import (
    RatioMetrics,
    classify_commits_classified_prs,
    compute_all_ratios,
    empty_ratio_metrics,
)
from find_tools import (
    ToolMetrics,
    ToolResult,
    find_gitignore_matches,
    find_repo_tools,
    get_file_list,
    get_gitignore_file_tools,
    read_file_list,
    tool_file_stats,
    write_file_list,
)
from get_pull_requests import get_pull_requests
from heuristics import is_commit_adopter
from pr_merge_types import PullRequestStats, analyze_pr_merges, empty_pr_stats
from utils import cloning, logging
from utils.data import Author, BasePullRequest, LogCommit, PRCommit, PullRequest
from utils.github import get_repo_name_from_url
from utils.import_export import read_dataclasses_from_csv, write_dataclasses_to_csv
from utils.metrics import AuthorType, CommitStats, FileStats
from utils.repository import GitRepository


# Utility functions (can remain outside the class or be static methods)
def ensure(dir: Path) -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    return dir


@dataclass
class CachedFileStat(FileStats):
    commit_sha: str = ""


@dataclass
class StatSummary(LogCommit):
    auth_type: AuthorType
    tool: str

    @classmethod
    def from_log_commit(
        cls, auth_type: AuthorType, tool: str, other: LogCommit
    ) -> StatSummary:
        log_commit_dict = {f.name: getattr(other, f.name) for f in fields(other)}
        return cls(**log_commit_dict, auth_type=auth_type, tool=tool)

    @classmethod
    def from_stats(cls, stats: CommitStats) -> StatSummary:
        return cls.from_log_commit(
            auth_type=stats.type, tool=stats.tool, other=stats.commit
        )


@dataclass
class AdoptionDate:
    tool: str
    adoption: datetime


def get_earliest_timestamps(
    metrics: list[ToolMetrics], commit_stats: list[CommitStats]
) -> tuple[datetime | None, list[AdoptionDate]]:
    """
    Alternative implementation using defaultdict to collect all timestamps
    per tool, then find the minimum.
    """
    timestamps_by_tool: dict[str, list[datetime]] = defaultdict(list)

    # Collect all timestamps for each tool
    for metric in metrics:
        timestamps_by_tool[metric.tool].append(metric.added_timestamp)

    for stat in commit_stats:
        if stat.type == "AI":
            timestamps_by_tool[stat.tool].append(stat.commit.date)

    if "followup" in timestamps_by_tool:
        del timestamps_by_tool["followup"]

    # Find minimum timestamp for each tool
    adoption_dates = [
        AdoptionDate(tool, min(timestamps))
        for tool, timestamps in timestamps_by_tool.items()
    ]

    if not adoption_dates:
        return None, []

    earliest_adoption = min(adoption_dates, key=lambda a: a.adoption)
    return earliest_adoption.adoption, adoption_dates


@dataclass
class AnalysisResult:
    github_url: str
    path: str
    log_path: str
    tool_use: bool
    commit_use: bool
    ignored_tools: bool
    adoption_date: datetime | None
    last_file_change: datetime | None
    ratio: float | None
    adoption_ratio: float | None
    since_adoption_commits: int | None
    recent_ratio: float | None
    recent_commits: int | None
    sampled: bool
    duration: timedelta
    error: str


OverallResults = tuple[AnalysisResult, PullRequestStats, dict[str, RatioMetrics]]


class RepoAnalyzer:
    github_url: str
    analysis_period: str
    sample: set[str]
    output_dir: Path
    resume: bool

    def __init__(
        self,
        exp_name: str,
        github_url: str,
        base_dir: str,
        analysis_period: str = "2025-08-01",
        sample: set[str] = set(),
        resume: bool = False,
        just_heuristics: bool = False,
    ):
        self.github_url = github_url
        self.analysis_period = analysis_period
        self.sample = sample
        # when True, assume all the base data is there to resume the analysis
        self.resume = resume
        # when True, assume all the external data (from github, from repos) is there
        # and re-run the analysis with changes to the heuristics, creating new files of the files depending on the heuristics
        self.just_heuristics = resume
        if resume or just_heuristics:
            self.output_dir = ensure(Path(base_dir))
        else:
            self.output_dir = ensure(
                Path(base_dir)
                / exp_name
                / "working"
                / get_repo_name_from_url(self.github_url)
            )

    def log_file(self) -> Path:
        return self.output_dir / "analysis.log"

    def clone_dir(self) -> Path:
        return ensure(self.output_dir / "clones")

    def pr_dir(self) -> Path:
        return ensure(self.output_dir / "pull_requests")

    def tool_dir(self) -> Path:
        return ensure(self.output_dir / "tools")

    def partial_clone_repo(self) -> GitRepository:
        clone_path = self.clone_dir() / "partial"
        if not os.path.exists(clone_path):
            logging.info("starting partial cloning")
            success = cloning.clone_repo(
                self.github_url, clone_path, partial=True, no_checkout=True
            )
            logging.info(f"partial cloning result: {success}")
        return GitRepository(clone_path)

    def shallow_clone_repo(self) -> GitRepository:
        clone_path = self.clone_dir() / "shallow"
        if not os.path.exists(clone_path):
            logging.info("starting shallow cloning")
            success = cloning.clone_repo(
                self.github_url,
                clone_path,
                shallow_since=self.analysis_period,
                no_checkout=True,
            )
            logging.info(f"shallow cloning result: {success}")
        return GitRepository(clone_path)

    def _analyze_repo_for_files(self) -> list[ToolMetrics]:
        logging.info(f"Starting analysis for {self.github_url}")
        if self.just_heuristics:
            file_list = read_file_list(self.output_dir / "files.txt")
        else:
            file_list = get_file_list(self.github_url)
        if file_list:
            write_file_list(self.output_dir / "files.txt", file_list)
        tool_results = find_repo_tools(file_list)

        if tool_results:
            tool_path = self.tool_dir()
            write_dataclasses_to_csv(
                tool_path / "tool_files.csv", tool_results, ToolResult
            )

            repo = self.partial_clone_repo()
            tool_stats = tool_file_stats(repo, tool_results, tool_path)
            write_dataclasses_to_csv(
                tool_path / "tool_metrics.csv", tool_stats, ToolMetrics
            )
            return tool_stats

        return []

    def _read_tool_metrics(self) -> list[ToolMetrics]:
        tool_path = self.tool_dir()
        tool_file = tool_path / "tool_metrics.csv"
        logging.info(f"reading file: {tool_file}")
        if os.path.exists(tool_file):
            logging.info("file found")
            tool_stats: list[ToolMetrics] = read_dataclasses_from_csv(
                tool_file, ToolMetrics
            )
            logging.info(f"found {len(tool_stats)} tool stats")
            return tool_stats
        return []

    def analyze_repo_for_files(self) -> list[ToolMetrics]:
        if False and self.resume:
            return self._read_tool_metrics()
        return self._analyze_repo_for_files()

    def _read_gitignore_matches(self) -> list[ToolMetrics]:
        tool_path = self.tool_dir()
        tool_file = tool_path / "gitignore_tool_matches.csv"
        logging.info(f"reading file: {tool_file}")
        if os.path.exists(tool_file):
            logging.info("file found")
            tool_stats: list[ToolMetrics] = read_dataclasses_from_csv(
                tool_file, ToolMetrics
            )
            logging.info(f"found {len(tool_stats)} tool stats")
            return tool_stats
        return []

    def _analyze_repo_gitignore(self) -> list[ToolMetrics]:
        tool_path = self.tool_dir()
        if self.just_heuristics:
            ignored_file_lines = read_file_list(tool_path / "tool_ignored_files.csv")
            ignore_matches = find_gitignore_matches(ignored_file_lines, tool_path)
        else:
            ignore_matches = get_gitignore_file_tools(self.github_url, tool_path)
        if ignore_matches:
            write_dataclasses_to_csv(
                tool_path / "tool_ignored_files.csv", ignore_matches, ToolMetrics
            )
        return ignore_matches

    def analyze_repo_gitignore(self) -> list[ToolMetrics]:
        if self.resume:
            return self._read_gitignore_matches()
        return self._analyze_repo_gitignore()

    def _analyze_repo_for_commits(self) -> tuple[list[PullRequest], list[LogCommit]]:

        logging.info("getting pull requests from github repo")
        prs = get_pull_requests(self.github_url, self.analysis_period)
        logging.info(f"got {len(prs)} pull requests")
        logging.info("getting commits from first clone log")
        repo = self.partial_clone_repo()
        start_date = datetime.fromisoformat(self.analysis_period)
        commits = repo.get_commit_info(include_merges=True, start_date=start_date)
        logging.info(f"got {len(commits)} commits")

        csv_prs = [BasePullRequest.from_subclass(pr) for pr in prs]
        write_dataclasses_to_csv(
            self.pr_dir() / "pr_list.csv", csv_prs, BasePullRequest
        )
        logging.info(f"wrote {len(prs)} pull requests")
        write_dataclasses_to_csv(
            self.output_dir / "commit_list.csv", commits, LogCommit
        )
        logging.info(f"wrote {len(commits)} commits")

        all_pr_commits: list[PRCommit] = []
        for pr in prs:
            all_pr_commits.extend(pr.commits)
        write_dataclasses_to_csv(
            self.pr_dir() / f"pr_commits.csv", all_pr_commits, PRCommit
        )
        logging.info(f"wrote {len(all_pr_commits)}  PR commits")

        return prs, commits

    def _read_pr_commits(self) -> tuple[list[PullRequest], list[LogCommit]]:
        dir_path = self.pr_dir()

        pr_list_path = dir_path / "pr_list.csv"
        pr_list: list[BasePullRequest] = []
        logging.info(f"reading file: {pr_list_path}")
        if os.path.exists(pr_list_path):
            logging.info("file found")
            pr_list = read_dataclasses_from_csv(pr_list_path, BasePullRequest)
            logging.info(f"found {len(pr_list)} PRs")

        pr_commits: list[PRCommit] = []
        pr_commits_path = dir_path / "pr_commits.csv"
        logging.info(f"reading file: {pr_commits_path}")
        if os.path.exists(pr_commits_path):
            logging.info("file found")
            pr_commits = read_dataclasses_from_csv(pr_commits_path, PRCommit)
            logging.info(f"found {len(pr_commits)} PR commits")

        commit_list_path = self.output_dir / "commit_list.csv"
        commit_list: list[LogCommit] = []
        logging.info(f"reading file: {commit_list_path}")
        if os.path.exists(commit_list_path):
            logging.info("file found")
            commit_list = read_dataclasses_from_csv(commit_list_path, LogCommit)
            logging.info(f"found {len(commit_list)} Log commits")

        prs: list[PullRequest] = [
            PullRequest.from_superclass(bpr, pr_commits) for bpr in pr_list
        ]
        return prs, commit_list

    def analyze_repo_for_commits(self) -> tuple[list[PullRequest], list[LogCommit]]:
        if self.resume or self.just_heuristics:
            return self._read_pr_commits()
        return self._analyze_repo_for_commits()

    def _write_commit_stats_to_cache(self, commit_stats: list[CommitStats]):
        """Flattens and writes CommitStats to two CSV files for caching."""
        main_stats = [StatSummary.from_stats(stat) for stat in commit_stats]
        file_stats = [
            CachedFileStat(commit_sha=stat.commit.sha, **asdict(fs))
            for stat in commit_stats
            for fs in stat.file_stats
        ]

        write_dataclasses_to_csv(
            self.output_dir / "commit_stats_main.csv", main_stats, StatSummary
        )
        write_dataclasses_to_csv(
            self.output_dir / "commit_stats_files.csv", file_stats, CachedFileStat
        )
        logging.info(
            f"Wrote {len(main_stats)} commits and {len(file_stats)} file changes to cache."
        )

    def _read_commit_stats_from_cache(self) -> list[CommitStats] | None:
        """Reads and reconstructs CommitStats from cached CSV files."""
        main_path = self.output_dir / "commit_stats_main.csv"
        files_path = self.output_dir / "commit_stats_files.csv"

        if not main_path.exists() or not files_path.exists():
            return None

        main_stats: list[StatSummary] = read_dataclasses_from_csv(
            main_path, StatSummary
        )
        file_stats: list[CachedFileStat] = read_dataclasses_from_csv(
            files_path, CachedFileStat
        )

        # Group file stats by commit sha for efficient lookup
        files_by_commit = defaultdict(list)
        for fs in file_stats:
            files_by_commit[fs.commit_sha].append(fs)

        # Reconstruct CommitStats objects
        reconstructed_stats = []
        for main_stat in main_stats:
            commit_fields = {f.name for f in fields(LogCommit)}
            commit_data = {
                k: v for k, v in asdict(main_stat).items() if k in commit_fields
            }

            # Manually reconstruct Author objects, as they are read as dicts
            if "author" in commit_data and isinstance(commit_data["author"], dict):
                commit_data["author"] = Author(**commit_data["author"])
            if "co_authors" in commit_data and isinstance(
                commit_data["co_authors"], list
            ):
                commit_data["co_authors"] = [
                    Author(**a) for a in commit_data["co_authors"]
                ]

            commit = LogCommit(**commit_data)
            reconstructed_stats.append(
                CommitStats(
                    commit=commit,
                    type=main_stat.auth_type,
                    tool=main_stat.tool,
                    file_stats=files_by_commit.get(main_stat.sha, []),
                )
            )
        return reconstructed_stats

    def compute_adoption_and_metrics(
        self,
        pull_requests: list[PullRequest],
        commits: list[LogCommit],
        tool_results: list[ToolMetrics],
    ) -> tuple[datetime | None, PullRequestStats, dict[str, RatioMetrics]]:
        logging.info("computing commit stats")

        commit_stats: list[CommitStats] | None = None
        if self.resume:
            commit_stats = self._read_commit_stats_from_cache()

        if commit_stats is None:
            logging.info("Cache miss or not resuming. Computing commit stats from git.")
            classified_prs, summary = analyze_pr_merges(pull_requests, commits, 50)
            repo = self.shallow_clone_repo()
            all_stats = repo.get_commit_stats(
                all_branches=False, include_merges=True, filter_orphan_commits=True
            )
            commit_stats = classify_commits_classified_prs(all_stats, classified_prs)
            self._write_commit_stats_to_cache(commit_stats)
        else:
            logging.info(f"Loaded {len(commit_stats)} commit stats from cache.")
            # Still need the PR merge summary, but this is fast
            _, summary = analyze_pr_merges(pull_requests, commits, 50)

        logging.info("computing commit ratios")

        # treat AI-followup as AI
        commit_stats = [s.simplify() for s in commit_stats]

        logging.info("finding adoption dates")
        earliest, adoption_dates = get_earliest_timestamps(tool_results, commit_stats)
        write_dataclasses_to_csv(
            self.output_dir / "adoption_dates.csv", adoption_dates, AdoptionDate
        )

        summary_ratios, all_commit_ratios = compute_all_ratios(
            commit_stats, self.analysis_period, earliest
        )
        logging.info("found the following ratios for project")
        write_dataclasses_to_csv(
            self.output_dir / "ratios.csv", all_commit_ratios, RatioMetrics
        )

        return earliest, summary, summary_ratios

    def do_analysis(self) -> OverallResults:

        log_path = self.log_file()
        repo_name = get_repo_name_from_url(self.github_url)
        start = datetime.now()
        commit_use = False
        tool_use = False
        ignored_tools = False
        adoption_date = None
        last_file_change = None
        ratio = None
        adoption_ratio = None
        adoption_commits = None
        recent_ratio = None
        recent_commits = None
        error = ""
        sampled = self.github_url in self.sample
        summary = empty_pr_stats()
        summary_ratios: dict[str, RatioMetrics] = {}
        # overall_ratio = empty_ratio_metrics("overall")
        # since_adoption = empty_ratio_metrics("since_adoption")

        def run_commit_analysis_internal(tool_results: list[ToolMetrics]):
            tool_adopter = len(tool_results) > 0
            prs, commits = self.analyze_repo_for_commits()
            if tool_adopter or is_commit_adopter(prs, commits):
                logging.info(
                    "this project is a tool or commit adopter, doing further analysis"
                )
                nonlocal adoption_date, summary, summary_ratios
                adoption_date, summary, summary_ratios = (
                    self.compute_adoption_and_metrics(prs, commits, tool_results)
                )
                if "overall" in summary_ratios:
                    o_ratio: float | None = summary_ratios["overall"].commit_ratio
                    if o_ratio and o_ratio > 0:
                        nonlocal commit_use, ratio
                        commit_use = True
                        ratio = o_ratio
                if "since_adoption" in summary_ratios:
                    nonlocal adoption_ratio, adoption_commits
                    adoption_ratio = summary_ratios["since_adoption"].commit_ratio
                    adoption_commits = summary_ratios["since_adoption"].commits

                if "recent_adoption" in summary_ratios:
                    nonlocal recent_ratio, recent_commits
                    recent_ratio = summary_ratios["recent_adoption"].commit_ratio
                    recent_commits = summary_ratios["recent_adoption"].commits

                logging.info("commit analysis finished")
                logging.info("PR merge report is:")

                for k, v in summary.counts.items():
                    logging.info(f"{k}: {v}")
                for k, v in summary.percent.items():
                    logging.info(f"{k}: {v}")
            else:
                logging.info(
                    "this project is not a tool or commit adopter, deleting clones"
                )
                if "december-1st" in str(self.clone_dir()):
                    logging.info(f"deleting: {str(self.clone_dir())}")
                    shutil.rmtree(self.clone_dir())
                else:
                    logging.info(f"NOT deleting: {str(self.clone_dir())}")

        # Use context manager for automatic cleanup
        with logging.repository_logger(log_path, repo_name):
            try:
                logging.info("starting analysis")
                tool_results = self.analyze_repo_for_files()
                logging.info("tool analysis finished")

                ignored_matches = self.analyze_repo_gitignore()
                if ignored_matches:
                    ignored_tools = True

                if tool_results:
                    logging.info(
                        "repository is a tool adopter, selected for further analysis"
                    )
                    tool_use = True
                    file_changes = [tr.last_modification for tr in tool_results]
                    last_file_change = max(file_changes)
                    run_commit_analysis_internal(tool_results)

                    if sampled:
                        logging.info(
                            "project is no longer a non-adopter, but we don't repeat the analysis"
                        )

                elif ignored_matches:
                    logging.info(
                        "repository has tool matches in gitignore, selected for further analysis"
                    )
                    # TODO: check if we want an empty list here
                    # probably, yes, since this is used for timestamps ...
                    run_commit_analysis_internal([])

                elif sampled:
                    logging.info(
                        "repository selected for further analysis based on sampling"
                    )
                    run_commit_analysis_internal([])

                else:
                    logging.info("analysis ends here")

            except Exception:
                error = traceback.format_exc()
                logging.error("An exception occurred during analysis", exc_info=True)
            finally:
                end = datetime.now()
                summary.github_url = self.github_url
                for _, dictratio in summary_ratios.items():
                    dictratio.name = self.github_url
                return (
                    AnalysisResult(
                        github_url=self.github_url,
                        path=str(self.output_dir),
                        log_path=str(self.log_file()),
                        tool_use=tool_use,
                        ignored_tools=ignored_tools,
                        adoption_date=adoption_date,
                        last_file_change=last_file_change,
                        ratio=ratio,
                        adoption_ratio=adoption_ratio,
                        since_adoption_commits=adoption_commits,
                        recent_ratio=recent_ratio,
                        recent_commits=recent_commits,
                        commit_use=commit_use,
                        sampled=sampled,
                        duration=(end - start),
                        error=error,
                    ),
                    summary,
                    summary_ratios,
                )


if __name__ == "__main__":
    analyzer = RepoAnalyzer(
        exp_name="test_full",
        github_url="https://github.com/liam-hq/liam",
        base_dir="../temp",
    )
    _ = analyzer.do_analysis()

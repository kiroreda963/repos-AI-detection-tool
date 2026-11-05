"""
Per-repo analysis using a BLOBLESS partial clone (--filter=blob:none,
--no-checkout - full history metadata, no blob content upfront) instead of
the GitHub API for the parts that genuinely benefit from local history:
revert-target lookup, tool-file history detection for adoption_date, and
fix attribution - while still using the cheap GraphQL commit-history walk
(utils/commit_history.py, unmodified) for the bulk per-commit data, since
that's already free and a local `git log --numstat` equivalent would mean
computing a diff per changed file per commit across the whole history just
to get line counts the API already gives us for free.

Blobless rather than a full/bounded clone: this pipeline runs at thousands-
of-repos scale, where a full clone's bandwidth (downloading every blob of
every file, almost none of which any fix commit will ever touch) multiplies
badly. Blobless keeps bandwidth minimal. See the module docstring in
partial_clone_helpers.py for the two other approaches tried and measured
first (pure local blame on this same clone type; a full/shallow clone with
blobs), and why neither was the right fit.

Fix attribution: local diff finds which lines a fix commit touched (fast -
needs only two specific tree snapshots), then each touched file is routed
by a cheap local revision-count check: low-churn files (the common case)
get blamed locally and fast; the rare high-churn file falls back to the
existing single batched GraphQL blame call instead, so bandwidth and API
spend both stay minimal in aggregate. See find_fix_target_hybrid in
partial_clone_helpers.py for the exact threshold and reasoning.

Nothing here modifies run.py, repo_analyzer.py, lean_*.py, ai_adoption_*.py,
ai_detection.py, or any utils/*.py file - it only imports and reuses them.

Deliberately does NOT use `from __future__ import annotations`: that turns
every dataclass field's type into a plain string at runtime, which breaks
utils/import_export.py's type-based CSV round-trip (every non-string field
comes back from a resumed CSV as an unconverted string - the same bug
already caught once in ai_adoption_analyzer.py).
"""

import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from error_log import ErrorCollector, ErrorLogEntry
from fix_revert import classify_commit_label
from heuristics import commit_type_tool
from partial_clone_helpers import (
    cleanup_clone,
    clone_blobless,
    detect_file_and_gitignore_signals_local,
    find_fix_target_hybrid,
    get_tool_file_history_signal,
    resolve_commit_locally,
)
from utils.commit_history import ApiCommit, get_commit_history
from utils.data import Author, Commit
from utils.github import get_repo_name_from_url, parse_github_url

# Same cap/parallelism philosophy as the lean pipeline's blame step: bounds
# worst-case per-repo time regardless of how many fix commits it has, and
# overlaps this repo's own fix/revert resolutions instead of running them
# one at a time. The cap matters less now that attribution is local rather
# than API-bound, but still guards against a pathological number of fix
# commits turning into a very long per-repo runtime.
#
# Raised from 4 to 12: resolutions here are I/O-bound (local git calls, plus
# an occasional API call for high-churn files), not CPU-bound, so more of
# them can genuinely run at once. This is pure concurrency - it doesn't
# change which commit any fix/revert resolves to, only how many of those
# independent resolutions overlap in time. Measured driver of this change:
# a repo with 497 fix/revert commits took ~6.6 minutes at 4 workers.
BLAME_WORKERS_PER_REPO = 12
MAX_FIX_ATTRIBUTIONS_PER_REPO = 300

SECONDS_PER_DAY = 86400.0
DAY_BUCKETS = ("0-1", "1-3", "3-7", "7-30", "30+")


@dataclass
class CommitRecord:
    github_url: str
    sha: str
    date: datetime
    author_type: str  # Human | AI | Bot
    tool: str
    additions: int
    deletions: int
    changed_files: int | None
    is_fix_strict: bool
    is_fix_keyword: bool
    is_fix_issue_linked: bool
    is_revert: bool
    message: str


@dataclass
class FixRevertLink:
    github_url: str
    source_sha: str
    source_date: datetime
    source_type: str          # "fix" | "revert"
    source_author_type: str   # who DID the fix/revert - Human | AI | Bot
    target_sha: str
    target_date: datetime | None
    target_author_type: str   # whose commit got fixed/reverted - Human | AI | Bot | Unknown
    days_to_action: float | None
    day_bucket: str           # one of DAY_BUCKETS, or "unresolved"
    link_method: str          # "revert-trailer" | "blame" | "unresolved" | "skipped-budget"


@dataclass
class RepoSummary:
    github_url: str
    repo_name: str
    is_ai_adopter: bool
    ai_identification_methods: str  # file_manifest, gitignore, commits - comma-joined, or "none"
    ai_tools_used: str              # comma-joined tool names, or "none"
    adoption_date: datetime | None  # earliest of: first AI commit, first AI-tool file in history
    total_commits: int
    ai_commits: int
    human_commits: int
    bot_commits: int
    ai_additions: int
    human_additions: int
    bot_additions: int
    ai_loc_percentage: float | None     # ai_additions / (ai_additions + human_additions) * 100
    human_loc_percentage: float | None
    fix_revert_count: int
    fix_revert_target_ai: int       # how many fix/reverts targeted an AI-authored commit
    fix_revert_target_human: int
    fix_revert_target_bot: int
    fix_revert_target_unknown: int
    fix_revert_by_ai: int           # how many fix/revert commits were themselves AI-authored
    fix_revert_by_human: int
    fix_revert_by_bot: int
    error: str = ""


def _author_type(author: Author, message: str) -> tuple[str, str]:
    dummy = Commit(sha="", author=author, co_authors=[], message=message, date="")
    return commit_type_tool(dummy)


def _parse_date(d) -> datetime | None:
    if not d:
        return None
    if isinstance(d, datetime):
        return d
    try:
        return datetime.fromisoformat(str(d).replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_between(later: datetime | None, earlier: datetime | None) -> float | None:
    if not later or not earlier:
        return None
    return round((later - earlier).total_seconds() / SECONDS_PER_DAY, 3)


def _day_bucket(days: float | None) -> str:
    if days is None:
        return "unresolved"
    d = max(days, 0.0)  # defensive: clock skew shouldn't produce negative durations
    if d <= 1:
        return "0-1"
    if d <= 3:
        return "1-3"
    if d <= 7:
        return "3-7"
    if d <= 30:
        return "7-30"
    return "30+"


class PartialCloneAnalyzer:
    def __init__(self, github_url: str, analysis_period: str = "2025-01-01"):
        self.github_url = github_url
        self.analysis_period = analysis_period
        self.owner, self.repo = parse_github_url(github_url)

    def _resolve_revert_link(
        self, repo, commit: ApiCommit, commit_date, target_sha: str, by_sha: dict[str, ApiCommit],
        errors: ErrorCollector,
    ) -> FixRevertLink:
        source_type, _ = _author_type(commit.author, commit.message)
        target = by_sha.get(target_sha) or (
            resolve_commit_locally(repo, self.github_url, target_sha, errors) if target_sha else None
        )
        if target is None:
            return FixRevertLink(
                github_url=self.github_url, source_sha=commit.sha, source_date=commit_date,
                source_type="revert", source_author_type=source_type, target_sha=target_sha, target_date=None,
                target_author_type="Unknown", days_to_action=None, day_bucket="unresolved", link_method="unresolved",
            )
        target_type, _ = _author_type(target.author, target.message)
        target_date = _parse_date(target.date)
        days = _days_between(commit_date, target_date)
        return FixRevertLink(
            github_url=self.github_url, source_sha=commit.sha, source_date=commit_date,
            source_type="revert", source_author_type=source_type, target_sha=target_sha, target_date=target_date,
            target_author_type=target_type, days_to_action=days, day_bucket=_day_bucket(days),
            link_method="revert-trailer",
        )

    def _resolve_fix_link(self, repo, commit: ApiCommit, commit_date, errors: ErrorCollector) -> FixRevertLink:
        source_type, _ = _author_type(commit.author, commit.message)
        target = find_fix_target_hybrid(repo, self.owner, self.repo, commit.sha, commit.parents[0], errors)
        if target is None:
            return FixRevertLink(
                github_url=self.github_url, source_sha=commit.sha, source_date=commit_date,
                source_type="fix", source_author_type=source_type, target_sha="", target_date=None,
                target_author_type="Unknown", days_to_action=None, day_bucket="unresolved", link_method="unresolved",
            )
        target_type, _ = _author_type(target.target_author, target.target_message)
        target_date = _parse_date(target.target_date)
        days = _days_between(commit_date, target_date)
        return FixRevertLink(
            github_url=self.github_url, source_sha=commit.sha, source_date=commit_date,
            source_type="fix", source_author_type=source_type, target_sha=target.target_sha, target_date=target_date,
            target_author_type=target_type, days_to_action=days, day_bucket=_day_bucket(days),
            link_method="blame",
        )

    def analyze(self) -> tuple[RepoSummary, list[CommitRecord], list[FixRevertLink], list[ErrorLogEntry]]:
        classified: list[CommitRecord] = []
        links: list[FixRevertLink] = []
        counts = {
            "ai": 0, "human": 0, "bot": 0,
            "ai_add": 0, "human_add": 0, "bot_add": 0,
            "target_ai": 0, "target_human": 0, "target_bot": 0, "target_unknown": 0,
            "by_ai": 0, "by_human": 0, "by_bot": 0,
        }
        repo_name = get_repo_name_from_url(self.github_url) or self.github_url
        clone_path: Path | None = None
        error_collector = ErrorCollector(self.github_url)

        try:
            if not self.owner or not self.repo:
                raise RuntimeError(f"Invalid GitHub URL: {self.github_url}")

            print(f"\n[REPO >] {self.github_url}")

            repo, clone_path = clone_blobless(self.github_url)

            signals = detect_file_and_gitignore_signals_local(repo, error_collector)
            file_history = get_tool_file_history_signal(repo, error_collector)

            commits = get_commit_history(self.github_url, self.analysis_period)
            by_sha = {c.sha: c for c in commits}

            revert_jobs: list[tuple[ApiCommit, datetime | None, str]] = []
            fix_jobs: list[tuple[ApiCommit, datetime | None]] = []
            earliest_ai_commit_date: datetime | None = None

            for commit in commits:
                author_type, tool = _author_type(commit.author, commit.message)
                label = classify_commit_label(commit.message)
                commit_date = _parse_date(commit.date)

                classified.append(CommitRecord(
                    github_url=self.github_url, sha=commit.sha, date=commit_date,
                    author_type=author_type, tool=tool,
                    additions=commit.additions, deletions=commit.deletions, changed_files=commit.changed_files,
                    is_fix_strict=label.is_fix_strict, is_fix_keyword=label.is_fix_keyword,
                    is_fix_issue_linked=label.is_fix_issue_linked, is_revert=label.is_revert,
                    message=commit.message,
                ))

                if author_type == "AI":
                    counts["ai"] += 1
                    counts["ai_add"] += commit.additions
                    if earliest_ai_commit_date is None or (commit_date and commit_date < earliest_ai_commit_date):
                        earliest_ai_commit_date = commit_date
                elif author_type == "Bot":
                    counts["bot"] += 1
                    counts["bot_add"] += commit.additions
                else:
                    counts["human"] += 1
                    counts["human_add"] += commit.additions

                if label.is_revert:
                    revert_jobs.append((commit, commit_date, label.revert_target_sha))

                if label.is_fix:
                    if commit.parents and len(fix_jobs) < MAX_FIX_ATTRIBUTIONS_PER_REPO:
                        fix_jobs.append((commit, commit_date))
                    else:
                        src_type, _ = _author_type(commit.author, commit.message)
                        links.append(FixRevertLink(
                            github_url=self.github_url, source_sha=commit.sha, source_date=commit_date,
                            source_type="fix", source_author_type=src_type, target_sha="", target_date=None,
                            target_author_type="Unknown", days_to_action=None, day_bucket="unresolved",
                            link_method="unresolved" if not commit.parents else "skipped-budget",
                        ))

            with ThreadPoolExecutor(max_workers=BLAME_WORKERS_PER_REPO) as pool:
                revert_futures = [
                    pool.submit(self._resolve_revert_link, repo, commit, commit_date, target_sha, by_sha, error_collector)
                    for commit, commit_date, target_sha in revert_jobs
                ]
                fix_futures = [
                    pool.submit(self._resolve_fix_link, repo, commit, commit_date, error_collector)
                    for commit, commit_date in fix_jobs
                ]
                for future in revert_futures + fix_futures:
                    links.append(future.result())

            for link in links:
                if link.target_author_type == "AI":
                    counts["target_ai"] += 1
                elif link.target_author_type == "Human":
                    counts["target_human"] += 1
                elif link.target_author_type == "Bot":
                    counts["target_bot"] += 1
                else:
                    counts["target_unknown"] += 1

                if link.source_author_type == "AI":
                    counts["by_ai"] += 1
                elif link.source_author_type == "Bot":
                    counts["by_bot"] += 1
                else:
                    counts["by_human"] += 1

            adoption_candidates = [d for d in (earliest_ai_commit_date, file_history.earliest_date) if d]
            adoption_date = min(adoption_candidates) if adoption_candidates else None

            identified_via_commits = counts["ai"] > 0
            methods = []
            if signals.file_manifest_hit:
                methods.append("file_manifest")
            if signals.gitignore_hit:
                methods.append("gitignore")
            if identified_via_commits:
                methods.append("commits")

            commit_tools = {c.tool for c in classified if c.author_type == "AI" and c.tool}
            all_tools = commit_tools | signals.file_manifest_tools | signals.gitignore_tools | file_history.tools

            ai_human_total = counts["ai_add"] + counts["human_add"]
            ai_pct = round(100 * counts["ai_add"] / ai_human_total, 2) if ai_human_total else None
            human_pct = round(100 - ai_pct, 2) if ai_pct is not None else None

            summary = RepoSummary(
                github_url=self.github_url, repo_name=repo_name,
                is_ai_adopter=bool(methods),
                ai_identification_methods=",".join(methods) if methods else "none",
                ai_tools_used=",".join(sorted(all_tools)) if all_tools else "none",
                adoption_date=adoption_date,
                total_commits=len(commits), ai_commits=counts["ai"], human_commits=counts["human"],
                bot_commits=counts["bot"],
                ai_additions=counts["ai_add"], human_additions=counts["human_add"], bot_additions=counts["bot_add"],
                ai_loc_percentage=ai_pct, human_loc_percentage=human_pct,
                fix_revert_count=len(links),
                fix_revert_target_ai=counts["target_ai"], fix_revert_target_human=counts["target_human"],
                fix_revert_target_bot=counts["target_bot"], fix_revert_target_unknown=counts["target_unknown"],
                fix_revert_by_ai=counts["by_ai"], fix_revert_by_human=counts["by_human"],
                fix_revert_by_bot=counts["by_bot"],
            )
            print(f"\n[REPO <] {self.github_url} -> {summary.total_commits} commits, "
                  f"ai_adopter={summary.is_ai_adopter}, fix_revert={summary.fix_revert_count}, "
                  f"adoption_date={summary.adoption_date}")
            return summary, classified, links, error_collector.entries

        except Exception:
            err = traceback.format_exc()
            print(f"\n[REPO x] {self.github_url} -> failed: {err.splitlines()[-1]}")
            error_collector.record("repo_analysis", "", err)
            summary = RepoSummary(
                github_url=self.github_url, repo_name=repo_name, is_ai_adopter=False,
                ai_identification_methods="none", ai_tools_used="none", adoption_date=None,
                total_commits=0, ai_commits=0, human_commits=0, bot_commits=0,
                ai_additions=0, human_additions=0, bot_additions=0,
                ai_loc_percentage=None, human_loc_percentage=None,
                fix_revert_count=0, fix_revert_target_ai=0, fix_revert_target_human=0,
                fix_revert_target_bot=0, fix_revert_target_unknown=0,
                fix_revert_by_ai=0, fix_revert_by_human=0, fix_revert_by_bot=0,
                error=err,
            )
            return summary, classified, links, error_collector.entries

        finally:
            if clone_path is not None:
                cleanup_clone(clone_path)


if __name__ == "__main__":
    analyzer = PartialCloneAnalyzer("https://github.com/liam-hq/liam", analysis_period="2025-01-01")
    summary, commits, links, errors = analyzer.analyze()
    print(summary)
    print(f"{len(commits)} commits, {len(links)} fix/revert links, {len(errors)} logged errors")

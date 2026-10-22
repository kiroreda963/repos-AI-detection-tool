"""
No-clone, API-only analysis: for one repo, fetch its commit history over the
GraphQL API, classify every commit as Human/AI/Bot, flag fix/revert commits,
and attribute each fix/revert to the commit it targets plus the time elapsed.

Nothing is cloned and nothing is written to a per-repo working directory -
"identify the repo as-is": nothing on disk gets reorganized, no clones/
directory, no classification-folder move. Output is a handful of CSV rows.
"""
from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from find_tools import find_repo_tools, get_file_list
from fix_revert import classify_commit_label
from heuristics import commit_type_tool
from utils.blame_attribution import find_fix_target
from utils.commit_history import ApiCommit, get_commit_history, get_single_commit
from utils.data import Author, Commit
from utils.github import GITHUB_API, parse_github_url
from utils import logging

# Blame is GitHub's slowest GraphQL field (computed on demand), and a repo
# can have hundreds of "fix:" commits. Both knobs below exist to keep one
# repo's tail latency and API spend bounded regardless of its commit history:
BLAME_WORKERS_PER_REPO = 4       # overlap a repo's own fix-attribution calls
MAX_FIX_ATTRIBUTIONS_PER_REPO = 300  # beyond this, mark as skipped rather than call blame

SECONDS_PER_DAY = 86400.0


@dataclass
class ClassifiedCommit:
    github_url: str
    sha: str
    date: datetime
    author_type: str  # Human | AI | Bot
    tool: str
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
    source_type: str  # "fix" | "revert"
    target_sha: str
    target_date: datetime | None
    target_author_type: str  # Human | AI | Bot | Unknown
    days_to_action: float | None
    link_method: str  # "revert-trailer" | "blame" | "unresolved" | "skipped-budget"


@dataclass
class RepoCommitSummary:
    github_url: str
    total_commits: int
    ai_commits: int
    human_commits: int
    bot_commits: int
    fix_commits: int
    revert_commits: int
    fix_links_resolved: int
    revert_links_resolved: int
    # How the repo was recognized as an AI-tool adopter at all - independent
    # signals, same categories the clone-based pipeline uses:
    identified_via_file_manifest: bool  # AI-tool config files tracked in the repo (e.g. .cursor/, CLAUDE.md)
    identified_via_gitignore: bool      # those same config files only referenced in .gitignore, never committed
    identified_via_commits: bool        # at least one commit authored/co-authored by a recognized AI tool
    ai_identification_methods: str      # comma-joined summary of the above, "none" if no signal fired
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


class LeanAnalyzer:
    def __init__(self, github_url: str, analysis_period: str = "2025-01-01"):
        self.github_url = github_url
        self.analysis_period = analysis_period
        self.owner, self.repo = parse_github_url(github_url)

    def _detect_file_and_gitignore_signals(self) -> tuple[bool, bool]:
        """Checks for AI-tool marker files (e.g. .cursor/, CLAUDE.md) two ways,
        both clone-free: in the repo's real file tree, and in .gitignore (tool
        used but its files were never committed). Mirrors the clone-based
        pipeline's tool_use / ignored_tools signals."""
        file_manifest_hit = False
        try:
            file_list = get_file_list(self.github_url)
            file_manifest_hit = len(find_repo_tools(file_list)) > 0
        except Exception as e:
            logging.warning(f"Could not fetch file tree for {self.github_url}: {e}")

        gitignore_hit = False
        try:
            gitignore_content = GITHUB_API.get_file_content(self.owner, self.repo, ".gitignore")
            if isinstance(gitignore_content, str):
                gitignore_hit = len(find_repo_tools(gitignore_content.splitlines())) > 0
        except Exception:
            pass  # no .gitignore, or it couldn't be fetched - not an adoption signal either way

        return file_manifest_hit, gitignore_hit

    def _resolve_revert_link(self, commit: ApiCommit, commit_date, target_sha: str, by_sha) -> FixRevertLink:
        """Looks up the revert's target: from the already-fetched history
        window if possible, else one extra API call."""
        target = by_sha.get(target_sha) or (get_single_commit(self.github_url, target_sha) if target_sha else None)
        if target is None:
            return FixRevertLink(
                github_url=self.github_url, source_sha=commit.sha, source_date=commit_date,
                source_type="revert", target_sha=target_sha, target_date=None,
                target_author_type="Unknown", days_to_action=None, link_method="unresolved",
            )
        target_type, _ = _author_type(target.author, target.message)
        target_date = _parse_date(target.date)
        return FixRevertLink(
            github_url=self.github_url, source_sha=commit.sha, source_date=commit_date,
            source_type="revert", target_sha=target_sha, target_date=target_date,
            target_author_type=target_type, days_to_action=_days_between(commit_date, target_date),
            link_method="revert-trailer",
        )

    def _resolve_fix_link(self, commit: ApiCommit, commit_date) -> FixRevertLink:
        target = find_fix_target(self.owner, self.repo, commit.sha, commit.parents[0])
        if target is None:
            return FixRevertLink(
                github_url=self.github_url, source_sha=commit.sha, source_date=commit_date,
                source_type="fix", target_sha="", target_date=None,
                target_author_type="Unknown", days_to_action=None, link_method="unresolved",
            )
        target_type, _ = _author_type(target.target_author, target.target_message)
        target_date = _parse_date(target.target_date)
        return FixRevertLink(
            github_url=self.github_url, source_sha=commit.sha, source_date=commit_date,
            source_type="fix", target_sha=target.target_sha, target_date=target_date,
            target_author_type=target_type, days_to_action=_days_between(commit_date, target_date),
            link_method="blame",
        )

    def analyze(self) -> tuple[RepoCommitSummary, list[ClassifiedCommit], list[FixRevertLink]]:
        classified: list[ClassifiedCommit] = []
        links: list[FixRevertLink] = []
        counts = {"ai": 0, "human": 0, "bot": 0, "fix": 0, "revert": 0, "fix_resolved": 0, "revert_resolved": 0}

        try:
            if not self.owner or not self.repo:
                raise RuntimeError(f"Invalid GitHub URL: {self.github_url}")

            print(f"\n[REPO >] {self.github_url}")

            file_manifest_hit, gitignore_hit = self._detect_file_and_gitignore_signals()

            commits = get_commit_history(self.github_url, self.analysis_period)
            by_sha = {c.sha: c for c in commits}

            # Pass 1: pure classification, no network - cheap and instant.
            revert_jobs: list[tuple[ApiCommit, datetime | None, str]] = []
            fix_jobs: list[tuple[ApiCommit, datetime | None]] = []

            for commit in commits:
                author_type, tool = _author_type(commit.author, commit.message)
                label = classify_commit_label(commit.message)
                commit_date = _parse_date(commit.date)

                classified.append(ClassifiedCommit(
                    github_url=self.github_url, sha=commit.sha, date=commit_date,
                    author_type=author_type, tool=tool,
                    is_fix_strict=label.is_fix_strict, is_fix_keyword=label.is_fix_keyword,
                    is_fix_issue_linked=label.is_fix_issue_linked, is_revert=label.is_revert,
                    message=commit.message,
                ))

                if author_type == "AI":
                    counts["ai"] += 1
                elif author_type == "Bot":
                    counts["bot"] += 1
                else:
                    counts["human"] += 1

                if label.is_revert:
                    counts["revert"] += 1
                    revert_jobs.append((commit, commit_date, label.revert_target_sha))

                if label.is_fix:
                    counts["fix"] += 1
                    if commit.parents and len(fix_jobs) < MAX_FIX_ATTRIBUTIONS_PER_REPO:
                        fix_jobs.append((commit, commit_date))
                    else:
                        links.append(FixRevertLink(
                            github_url=self.github_url, source_sha=commit.sha, source_date=commit_date,
                            source_type="fix", target_sha="", target_date=None,
                            target_author_type="Unknown", days_to_action=None,
                            link_method="unresolved" if not commit.parents else "skipped-budget",
                        ))

            # Pass 2: the network-bound work, overlapped so one repo with many
            # fix commits doesn't serialize its latency onto the whole run.
            with ThreadPoolExecutor(max_workers=BLAME_WORKERS_PER_REPO) as pool:
                revert_futures = [
                    pool.submit(self._resolve_revert_link, commit, commit_date, target_sha, by_sha)
                    for commit, commit_date, target_sha in revert_jobs
                ]
                fix_futures = [
                    pool.submit(self._resolve_fix_link, commit, commit_date)
                    for commit, commit_date in fix_jobs
                ]

                for future in revert_futures:
                    link = future.result()
                    links.append(link)
                    if link.link_method != "unresolved":
                        counts["revert_resolved"] += 1

                for future in fix_futures:
                    link = future.result()
                    links.append(link)
                    if link.link_method != "unresolved":
                        counts["fix_resolved"] += 1

            identified_via_commits = counts["ai"] > 0
            methods = []
            if file_manifest_hit:
                methods.append("file_manifest")
            if gitignore_hit:
                methods.append("gitignore")
            if identified_via_commits:
                methods.append("commits")

            summary = RepoCommitSummary(
                github_url=self.github_url, total_commits=len(commits),
                ai_commits=counts["ai"], human_commits=counts["human"], bot_commits=counts["bot"],
                fix_commits=counts["fix"], revert_commits=counts["revert"],
                fix_links_resolved=counts["fix_resolved"], revert_links_resolved=counts["revert_resolved"],
                identified_via_file_manifest=file_manifest_hit, identified_via_gitignore=gitignore_hit,
                identified_via_commits=identified_via_commits,
                ai_identification_methods=",".join(methods) if methods else "none",
            )
            print(f"\n[REPO <] {self.github_url} -> {len(commits)} commits, "
                  f"{counts['fix']} fix, {counts['revert']} revert, methods={summary.ai_identification_methods}")
            return summary, classified, links

        except Exception:
            err = traceback.format_exc()
            print(f"\n[REPO x] {self.github_url} -> failed: {err.splitlines()[-1]}")
            summary = RepoCommitSummary(
                github_url=self.github_url, total_commits=0, ai_commits=0, human_commits=0,
                bot_commits=0, fix_commits=0, revert_commits=0, fix_links_resolved=0,
                revert_links_resolved=0, identified_via_file_manifest=False,
                identified_via_gitignore=False, identified_via_commits=False,
                ai_identification_methods="none", error=err,
            )
            return summary, classified, links


if __name__ == "__main__":
    analyzer = LeanAnalyzer("https://github.com/liam-hq/liam", analysis_period="2025-01-01")
    summary, commits, links = analyzer.analyze()
    print(summary)
    print(f"{len(commits)} commits, {len(links)} fix/revert links")

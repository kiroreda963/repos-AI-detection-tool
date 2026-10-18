from __future__ import annotations
from collections import Counter
from utils.data import LogCommit, PullRequest
from dataclasses import dataclass
from typing import Literal

MergeType = Literal["unmerged", "merge", "squash", "rebase", "partial-rebase", "merge (fast-forward)", "unknown"]


@dataclass
class ClassifiedPR:
    pr: PullRequest
    merge_type: MergeType
    commit_mapping: dict[str, str]
    confidence: float

# classify_pr_merge with confidence
# we should add a category for the "unmerged" PRs maybe???
# probably ....
# are they bundled in "unknown" at the moment?
def classify_pr_merge(
    pr: PullRequest,
    main_commits: dict[str, LogCommit]) -> ClassifiedPR:
    """
    Classify how a PullRequest was merged into main and produce:
      - merge_type: {"merge", "rebase", "squash", "unknown"}
      - mapping: dict of pr_commit_sha -> main_commit_sha
      - confidence: float (0.0–1.0)
    """
    pr_commit_shas = [c.sha for c in pr.commits]
    pr_commit_count = len(pr_commit_shas)

    mapping: dict[str, str] = {}
    if not pr.merged_at:
        return ClassifiedPR(pr, "unmerged", mapping, 1.0)

    # === CASE A: Merge commit (>=2 parents) ===
    if pr.merge_commit_sha and pr.merge_commit_sha in main_commits:
        merge_commit = main_commits[pr.merge_commit_sha]
        if len(merge_commit.parents) >= 2:
            # identity mapping if possible
            for c in pr.commits:
                if c.sha in main_commits:
                    mapping[c.sha] = c.sha
            return ClassifiedPR(pr, "merge", mapping, 1.0)

    # === CASE B: Squash (merge_commit_sha exists, PR SHAs absent) ===
    if pr.merge_commit_sha and pr.merge_commit_sha in main_commits:
        if not any(sha in main_commits for sha in pr_commit_shas):
            squash_sha = pr.merge_commit_sha
            return ClassifiedPR(pr, "squash", {sha: squash_sha for sha in pr_commit_shas}, 1.0)

    # === CASE C: Rebase (map all commits uniquely, none of the SHAs match) ===
    # Simple heuristic: same number of commits, all distinct, no direct SHA matches
    if pr_commit_count > 0:
        candidates = [
            mc for mc in main_commits.values()
            if mc.message.strip() in {c.message.strip() for c in pr.commits}
            and mc.author.email in {c.author.email for c in pr.commits}
        ]
        if len(candidates) >= pr_commit_count:
            # Map by message+author
            mapping = {}
            for pr_commit in pr.commits:
                for mc in main_commits.values():
                    if (mc.message.strip() == pr_commit.message.strip() and
                        mc.author.email == pr_commit.author.email):
                        mapping[pr_commit.sha] = mc.sha
                        break
            if len(mapping) == pr_commit_count:
                return ClassifiedPR(pr,"rebase", mapping, 0.9)
            if 0 < len(mapping) < pr_commit_count:
                return ClassifiedPR(pr, "partial-rebase", mapping, 0.6)

    # === CASE D: All PR SHAs present on main (fast-forward merge) ===
    if all(sha in main_commits for sha in pr_commit_shas):
        return ClassifiedPR(pr, "merge (fast-forward)", {sha: sha for sha in pr_commit_shas}, 0.9)

    # === Fallback: unknown ===
    return ClassifiedPR(pr, "unknown", mapping, 0.2)

@dataclass
class PullRequestStats:
    total_prs: int
    counts: dict[MergeType,int]
    percent:dict[MergeType,float]
    large_pr_threshold: int
    total_large_prs: int
    large_counts: dict[MergeType, int]
    github_url: str=""

def empty_pr_stats(): return PullRequestStats(-1,{},{},-1,-1,{})

def analyze_pr_merges(
    pull_requests: list[PullRequest],
    commits: list[LogCommit],
    large_pr_threshold: int = 5
) -> tuple[list[ClassifiedPR], PullRequestStats]:
    """
    Analyze all pull requests and return:
      - pr_report: dict pr.number -> {"merge_type", "mapping", "confidence", "n_commits"}
      - summary: statistics including % squash merges among large PRs
    """
    pr_report: list[ClassifiedPR] = []
    counts: Counter[MergeType] = Counter()
    large_counts: Counter[MergeType] = Counter()
    total_large = 0
    main_commits = {commit.sha.strip(): commit for commit in commits}

    for pr in pull_requests:
        classif = classify_pr_merge(pr, main_commits)
        mtype = classif.merge_type
        pr_report.append(classif)
        counts[mtype] += 1
        if len(pr.commits) >= large_pr_threshold:
            total_large += 1
            large_counts[mtype] += 1

    total = len(pull_requests)
    def _pct(cnt: int): return (cnt / total * 100.0) if total else 0.0

    summary = PullRequestStats(
        total_prs = total,
        counts = dict(counts),
        percent = {k: _pct(v) for k, v in counts.items()},
        large_pr_threshold=large_pr_threshold,
        total_large_prs=total_large,
        large_counts=dict(large_counts)
    )
    return pr_report, summary

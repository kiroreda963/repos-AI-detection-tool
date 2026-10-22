"""
Attributes a "fix" commit to the commit that last touched the lines it
changes - the one piece that has no structural marker (unlike "revert",
which carries its target sha in the message).

Cost model: one REST call for the fix commit's patch, then ONE batched
GraphQL call (all touched files aliased together) for blame at the fix's
parent commit. Only commits already flagged as "fix" pay this cost.
"""
import re
from dataclasses import dataclass

from utils import logging
from utils.data import Author
from utils.github import GITHUB_API

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")

MAX_FILES_PER_FIX = 6  # cap blame calls on fix commits that touch many files


@dataclass
class BlameTarget:
    target_sha: str
    target_date: str
    target_author: Author
    target_message: str
    overlap_lines: int


def _old_line_ranges_from_patch(patch: str) -> list[tuple[int, int]]:
    """Returns the [start,end] line ranges in the OLD (pre-fix) file version
    that this patch touches - i.e. lines it deleted or modified. Pure
    insertions (old_count == 0) are skipped: there's nothing prior to blame."""
    ranges = []
    for line in patch.splitlines():
        m = HUNK_HEADER.match(line)
        if not m:
            continue
        old_start = int(m.group(1))
        old_count = int(m.group(2)) if m.group(2) is not None else 1
        if old_count > 0:
            ranges.append((old_start, old_start + old_count - 1))
    return ranges


def _ranges_overlap(a: tuple[int, int], b_start: int, b_end: int) -> int:
    lo = max(a[0], b_start)
    hi = min(a[1], b_end)
    return max(0, hi - lo + 1)


def find_fix_target(owner: str, repo: str, fix_sha: str, parent_sha: str) -> BlameTarget | None:
    """
    1. Fetch the fix commit's patch (REST) to find which old-file lines it touches.
    2. Blame those files at the fix's parent commit (one batched GraphQL call).
    3. Return the commit with the largest overlap with the touched lines.
    """
    try:
        commit_data = GITHUB_API.get_commit(owner, repo, fix_sha)
    except Exception as e:
        logging.error(f"Could not fetch patch for fix commit {fix_sha}: {e}")
        return None

    files = commit_data.get("files", []) or []
    touched_ranges: dict[str, list[tuple[int, int]]] = {}
    for f in files[:MAX_FILES_PER_FIX]:
        patch = f.get("patch")
        if not patch:
            continue  # binary file, or too large for GitHub to diff
        ranges = _old_line_ranges_from_patch(patch)
        if ranges:
            touched_ranges[f["filename"]] = ranges

    if not touched_ranges:
        return None

    try:
        blame_by_path = GITHUB_API.get_blame_batch(owner, repo, parent_sha, list(touched_ranges.keys()))
    except Exception as e:
        logging.error(f"Blame lookup failed for {fix_sha} at parent {parent_sha}: {e}")
        return None

    overlap_by_commit: dict[str, int] = {}
    info_by_commit: dict[str, dict] = {}

    for path, ranges in touched_ranges.items():
        for blame_range in blame_by_path.get(path, []):
            b_start, b_end = blame_range["startingLine"], blame_range["endingLine"]
            for r in ranges:
                overlap = _ranges_overlap(r, b_start, b_end)
                if overlap <= 0:
                    continue
                c = blame_range["commit"]
                oid = c["oid"]
                overlap_by_commit[oid] = overlap_by_commit.get(oid, 0) + overlap
                info_by_commit[oid] = c

    if not overlap_by_commit:
        return None

    best_oid = max(overlap_by_commit, key=lambda k: overlap_by_commit[k])
    best = info_by_commit[best_oid]
    author = best.get("author") or {}
    user = author.get("user") if author else None

    return BlameTarget(
        target_sha=best_oid,
        target_date=best.get("committedDate", ""),
        target_author=Author(
            login=(user.get("login") if user else "") or "",
            name=author.get("name") or "",
            email=author.get("email") or "",
        ),
        target_message=best.get("message") or "",
        overlap_lines=overlap_by_commit[best_oid],
    )

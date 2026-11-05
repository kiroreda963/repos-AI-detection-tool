"""
Local-git equivalents of the API-based helpers used by the lean/ai_adoption
pipelines, built on top of a BLOBLESS partial clone (--filter=blob:none,
--no-checkout, full history metadata, no blob content upfront).

Three approaches were tried and measured against a real repo before this
one, in order:

1. Blobless, pure local blame. Fetches the minimum possible data (commit/
   tree metadata only; blob content is fetched lazily, one object at a
   time, only for what's actually read). Bandwidth-optimal, but blame has
   to walk a file's WHOLE revision history to attribute every line, and
   each not-yet-local historical version is a SEPARATE round trip - a file
   with 358 revisions did not finish blaming within 25s. This is a latency
   problem, not a bandwidth one: the data fetched is still minimal, it's
   just fetched in many slow sequential pieces instead of one request.
2. Full clone (all blobs, no bound). Fixed the latency problem completely
   (0.0s on the same file, since every blob is already on disk) - but at
   the scale this pipeline runs at (thousands of repos), downloading every
   blob of every file - almost all of which no fix commit will ever touch -
   is pure bandwidth waste multiplied thousands of times over.
3. This approach: stay blobless (bandwidth-optimal, like #1), but before
   blaming a file, cheaply check its revision count first (git rev-list
   --count - pure metadata, free). Low revision count (the common case,
   especially since every repo here is young) -> local blame, fast and
   minimal-bandwidth, same as #1. High revision count (the rare case that
   broke #1) -> skip local blame for just that file and use the existing
   single batched GraphQL blame call instead. Bandwidth stays minimal for
   the common case; the API is only spent on the specific files that would
   otherwise be slow.

Reuses utils/cloning.py, utils/repository.py, utils/blame_attribution.py,
utils/commit_history.py, utils/github.py, heuristics.py and ai_detection.py
exactly as they are - nothing in this file modifies any of them.

Deliberately does NOT use `from __future__ import annotations` - see the
note in partial_analyzer.py for why (breaks CSV round-tripping of any
dataclass whose fields aren't all strings).
"""

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ai_detection import AdoptionSignals
from error_log import ErrorCollector
from find_tools import find_repo_tools
from heuristics import path_matches
from utils import cloning, logging
from utils.blame_attribution import BlameTarget, _old_line_ranges_from_patch, _ranges_overlap
from utils.commit_history import ApiCommit, get_single_commit
from utils.data import Author
from utils.github import GITHUB_API, get_repo_name_from_url
from utils.repository import GitRepository

MAX_FILES_PER_FIX = 6  # same cap as the API-based blame_attribution.py, for the same reason

# Measured: a file with 358 revisions did not finish local blame within 25s
# on a blobless clone (each missing historical version is a separate lazy
# fetch). This threshold is set conservatively low so the common case (a
# young repo's typical file) stays local, while outliers route to the API
# blame call instead of risking that per-file sequential-fetch latency.
MAX_LOCAL_BLAME_REVISIONS = 20


# ---------- clone lifecycle ----------

def clone_blobless(github_url: str) -> tuple[GitRepository, Path]:
    """Clones into a fresh temp dir: --filter=blob:none --no-checkout, full
    history metadata, no blob content upfront. Caller must call
    cleanup_clone() when done with this repo."""
    repo_name = get_repo_name_from_url(github_url) or "repo"
    dest = Path(tempfile.mkdtemp(prefix=f"partial_{repo_name}_"))
    # clone_repo treats an already-existing dest as "already cloned" and
    # no-ops, so make sure it starts genuinely empty.
    dest.rmdir()
    success = cloning.clone_repo(github_url, dest, partial=True, no_checkout=True)
    if not success:
        raise RuntimeError(f"partial clone failed for {github_url}")
    return GitRepository(dest), dest


def cleanup_clone(path: Path):
    if path.exists():
        shutil.rmtree(path, onexc=cloning._clear_readonly)


# ---------- file list / .gitignore, read locally instead of via API ----------

def local_file_list(repo: GitRepository) -> list[str]:
    """repo.get_file_list() has a pre-existing bug (indexes into the stdout
    string instead of splitting it), so this is a correct equivalent rather
    than a fix to that method."""
    output = repo.run_command(["ls-tree", "-r", "--name-only", "HEAD"])
    return output.splitlines() if output else []


def local_gitignore_content(repo: GitRepository) -> str | None:
    try:
        repo.populate_index()
        content = repo.get_file_content_from_blob(".gitignore")
        return content if content else None
    except Exception:
        return None  # no .gitignore, or this repo has no commits at all


def detect_file_and_gitignore_signals_local(repo: GitRepository, errors: ErrorCollector | None = None) -> AdoptionSignals:
    file_manifest_tools: set[str] = set()
    try:
        file_manifest_tools = {tr.tool for tr in find_repo_tools(local_file_list(repo))}
    except Exception as e:
        logging.warning(f"Could not list files locally: {e}")
        if errors:
            errors.record("file_manifest", "", e)

    gitignore_tools: set[str] = set()
    gitignore_content = local_gitignore_content(repo)
    if gitignore_content:
        gitignore_tools = {tr.tool for tr in find_repo_tools(gitignore_content.splitlines())}

    return AdoptionSignals(
        file_manifest_hit=len(file_manifest_tools) > 0,
        gitignore_hit=len(gitignore_tools) > 0,
        file_manifest_tools=file_manifest_tools,
        gitignore_tools=gitignore_tools,
    )


# ---------- tool-file history signal (for adoption_date) ----------
#
# Full, unbounded history (the blobless clone has no date limit), so this
# catches a tool file that was added and later removed at ANY point in the
# repo's life - which the API-based current-tree-only check (ai_detection.py)
# cannot see, since it only looks at what's in the tree right now.

@dataclass
class ToolFileHistorySignal:
    earliest_date: datetime | None
    tools: set[str] = field(default_factory=set)


def _walk_full_file_history(repo: GitRepository) -> dict[str, int]:
    """Returns {path: earliest unix timestamp it was touched} within the
    clone's (shallow) history - git log --name-status, no blob content
    needed for this walk regardless of clone type.

    This duplicates GitRepository.get_file_history_metrics()'s git-log
    approach rather than calling it: that method's whole-repo branch has a
    pre-existing bug - it seeds an empty dict and then only ever UPDATES
    keys already present in it, so it can never discover a new path and
    always returns {} when called without an explicit file_paths list."""
    log_output = repo.run_command(
        ["log", "--all", '--pretty=format:"%H %ct"', "--name-status", "--no-merges", "--no-renames"]
    )
    earliest: dict[str, int] = {}
    current_ts: int | None = None
    for line in log_output.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('"') and line.endswith('"'):
            parts = line.strip('"').split(" ")
            current_ts = int(parts[1]) if len(parts) == 2 else None
            continue
        if current_ts is None:
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2:
            _status, file_path = parts
            if file_path not in earliest or current_ts < earliest[file_path]:
                earliest[file_path] = current_ts
    return earliest


def get_tool_file_history_signal(repo: GitRepository, errors: ErrorCollector | None = None) -> ToolFileHistorySignal:
    try:
        earliest_by_path = _walk_full_file_history(repo)
    except Exception as e:
        logging.warning(f"File-history walk failed: {e}")
        if errors:
            errors.record("tool_file_history", "", e)
        return ToolFileHistorySignal(earliest_date=None)

    earliest_ts: int | None = None
    tools: set[str] = set()
    for path, ts in earliest_by_path.items():
        tool = path_matches(path)
        if not tool:
            continue
        tools.add(tool)
        if earliest_ts is None or ts < earliest_ts:
            earliest_ts = ts

    earliest_date = datetime.fromtimestamp(earliest_ts, tz=timezone.utc) if earliest_ts is not None else None
    return ToolFileHistorySignal(earliest_date=earliest_date, tools=tools)


# ---------- resolve a single commit, locally first, API as fallback ----------

def resolve_commit_locally(
    repo: GitRepository, github_url: str, sha: str, errors: ErrorCollector | None = None
) -> ApiCommit | None:
    """Looks up a commit's metadata. A blobless clone fetches ALL commit
    objects regardless of date, so this is free/local for any sha that
    exists in this repo's history - the GraphQL fallback
    (utils.commit_history.get_single_commit, unmodified) only matters for
    genuinely exotic cases (e.g. a sha from an unreachable fork)."""
    try:
        out = repo.run_command(
            ["show", sha, "--no-patch", "--format=%H%x00%an%x00%ae%x00%aI%x00%P%x00%B%x00"],
            check=True,
        )
        if out:
            parts = out.split("\x00")
            if len(parts) >= 6:
                sha_, name, email, date_iso, parents_str, message = parts[:6]
                parents = parents_str.split() if parents_str.strip() else []
                return ApiCommit(
                    sha=sha_, author=Author(login="", name=name, email=email), co_authors=[],
                    message=message.strip("\n"), date=date_iso, parents=parents,
                    additions=0, deletions=0, changed_files=None,
                )
    except Exception:
        pass

    target = get_single_commit(github_url, sha)
    if target is None and errors:
        errors.record("resolve_commit", sha, "not found locally or via the GraphQL single-commit fallback")
    return target


# ---------- fix attribution: local diff + revision-gated blame ----------

_DIFF_FILE_HEADER = re.compile(r"^diff --git a/.* b/(.*)$")
_BLAME_HEADER = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)")


def _split_patch_by_file(patch_text: str) -> dict[str, str]:
    """Splits a multi-file `git diff` into {new_path: patch_body}."""
    sections: dict[str, str] = {}
    current_path: str | None = None
    current_lines: list[str] = []
    for line in patch_text.splitlines():
        m = _DIFF_FILE_HEADER.match(line)
        if m:
            if current_path is not None:
                sections[current_path] = "\n".join(current_lines)
            current_path = m.group(1)
            current_lines = []
        elif current_path is not None:
            current_lines.append(line)
    if current_path is not None:
        sections[current_path] = "\n".join(current_lines)
    return sections


def local_touched_line_ranges(
    repo: GitRepository, fix_sha: str, parent_sha: str, errors: ErrorCollector | None = None
) -> dict[str, list[tuple[int, int]]]:
    """Diffs the fix commit against its first parent (locally - only needs
    the two specific tree snapshots, always fast regardless of a file's
    total revision count) to find which OLD line ranges per file it
    touched. Returns {path: [(start,end), ...]}, capped at MAX_FILES_PER_FIX."""
    try:
        patch_text = repo.run_command(["diff", "--no-color", "-U0", parent_sha, fix_sha])
    except Exception as e:
        logging.error(f"Local diff failed for {fix_sha}: {e}")
        if errors:
            errors.record("fix_diff", fix_sha, e)
        return {}

    if not patch_text:
        return {}

    sections = _split_patch_by_file(patch_text)
    touched: dict[str, list[tuple[int, int]]] = {}
    for path, body in list(sections.items())[:MAX_FILES_PER_FIX]:
        ranges = _old_line_ranges_from_patch(body)
        if ranges:
            touched[path] = ranges
    return touched


def _revision_count(
    repo: GitRepository, parent_sha: str, path: str, errors: ErrorCollector | None = None
) -> int:
    """Pure metadata, no blob content - cheap and safe to run on every
    touched file before deciding whether to blame it locally."""
    try:
        out = repo.run_command(["rev-list", "--count", parent_sha, "--", path])
        return int(out.strip()) if out.strip().isdigit() else 0
    except Exception as e:
        logging.error(f"Revision-count check failed for {path} @ {parent_sha[:8]}: {e}")
        if errors:
            errors.record("revision_count", f"{path} @ {parent_sha[:8]}", e)
        return 0  # unknown - treat as low-churn and try local blame; a real failure there fails fast


def _local_blame_ranges(
    repo: GitRepository, parent_sha: str, path: str, errors: ErrorCollector | None = None
) -> list[tuple[int, int, str]]:
    """[(start_line, end_line, sha), ...], merging consecutive lines
    attributed to the same commit."""
    try:
        output = repo.run_command(["blame", "--porcelain", parent_sha, "--", path])
    except Exception as e:
        logging.error(f"Local blame failed for {path} @ {parent_sha[:8]}: {e}")
        if errors:
            errors.record("local_blame", f"{path} @ {parent_sha[:8]}", e)
        return []

    per_line: list[tuple[int, str]] = []
    for line in output.splitlines():
        if line.startswith("\t"):
            continue
        m = _BLAME_HEADER.match(line)
        if m:
            per_line.append((int(m.group(2)), m.group(1)))

    per_line.sort(key=lambda x: x[0])
    ranges: list[tuple[int, int, str]] = []
    for line_no, sha in per_line:
        if ranges and ranges[-1][2] == sha and ranges[-1][1] == line_no - 1:
            ranges[-1] = (ranges[-1][0], line_no, sha)
        else:
            ranges.append((line_no, line_no, sha))
    return ranges


def find_fix_target_hybrid(
    repo: GitRepository, owner: str, repo_name: str, fix_sha: str, parent_sha: str,
    errors: ErrorCollector | None = None,
) -> BlameTarget | None:
    """Local diff finds touched line ranges. Each touched file is then
    routed by its revision count: low (the common case) -> local blame,
    fast and bandwidth-free; high -> the existing single batched GraphQL
    blame call, for just that file. See the module docstring for why."""
    touched_ranges = local_touched_line_ranges(repo, fix_sha, parent_sha, errors)
    if not touched_ranges:
        return None

    local_paths = []
    api_paths = []
    for path in touched_ranges:
        if _revision_count(repo, parent_sha, path, errors) > MAX_LOCAL_BLAME_REVISIONS:
            api_paths.append(path)
        else:
            local_paths.append(path)

    overlap_by_commit: dict[str, int] = {}
    info_by_commit: dict[str, dict] = {}

    for path in local_paths:
        for b_start, b_end, sha in _local_blame_ranges(repo, parent_sha, path, errors):
            for r in touched_ranges[path]:
                overlap = _ranges_overlap(r, b_start, b_end)
                if overlap > 0:
                    overlap_by_commit[sha] = overlap_by_commit.get(sha, 0) + overlap

    if api_paths:
        try:
            blame_by_path = GITHUB_API.get_blame_batch(owner, repo_name, parent_sha, api_paths)
        except Exception as e:
            logging.error(f"API blame fallback failed for {fix_sha} at parent {parent_sha}: {e}")
            if errors:
                errors.record("api_blame_fallback", f"{fix_sha} @ parent {parent_sha[:8]} ({len(api_paths)} files)", e)
            blame_by_path = {}

        for path in api_paths:
            for blame_range in blame_by_path.get(path, []):
                b_start, b_end = blame_range["startingLine"], blame_range["endingLine"]
                for r in touched_ranges[path]:
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

    if best_oid in info_by_commit:
        best = info_by_commit[best_oid]
        author = best.get("author") or {}
        user = author.get("user") if author else None
        return BlameTarget(
            target_sha=best_oid, target_date=best.get("committedDate", ""),
            target_author=Author(
                login=(user.get("login") if user else "") or "",
                name=author.get("name") or "", email=author.get("email") or "",
            ),
            target_message=best.get("message") or "", overlap_lines=overlap_by_commit[best_oid],
        )

    github_url = f"https://github.com/{owner}/{repo_name}"
    target = resolve_commit_locally(repo, github_url, best_oid, errors)
    if target is None:
        return None
    return BlameTarget(
        target_sha=target.sha, target_date=target.date, target_author=target.author,
        target_message=target.message, overlap_lines=overlap_by_commit[best_oid],
    )

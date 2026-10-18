#!/usr/bin/env python3
"""
1. Generates file lists for GitHub repositories from a CSV file.

This script uses an "API-first, clone-as-fallback" strategy:
    1. It first attempts to get the file list using the GitHub API, which is very fast.
    2. If the API fails because the repository is too large, it automatically
   falls back to performing a temporary shallow clone to generate the file list.

2. This script analyzes repository file lists to detect which development tools
are used, based on configuration files.

3. Extracts file-level adoption metadata from partial Git clones.

This script processes a directory of repositories cloned with --no-checkout.
For each repository, it identifies files matching given regex patterns and
extracts metadata for each file, such as its creation date and number of
modifications.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from heuristics import path_matches
from utils import cloning, logging
from utils.github import GITHUB_API, RepositoryTooLargeError, parse_github_url
from utils.repository import GitRepository


def write_file_list(output_path: Path, file_list: list[str]) -> None:
    """Writes the list of files to a text file."""
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            _ = f.write("\n".join(file_list))
    except IOError as e:
        logging.error(f"Error writing file list for {output_path}: {e}")
        raise RuntimeError("error writing file list")


def read_file_list(input_path: Path) -> list[str]:
    """Reads a list of files from a text file."""
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            return f.read().splitlines()
    except IOError as e:
        logging.error(f"Error reading file list from {input_path}: {e}")
        raise RuntimeError("error reading file list")


def get_file_list(repo_url: str) -> list[str]:
    """
    Worker function to get a file list for a single repository.
    Implements the API-first, clone-as-fallback strategy.
    """

    owner, repo = parse_github_url(repo_url)
    logging.info("querying API for file list")

    if not owner or not repo:
        raise RuntimeError(f"Invalid GitHub URL: {repo_url}")

    try:
        # 1. Attempt to get file list via API
        file_list = GITHUB_API.get_file_list_by_api(owner, repo)
        logging.info(f"✅ Successfully retrieved file list for {owner}/{repo} via API.")

    except RepositoryTooLargeError as e:
        # 2. If repo is too large, fall back to shallow clone
        logging.info(f"⚠️  {e}. Falling back to shallow clone...")
        file_list = cloning.get_file_list_by_clone(repo_url)

        if not file_list:
            logging.error("shallow clone also failed")
            raise RuntimeError("shallow clone failed")

    except TimeoutError:
        logging.error(f"❌ Timeout processing {owner}/{repo}")
        raise RuntimeError("Timeout processing file list")

    except Exception as e:
        logging.error(f"❌ Failed to get file list for {owner}/{repo}: {e}")
        raise RuntimeError("failed to get file list")

    return file_list


@dataclass
class ToolResult:
    tool: str
    file_path: str


def find_repo_tools(file_list: list[str]) -> list[ToolResult]:
    logging.info("starting tool search")
    matched_files: list[ToolResult] = []

    for file_path in file_list:
        file_path = file_path.strip()
        if not file_path:
            continue
        toolname = path_matches(file_path)
        if toolname:
            matched_files.append(ToolResult(toolname, file_path))

    tools: set[str] = set()
    for tr in matched_files:
        tools.add(tr.tool)
    logging.info(f"found {len(matched_files)} matches for {len(tools)} tools")
    return matched_files


def encode_path(path: str) -> str:
    """
    Make a file-system-safe encoding of a relative path.
    Example: "src/foo/bar.txt" -> "src_foo_bar.txt"
    """
    return re.sub(r"[^A-Za-z0-9._-]", "_", path)


@dataclass
class ToolMetrics:
    tool: str
    file_path: str
    added_timestamp: datetime
    last_modification: datetime
    lines: int
    modifications: int


# use this to get the gitignore
# def get_file_content(
#    self,
#    owner: str,
#    repo: str,
#    path: str,
#    ref: Optional[str] = None,
#    decode: bool = True
# ) -> Union[str, bytes, Dict]:
#
def get_gitignore_file_tools(repo_url: str, tool_output_dir: Path) -> list[ToolMetrics]:
    """
    gets the gitignore file by API, if present, and check for tool declarations there
    """

    owner, repo = parse_github_url(repo_url)
    logging.info("querying API for main gitignore file")
    try:
        gitignore: str = GITHUB_API.get_file_content(owner, repo, ".gitignore")
        ignore_file = tool_output_dir / f"_gitignore"
        with open(ignore_file, "w", encoding="utf-8") as f_out:
            _ = f_out.write(gitignore)

        ignore_lines = gitignore.splitlines()
        return find_gitignore_matches(ignore_lines, tool_output_dir)
    except Exception as e:
        logging.error(f"❌ Failed to get gitignore for {owner}/{repo}: {e}")
        return []


def find_gitignore_matches(
    gitignore_contents: list[str], tool_output_dir: Path
) -> list[ToolMetrics]:
    """
    find
    """
    try:
        ignore_lines = gitignore_contents
        ignored_tools = find_repo_tools(ignore_lines)
        dest_file = tool_output_dir / f"_gitignore_tool_matches_{len(ignored_tools)}"
        file_content: str = "\n".join([t.file_path for t in ignored_tools])
        with open(dest_file, "w", encoding="utf-8") as f_out:
            _ = f_out.write(file_content)
        future_date = datetime.now(timezone.utc) + timedelta(days=36500)
        tool_metrics = [
            ToolMetrics(tr.tool, tr.file_path, future_date, future_date, -1, -1)
            for tr in ignored_tools
        ]
        return tool_metrics
    except Exception as e:
        logging.error(f"❌ Failed to get gitignore for {owner}/{repo}: {e}")
        return []


def check_gitignore(repo: GitRepository, tool_output_dir: Path) -> list[ToolResult]:
    logging.info("checking content of gitignore")
    try:
        ignore_lines = repo.get_file_content_from_blob(".gitignore").splitlines()
        ignored_tools = find_repo_tools(ignore_lines)
        dest_file = tool_output_dir / f"_gitignore_tool_matches_{len(ignored_tools)}"
        file_content: str = "\n".join([t.file_path for t in ignored_tools])
        print("found a gitignore that matches")
        for m in ignored_tools:
            print(f"          {m.file_path}")
        with open(dest_file, "w", encoding="utf-8") as f_out:
            _ = f_out.write(file_content)
        return ignored_tools
    except Exception as e:
        logging.error(f"No .gitignore here: {e}")
        return []


def tool_file_stats(
    repo: GitRepository, matches: list[ToolResult], tool_output_dir: Path
) -> list[ToolMetrics]:
    """
    Worker function to process a single repository and return its metadata.
    """

    logging.info("getting file stats")
    try:
        repo.populate_index()

        # check_gitignore(repo, tool_output_dir)
        matched_files = [m.file_path for m in matches]
        all_file_history_metrics: dict[str, dict[str, int]] = (
            repo.get_file_history_metrics(file_paths=matched_files)
        )

        file_metrics: list[ToolMetrics] = []

        for m in matches:
            file_content = repo.get_file_content_from_blob(m.file_path)
            file_history = all_file_history_metrics.get(m.file_path)
            n_lines = len(file_content.splitlines())
            added_ts = file_history.get("added_timestamp", 0)
            n_mods = file_history.get("modifications", 0)
            last_mod = file_history.get("last_mod", 0)
            # print(m.file_path, ": ", last_mod)
            if added_ts is None:
                print("added_ts is none!")
                print(repo.repo_path)
                print(m.file_path)
                added_ts = datetime.now(timezone.utc) + timedelta(days=36500)
            else:
                added_ts = datetime.fromtimestamp(added_ts, tz=timezone.utc)

            if last_mod is None:
                print("last_mod is none!")
                print(repo.repo_path)
                print(m.file_path)
            dest_file = tool_output_dir / f"{m.tool}_{encode_path(m.file_path)}"
            with open(dest_file, "w", encoding="utf-8") as f_out:
                _ = f_out.write(file_content)

            metrics = ToolMetrics(
                m.tool,
                str(dest_file),
                added_ts,
                datetime.fromtimestamp(last_mod, tz=timezone.utc),
                n_lines,
                n_mods,
            )
            file_metrics.append(metrics)

        return file_metrics

    except Exception as e:
        logging.error(f"getting file stats failed: {e}")
        raise e

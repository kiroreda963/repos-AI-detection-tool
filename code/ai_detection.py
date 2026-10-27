"""
Clone-free checks for whether a repo shows signs of AI-tool adoption outside
its commit history: AI-tool config/marker files tracked in the repo (e.g.
.cursor/, CLAUDE.md), or those same files referenced only in .gitignore (tool
used, but its files were never committed). Shared by every pipeline variant
that needs these two signals, so the logic lives in exactly one place.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from find_tools import find_repo_tools, get_file_list
from utils import logging
from utils.github import GITHUB_API


@dataclass
class AdoptionSignals:
    file_manifest_hit: bool
    gitignore_hit: bool
    file_manifest_tools: set[str] = field(default_factory=set)
    gitignore_tools: set[str] = field(default_factory=set)


def detect_file_and_gitignore_signals(github_url: str, owner: str, repo: str) -> AdoptionSignals:
    """Two REST calls total (file-tree, then .gitignore content) - both
    already API-first/clone-free via find_tools.get_file_list and
    GITHUB_API.get_file_content."""
    file_manifest_tools: set[str] = set()
    try:
        file_list = get_file_list(github_url)
        file_manifest_tools = {tr.tool for tr in find_repo_tools(file_list)}
    except Exception as e:
        logging.warning(f"Could not fetch file tree for {github_url}: {e}")

    gitignore_tools: set[str] = set()
    try:
        gitignore_content = GITHUB_API.get_file_content(owner, repo, ".gitignore")
        if isinstance(gitignore_content, str):
            gitignore_tools = {tr.tool for tr in find_repo_tools(gitignore_content.splitlines())}
    except Exception:
        pass  # no .gitignore, or it couldn't be fetched - not an adoption signal either way

    return AdoptionSignals(
        file_manifest_hit=len(file_manifest_tools) > 0,
        gitignore_hit=len(gitignore_tools) > 0,
        file_manifest_tools=file_manifest_tools,
        gitignore_tools=gitignore_tools,
    )

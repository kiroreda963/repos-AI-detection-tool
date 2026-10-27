"""
Cheapest possible AI-adoption check: commit history + file-manifest +
.gitignore checks only. No cloning, no fix/revert detection, no blame/patch
calls - those are ~95% of the lean pipeline's API cost and aren't needed for
this summary. Per repo, this costs: 1 REST file-tree call + 1 REST .gitignore
call + 1 GraphQL call per 100 commits. Nothing else.

This file is fully standalone - it does not import from or modify any of the
lean_* pipeline files.

Deliberately does NOT use `from __future__ import annotations`: that turns
every dataclass field's type into a plain string at runtime, which breaks
utils/import_export.py's type-based CSV round-trip (every non-string field,
e.g. int/bool, comes back from a resumed CSV as an unconverted string).
"""
import traceback
from dataclasses import dataclass

from ai_detection import detect_file_and_gitignore_signals
from heuristics import commit_type_tool
from utils.commit_history import get_commit_history
from utils.data import Author, Commit
from utils.github import get_repo_name_from_url, parse_github_url


@dataclass
class AIAdoptionSummary:
    github_url: str
    repo_name: str
    is_ai_adopter: bool
    ai_identification_methods: str  # comma-joined: file_manifest, gitignore, commits - or "none"
    ai_tools_used: str              # comma-joined tool names (e.g. "Claude_Code,Copilot") - or "none"
    ai_commits: int
    human_commits: int
    bot_commits: int
    total_commits: int
    error: str = ""


def _author_type(author: Author, message: str) -> tuple[str, str]:
    dummy = Commit(sha="", author=author, co_authors=[], message=message, date="")
    return commit_type_tool(dummy)


class AIAdoptionAnalyzer:
    def __init__(self, github_url: str, analysis_period: str = "2025-01-01"):
        self.github_url = github_url
        self.analysis_period = analysis_period
        self.owner, self.repo = parse_github_url(github_url)

    def analyze(self) -> AIAdoptionSummary:
        repo_name = get_repo_name_from_url(self.github_url) or self.github_url

        try:
            if not self.owner or not self.repo:
                raise RuntimeError(f"Invalid GitHub URL: {self.github_url}")

            print(f"\n[REPO >] {self.github_url}")

            signals = detect_file_and_gitignore_signals(self.github_url, self.owner, self.repo)
            commits = get_commit_history(self.github_url, self.analysis_period)

            ai_count = human_count = bot_count = 0
            commit_tools: set[str] = set()
            for commit in commits:
                author_type, tool = _author_type(commit.author, commit.message)
                if author_type == "AI":
                    ai_count += 1
                    if tool:
                        commit_tools.add(tool)
                elif author_type == "Bot":
                    bot_count += 1
                else:
                    human_count += 1

            identified_via_commits = ai_count > 0
            methods = []
            if signals.file_manifest_hit:
                methods.append("file_manifest")
            if signals.gitignore_hit:
                methods.append("gitignore")
            if identified_via_commits:
                methods.append("commits")

            all_tools = commit_tools | signals.file_manifest_tools | signals.gitignore_tools

            summary = AIAdoptionSummary(
                github_url=self.github_url, repo_name=repo_name,
                is_ai_adopter=bool(methods),
                ai_identification_methods=",".join(methods) if methods else "none",
                ai_tools_used=",".join(sorted(all_tools)) if all_tools else "none",
                ai_commits=ai_count, human_commits=human_count, bot_commits=bot_count,
                total_commits=len(commits),
            )
            print(f"\n[REPO <] {self.github_url} -> {summary.total_commits} commits, "
                  f"ai_adopter={summary.is_ai_adopter}, methods={summary.ai_identification_methods}, "
                  f"tools={summary.ai_tools_used}")
            return summary

        except Exception:
            err = traceback.format_exc()
            print(f"\n[REPO x] {self.github_url} -> failed: {err.splitlines()[-1]}")
            return AIAdoptionSummary(
                github_url=self.github_url, repo_name=repo_name, is_ai_adopter=False,
                ai_identification_methods="none", ai_tools_used="none",
                ai_commits=0, human_commits=0, bot_commits=0, total_commits=0, error=err,
            )


if __name__ == "__main__":
    analyzer = AIAdoptionAnalyzer("https://github.com/liam-hq/liam", analysis_period="2025-01-01")
    print(analyzer.analyze())

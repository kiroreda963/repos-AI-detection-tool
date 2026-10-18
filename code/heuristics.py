import re
import csv
import sys
from collections import defaultdict
from collections.abc import Sequence

from utils import logging
from utils.data import Commit, PullRequest, Author
from utils.metrics import CommitType, AuthorType, TypeTool

"""
Centralized location for default pattern file paths.
This allows scripts to import these paths instead of hardcoding them,
and makes it easy to update them in one place.
"""


def load_patterns(pattern_type: str, is_files: bool=False) -> re.Pattern[str]:
    """
    Load file patterns from a CSV (tool_name, file_pattern).
    Returns a single compiled regex where each tool_name is a
    named group that can match any of its file patterns.
    """
    # sanitize group names: only letters, digits, underscore
    def sanitize(name: str) -> str:
        return re.sub(r'\W+', '_', name)

    patterns_csv = f"../config/patterns/{pattern_type}.csv"
    patterns: dict[str, list[str]] = defaultdict(list)
    try:
        with open(patterns_csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                tool_name = sanitize(row['subtype'].strip())
                pattern = row['pattern'].strip()

                if is_files:
                    # Escape bare dots
                    if '.' in pattern and '\\.' not in pattern:
                        pattern = pattern.replace('.', '\\.')

                    # Anchor: match only at start of string or after a slash
                    if not pattern.startswith('(?:^|/)') and not pattern.startswith('^'):
                        pattern = '(?:^|/)' + pattern

                patterns[tool_name].append(pattern)

        # Build one named group per tool, combining its patterns
        parts: list[str] = []
        for name, pats in patterns.items():
            combined = "|".join(pats)
            parts.append(f"(?P<{name}>{combined})")
        any_re = re.compile("|".join(parts))
        return any_re
    except FileNotFoundError:
        logging.error(f"Error: Pattern file '{patterns_csv}' not found.")
        sys.exit(1)
    except KeyError as e:
        logging.error(f"Error: Missing column {e} in patterns CSV file.")
        sys.exit(1)
    except Exception as e:
        raise RuntimeError(f"Failed to load patterns: {e}")



AUTHORS = load_patterns("authors")
FILES =  load_patterns("files", is_files=True)
BOTS = load_patterns("bots")
LABELS = load_patterns("labels")
BRANCHES = load_patterns("branches")
# MESSAGES = load_patterns("messages")

CO_AUTH = re.compile(r'[Cc]o-[Aa]uthored-[Bb]y:\s*([^<]+)<([^>]+)>')


def path_matches(file_path:str) -> str:
    for m in FILES.finditer(file_path):
        return m.lastgroup if m.lastgroup else ""
        # `m.lastgroup` is the pattern name that matched
        #print(f"Line {lineno} – pattern '{m.lastgroup}' matched: {m.group()}")
    return ""

def extract_co_authors(commit_message: str) -> list[Author]:
    """Extract co-authors from commit message."""
    co_authors: list[Author] = []
    matches : tuple[str, str] = re.findall(CO_AUTH, commit_message)

    for name, email in matches:
        # print("name: ", name, " email: ", email)
        co_authors.append(Author(login="", name=name.strip(), email=email.strip()))

    return co_authors

def all_authors(commit: Commit) -> list[Author]:
    authors = [commit.author]
    authors.extend(extract_co_authors(commit.message))
    return authors

def all_authors_of_type(commit: Commit, type: AuthorType) -> list[Author]:
    return [author for author in all_authors(commit) if author_matches(author)[0] == type]

def all_authors_of_types(commit: Commit, types: list[AuthorType]) -> list[Author]:
    return [author for author in all_authors(commit) if author_matches(author)[0] in types]


def any_authors_of_type(commit: Commit, type: AuthorType) -> bool:
    return len(all_authors_of_type(commit, type)) > 0

def any_authors_of_types(commit: Commit, types: list[AuthorType]) -> bool:
    return len(all_authors_of_types(commit, types)) > 0

def is_ai_author(author_str: str): return bool(re.search(AUTHORS, author_str))

def is_bot_author(author_str: str): return bool(re.search(BOTS, author_str))

def is_ai_pr_branch(pr: PullRequest):
    return bool(re.search(BRANCHES, pr.branch_name))

def is_ai_pr_label(pr: PullRequest):
    all_labels = ""
    for l in pr.labels:
        all_labels = f"{l} {all_labels}"
    return bool(re.search(LABELS, all_labels))



def author_matches(author: Author) -> TypeTool:
    author_str = str(author)
    #print("author matches: ", author_str)
    for m in AUTHORS.finditer(author_str):
        return "AI", m.lastgroup if m.lastgroup else ""
    for m in BOTS.finditer(author_str):
            #print("it's a bot!")
            return "Bot", m.lastgroup if m.lastgroup else ""
    #print(f"This is a Human: {author_str}")
    return "Human", ""

def pr_type_tool(pr: PullRequest) -> TypeTool:
    for m in BRANCHES.finditer(pr.branch_name):
        return "AI", (m.lastgroup if m.lastgroup else "")
    for l in pr.labels:
        for m in LABELS.finditer(l):
            return "AI", (m.lastgroup if m.lastgroup else "")
    return best_commit_type_tool(pr.commits)

def commit_type(commit: Commit) -> AuthorType:
    return commit_type_tool(commit)[0]

def commit_type_tool(commit: Commit) -> TypeTool:
    all_authors = [commit.author]
    co_authors = extract_co_authors(commit.message)
    all_authors.extend(co_authors)
    type, tool = "Human", ""
    for author in all_authors:
        ttype, ttool = author_matches(author)
        if ttype == "AI":
            return ttype, ttool
        if ttype == "Bot":
            type, tool = ttype, ttool
    return type, tool

def best_commit_type_tool(commits: Sequence[Commit]) -> TypeTool:
    type, tool = "Human", ""
    for commit in commits:
        ttype, ttool = commit_type_tool(commit)
        if ttype == "AI":
            return ttype, ttool
        if ttype == "Bot":
            type, tool = ttype, ttool
    return type, tool

def human_authors(commit: Commit) -> list[Author]:
    return all_authors_of_type(commit,"Human")

def is_ai_pr_meta(pr: PullRequest) -> bool:
    return is_ai_pr_branch(pr) or is_ai_pr_label(pr)

def is_ai_pr_body(pr: PullRequest) -> bool:
    if not pr.bodyText: return False
    return "https://chatgpt.com/codex/tasks" in pr.bodyText

def is_ai_pr(pr: PullRequest) -> bool:
    return is_ai_pr_meta(pr) or any_ai_commits(pr.commits)

def any_ai_commits(commits: Sequence[Commit]) -> bool:
    for cm in commits:
        if any_authors_of_type(cm, "AI"):
            return True
    return False

def all_ai_commits(commits: Sequence[Commit]) -> Sequence[Commit]:
    return [cm for cm in commits if any_authors_of_type(cm, "AI")]

def all_ai_commits_and_followups(commits: Sequence[Commit]) -> Sequence[Commit]:
    return [cm for cm in commits if any_authors_of_types(cm, ["AI", "AI-FOLLOWUP"])]

def is_commit_adopter(prs: list[PullRequest], commits: Sequence[Commit]) -> bool:
    for pr in prs:
        if is_ai_pr_meta(pr):
            return True
        if any_ai_commits(pr.commits):
            return True
    return any_ai_commits(commits)

#def filter_commit_message(message: str) -> bool:
#    header = message.splitlines()[0]
#    matches : tuple[str, str] = re.findall(MESSAGES, header)

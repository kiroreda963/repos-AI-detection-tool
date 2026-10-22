"""
Fetches commit history via the GitHub GraphQL API instead of cloning the repo.

Mirrors the retry/pagination pattern used by get_pull_requests.py's
fetch_prs_retries, but walks Commit.history on the default branch.
"""
import random
import time
from dataclasses import dataclass
from datetime import datetime

import requests

from utils import logging
from utils.data import Author
from utils.github import GITHUB_API, parse_github_url

HISTORY_QUERY = """
query($owner: String!, $name: String!, $pageSize: Int!, $cursor: String, $since: GitTimestamp) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: $pageSize, after: $cursor, since: $since) {
            pageInfo { endCursor hasNextPage }
            nodes {
              oid
              message
              committedDate
              additions
              deletions
              changedFilesIfAvailable
              author { name email user { login } }
              parents(first: 5) { nodes { oid } }
            }
          }
        }
      }
    }
  }
  rateLimit { cost remaining resetAt }
}
"""

SINGLE_COMMIT_QUERY = """
query($owner: String!, $name: String!, $oid: GitObjectID!) {
  repository(owner: $owner, name: $name) {
    object(oid: $oid) {
      ... on Commit {
        oid
        message
        committedDate
        additions
        deletions
        changedFilesIfAvailable
        author { name email user { login } }
        parents(first: 5) { nodes { oid } }
      }
    }
  }
}
"""


@dataclass
class ApiCommit:
    """A commit as fetched from the GraphQL API - no clone, no local git."""
    sha: str
    author: Author
    co_authors: list[Author]
    message: str
    date: datetime
    parents: list[str]
    additions: int
    deletions: int
    changed_files: int | None

    def is_merge(self) -> bool:
        return len(self.parents) > 1


def _node_to_commit(node: dict) -> ApiCommit:
    author = node.get("author") or {}
    user = author.get("user") if author else None
    login = user.get("login") if user else ""
    auth = Author(login=login or "", name=author.get("name") or "", email=author.get("email") or "")

    from heuristics import extract_co_authors
    message = node.get("message", "")
    co_authors = extract_co_authors(message)

    parents = [p["oid"] for p in (node.get("parents") or {}).get("nodes", [])]

    return ApiCommit(
        sha=node.get("oid", ""),
        author=auth,
        co_authors=co_authors,
        message=message,
        date=node.get("committedDate", ""),
        parents=parents,
        additions=node.get("additions", 0) or 0,
        deletions=node.get("deletions", 0) or 0,
        changed_files=node.get("changedFilesIfAvailable"),
    )


def fetch_commit_history_retries(
    owner: str, repo: str, since_date: str, page_size: int = 100,
    max_results: int = 100_000, max_retries: int = 3, initial_backoff: float = 2.0,
):
    """Yields raw GraphQL commit nodes, newest first, down to since_date."""
    cursor = None
    count = 0

    while True:
        variables = {"owner": owner, "name": repo, "pageSize": page_size, "cursor": cursor, "since": since_date}

        data = None
        for attempt in range(max_retries):
            try:
                data = GITHUB_API.run_graphQL_query(HISTORY_QUERY, variables)
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError) as e:
                if attempt == max_retries - 1:
                    logging.error(f"Failed after {max_retries} attempts: {e}")
                    raise
                backoff = initial_backoff * (2 ** attempt) + random.uniform(0, 1)
                logging.info(f"Network error (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {backoff:.1f}s")
                time.sleep(backoff)
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code in (502, 504) and attempt < max_retries - 1:
                    backoff = initial_backoff * (2 ** attempt) + random.uniform(0, 1)
                    logging.info(f"Gateway error {e.response.status_code}, retrying in {backoff:.1f}s")
                    time.sleep(backoff)
                else:
                    raise

        default_ref = (data.get("repository") or {}).get("defaultBranchRef")
        if not default_ref or not default_ref.get("target"):
            return
        history = default_ref["target"]["history"]

        for node in history["nodes"]:
            if node["committedDate"] < since_date:
                return
            if max_results and count >= max_results:
                return
            yield node
            count += 1

        if not history["pageInfo"]["hasNextPage"]:
            return
        cursor = history["pageInfo"]["endCursor"]


def get_commit_history(url: str, since_date: str) -> list[ApiCommit]:
    """Fetches the full commit history of a repo's default branch, no clone."""
    owner, repo = parse_github_url(url)
    if not owner or not repo:
        raise RuntimeError(f"Invalid GitHub URL: {url}")

    since_iso = since_date if "T" in since_date else f"{since_date}T00:00:00Z"
    return [_node_to_commit(node) for node in fetch_commit_history_retries(owner, repo, since_iso)]


def get_single_commit(url: str, sha: str) -> ApiCommit | None:
    """Looks up one commit by sha - used only when a fix/revert target falls
    outside the already-fetched history window."""
    owner, repo = parse_github_url(url)
    if not owner or not repo:
        return None
    try:
        data = GITHUB_API.run_graphQL_query(SINGLE_COMMIT_QUERY, {"owner": owner, "name": repo, "oid": sha})
        node = (data.get("repository") or {}).get("object")
        if not node:
            return None
        return _node_to_commit(node)
    except Exception as e:
        logging.error(f"Failed to look up commit {sha} in {owner}/{repo}: {e}")
        return None

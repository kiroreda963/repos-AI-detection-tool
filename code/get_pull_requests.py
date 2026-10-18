import csv
from utils import logging
from utils.data import Author, PRCommit, PullRequest
from utils.github import GITHUB_API, parse_github_url
import datetime
import requests
import random
import time

# if timeout or N >= 3000 (the max, or 2000)
# switch to search strategy (but make it work ...)
# search for 500 PRs every 15 days?
# reduce number of commits per PR? max 100? it's just for tagging, so ..
# # check page size issues
#

simple_labels = False


QUERY = """
query($owner: String!, $name: String!, $pageSize: Int!, $cursor: String) {
repository(owner: $owner, name: $name) {
    pullRequests(first: $pageSize, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
    totalCount
    pageInfo {
      endCursor
      hasNextPage
    }
    nodes {
      ... on PullRequest {
        number
        title
        bodyText
        headRefName
        updatedAt
        mergedAt
        additions
        deletions
        changedFiles
        author {
            login
            ... on User {
                  name
            }
        }
        mergeCommit {
            oid
        }
        labels(first: 5) {
           nodes {
             name
             }
        }
        commits(first: 100) {
          nodes {
            commit {
              oid
              message
              committedDate
              author {
                name
                email
                user { login }
              }
            }
          }
        }
      }
    }
  }
  }
  rateLimit {
    cost
    remaining
    resetAt
  }
}
"""

QUERY_TL = """
query($owner: String!, $name: String!, $pageSize: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      first: $pageSize
      after: $cursor
      orderBy: { field: UPDATED_AT, direction: DESC }
    ) {
      totalCount
      pageInfo {
        endCursor
        hasNextPage
      }
      nodes {
        ... on PullRequest {
          number
          title
          bodyText
          headRefName
          updatedAt
          mergedAt
          additions
          deletions
          changedFiles
          author {
            login
            ... on User {
              name
            }
          }
          mergeCommit {
            oid
          }
          labels(first: 5) {
            nodes {
              name
            }
          }
          commits(first: 200) {
            nodes {
              commit {
                oid
                message
                committedDate
                author {
                  name
                  email
                  user {
                    login
                  }
                }
              }
            }
          }
          timelineItems(first: 5, itemTypes: [LABELED_EVENT]) {
            nodes {
              __typename
              ... on LabeledEvent {
                createdAt
                actor {
                  login
                }
                label {
                  name
                  color
                }
              }
            }
          }
        }
      }
    }
  }
  rateLimit {
    cost
    remaining
    resetAt
  }
}

"""

QUERY_DATE = """
query($owner: String!, $name: String!, $pageSize: Int!, $cursor: String, $since: DateTime, $until: DateTime) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      first: $pageSize
      after: $cursor
      orderBy: { field: UPDATED_AT, direction: DESC }
      # Filter by update date range
      filterBy: {
        since: $since
        until: $until
      }
    ) {
      totalCount
      pageInfo {
        endCursor
        hasNextPage
      }
      nodes {
        ... on PullRequest {
          number
          title
          bodyText
          headRefName
          updatedAt
          mergedAt
          additions
          deletions
          changedFiles
          author {
            login
            ... on User {
              name
            }
          }
          mergeCommit {
            oid
          }
          labels(first: 5) {
            nodes {
              name
            }
          }
          commits(first: 75) {
            nodes {
              commit {
                oid
                message
                committedDate
                author {
                  name
                  email
                  user {
                    login
                  }
                }
              }
            }
          }
          timelineItems(first: 5, itemTypes: [LABELED_EVENT]) {
            nodes {
              __typename
              ... on LabeledEvent {
                createdAt
                actor {
                  login
                }
                label {
                  name
                  color
                }
              }
            }
          }
        }
      }
    }
  }
  rateLimit {
    cost
    remaining
    resetAt
  }
}
"""

SEARCH_QUERY = """
query($searchQuery: String!, $pageSize: Int!, $cursor: String) {
  search(
    type: ISSUE
    query: $searchQuery
    first: $pageSize
    after: $cursor
  ) {
    issueCount
    pageInfo {
      endCursor
      hasNextPage
    }
    nodes {
      ... on PullRequest {
        number
        title
        bodyText
        headRefName
        updatedAt
        mergedAt
        additions
        deletions
        changedFiles
        author {
          login
          ... on User {
            name
          }
        }
        mergeCommit {
          oid
        }
        labels(first: 5) {
          nodes {
            name
          }
        }
        commits(first: 200) {
          nodes {
            commit {
              oid
              message
              committedDate
              author {
                name
                email
                user {
                  login
                }
              }
            }
          }
        }
        timelineItems(first: 5, itemTypes: [LABELED_EVENT]) {
          nodes {
            __typename
            ... on LabeledEvent {
              createdAt
              actor {
                login
              }
              label {
                name
                color
              }
            }
          }
        }
      }
    }
  }
  rateLimit {
    cost
    remaining
    resetAt
  }
}
"""

def old_fetch_prs(owner: str, repo: str, since_date: str, page_size: int =50, max_results: int =3000):
    query_string = f"repo:{owner}/{repo} is:pr updated:>={since_date}"
    cursor = None
    count = 0

    while True:
        variables = {"queryString": query_string, "pageSize": page_size, "cursor": cursor}
        data = GITHUB_API.run_graphQL_query(QUERY, variables)

        # Print rate limit info
        rl = data["rateLimit"]
        print(f"[rateLimit] cost={rl['cost']}, remaining={rl['remaining']}, resetAt={rl['resetAt']}")

        search = data["search"]
        for pr in search["nodes"]:
            if max_results and count >= max_results:
                return
            yield pr
            count += 1

        if not search["pageInfo"]["hasNextPage"]:
            break
        cursor = search["pageInfo"]["endCursor"]

def fetch_prs(owner: str, repo: str, since_date: str, page_size: int =50, max_results: int =5000):
    cursor = None
    count = 0

    while True:
        variables = {"owner": owner, "name": repo, "pageSize": page_size, "cursor": cursor}
        data = GITHUB_API.run_graphQL_query(QUERY if simple_labels else QUERY_TL, variables)
        pr_connection = data["repository"]["pullRequests"]

        # Print rate limit info
        #rl = data["rateLimit"]
        #print(f"[rateLimit] cost={rl['cost']}, remaining={rl['remaining']}, resetAt={rl['resetAt']}")

        for pr in pr_connection["nodes"]:
            if pr['updatedAt'] < since_date:
                return
            if max_results and count >= max_results:
                return
            yield pr
            count += 1

        if not pr_connection["pageInfo"]["hasNextPage"]:
            break
        cursor = pr_connection["pageInfo"]["endCursor"]

should_print = False

def fetch_prs_retries(owner: str, repo: str, since_date: str, page_size: int = 50, max_results: int = 5000,
              max_retries: int = 3, initial_backoff: float = 2.0):
    cursor = None
    count = 0
    rates = None

    def printrates(rl):
        if rl and should_print:
            print(f"{owner}/{repo}: project done")
            print(f"[rateLimit] cost={rl['cost']}, remaining={rl['remaining']}, resetAt={rl['resetAt']}")

    while True:
        variables = {"owner": owner, "name": repo, "pageSize": page_size, "cursor": cursor}

        # Retry logic for this page
        for attempt in range(max_retries):
            try:
                data = GITHUB_API.run_graphQL_query(QUERY if simple_labels else QUERY_TL, variables)
                rates = data["rateLimit"]
                pagenum = count / page_size
                if should_print: print(f"{owner}/{repo}:{'.' * int(pagenum)}")
                break  # Success

            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError) as e:
                # Network-level errors
                if attempt == max_retries - 1:
                    logging.error(f"Failed after {max_retries} attempts: {e}")
                    raise

                backoff = initial_backoff * (2 ** attempt) + random.uniform(0, 1)
                logging.info(f"Network error (attempt {attempt + 1}/{max_retries}): {e}")
                logging.info(f"Retrying in {backoff:.1f} seconds...")
                time.sleep(backoff)

            except requests.exceptions.HTTPError as e:
                # HTTP errors (502, 504, etc.)
                if e.response is not None and e.response.status_code in [502, 504]:
                    if attempt == max_retries - 1:
                        logging.error(f"Failed after {max_retries} attempts: {e}")
                        raise

                    backoff = initial_backoff * (2 ** attempt) + random.uniform(0, 1)
                    logging.info(f"Gateway error {e.response.status_code} (attempt {attempt + 1}/{max_retries})")
                    logging.info(f"Retrying in {backoff:.1f} seconds...")
                    time.sleep(backoff)
                else:
                    # Other HTTP errors - don't retry
                    raise

        pr_connection = data["repository"]["pullRequests"]

        for pr in pr_connection["nodes"]:
            if pr['updatedAt'] < since_date:
                printrates(rates)
                return
            if max_results and count >= max_results:
                printrates(rates)
                return
            yield pr
            count += 1

        if not pr_connection["pageInfo"]["hasNextPage"]:
            # Print rate limit info
            printrates(rates)
            #print(f"[rateLimit] cost={rl['cost']}, remaining={rl['remaining']}, resetAt={rl['resetAt']}")
            break
        cursor = pr_connection["pageInfo"]["endCursor"]

def fetch_prs_search(owner: str, repo: str, since_date: str, until_date: str = None, page_size: int = 50, max_results: int = 5000):
    """
    Fetch PRs using GitHub's search API which supports date filtering.
    Dates should be in format: YYYY-MM-DD
    """
    # Build search query
    search_parts = [
        f"repo:{owner}/{repo}",
        "is:pr",
        f"updated:>={since_date[:10]}"  # Extract just YYYY-MM-DD
    ]

    if until_date:
        search_parts.append(f"updated:<={until_date[:10]}")

    search_query = " ".join(search_parts)

    cursor = None
    count = 0

    while True:
        variables = {
            "searchQuery": search_query,
            "pageSize": page_size,
            "cursor": cursor
        }

        data = GITHUB_API.run_graphQL_query(SEARCH_QUERY, variables)
        search_results = data["search"]

        for pr in search_results["nodes"]:
            if max_results and count >= max_results:
                return
            yield pr
            count += 1

        if not search_results["pageInfo"]["hasNextPage"]:
            break
        cursor = search_results["pageInfo"]["endCursor"]


def fetch_prs_in_chunks(owner, repo, start_date, end_date, chunk_months=3, max_results=None):
    # Handle both string and datetime inputs
    if isinstance(start_date, str):
        current = datetime.datetime.fromisoformat(start_date.replace('Z', '+00:00'))
    else:
        current = start_date

    if isinstance(end_date, str):
        end = datetime.datetime.fromisoformat(end_date.replace('Z', '+00:00'))
    else:
        end = end_date

    while current < end:
        next_date = current + datetime.timedelta(days=chunk_months*30)
        if next_date > end:
            next_date = end

        print(f"Fetching PRs from {current.date()} to {next_date.date()}")
        for pr in fetch_prs_search(owner, repo,
                                   since_date=current.isoformat(),
                                   until_date=next_date.isoformat(),
                                   max_results=max_results):
            yield pr

        current = next_date

def build_commit(data: dict[str,str], pr_number: int) -> PRCommit:
    commit = data["commit"]
    author = commit.get("author", {})

    # The 'user' object can sometimes be null in the API response even if 'author' exists.
    # We need to handle this case safely to prevent an AttributeError.
    user = author.get("user") if author else None
    login = user.get("login") if user else "no GH user"

    name =  author.get("name") if author else ""
    email =  author.get("email") if author else ""
    date = commit.get("committedDate", "")
    message = commit.get("message", "")
    sha = commit.get("oid","")
    auth = Author(login=login, name=name, email=email)
    return PRCommit(sha=sha, author=auth, co_authors=[], date=date, message=message, pr_number=pr_number)


def get_pull_requests(url: str, since_date: str) -> list[PullRequest]:
    owner, repo = parse_github_url(url)
    prs = []
    for pr in fetch_prs_retries(owner, repo, since_date, page_size=25, max_results=10000):
        pr_number = int(pr["number"])
        pr_url = f"https://github.com/{owner}/{repo}/pull/{pr_number}"
        pr_title = pr["title"]
        pr_body = pr["bodyText"]
        pr_branch_name = pr["headRefName"]
        pr_updated = pr["updatedAt"]
        pr_merged_at = pr["mergedAt"]
        merge_commit = pr.get("mergeCommit")
        pr_merge_commit_sha = merge_commit["oid"] if merge_commit else ""  # safe handling
        pr_add = pr.get("additions", 0)
        pr_del = pr.get("deletions", 0)
        author = pr.get("author")
        pr_author_login = author.get("login") if author else ""
        pr_author_name = author.get("name") if author else ""
        pr_files = pr.get("changedFiles", 0)
        pr_commits = [build_commit(data, pr_number) for data in pr["commits"]["nodes"]]
        if simple_labels:
            pr_labels = [n["name"] for n in pr["labels"]["nodes"]]
        else:
            pr_labels = list(dict.fromkeys(
                [n["name"] for n in pr["labels"]["nodes"]] +
                [n["label"]["name"] for n in pr["timelineItems"]["nodes"] if n.get("label")]
            ))

        pr_author = Author(login=pr_author_login, name=pr_author_name, email="")

        pr = PullRequest(url=pr_url, number=pr_number, owner=owner, repo=repo, branch_name=pr_branch_name, title=pr_title, bodyText=pr_body, updated_at=pr_updated, \
            merged_at=pr_merged_at, merge_commit_sha=pr_merge_commit_sha, additions=pr_add, deletions=pr_del, changed_files=pr_files, \
            labels=pr_labels, commits=pr_commits, pr_author=pr_author)
        prs.append(pr)
    return prs


if __name__ == "__main__":
    owner = "liam-hq"
    #owner = "MontrealAI"
    repo = "liam"
    #repo = "AGI-Alpha-Agent-v0"
    since_date = "2025-09-01T00:00:00Z"
    github_url = f"https://github.com/{owner}/{repo}"
    csv_file = "github_prs.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "pr_number",
            "pr_author_login",
            "pr_url",
            "pr_title",
            "pr_updated_at",
            "pr_merged_at",
            "pr_merge_commit_sha",
            "pr_commits",
            "pr_label_1"
        ]

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for pr in get_pull_requests(github_url, since_date):

            writer.writerow({
                "pr_number": pr.number,
                "pr_author_login": pr.pr_author.login,
                "pr_url": pr.url,
                "pr_title": pr.title,
                "pr_updated_at": pr.updated_at,
                "pr_merged_at": pr.merged_at,
                "pr_merge_commit_sha": pr.merge_commit_sha,
                "pr_commits": len(pr.commits),
                "pr_label_1": pr.labels[0] if pr.labels else ""
            })

    print(f"Data written to {csv_file}")

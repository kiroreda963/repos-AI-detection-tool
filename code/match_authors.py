import re
import difflib
from functools import lru_cache

from utils.data import Author
from utils import logging

##
# we could only compare authors of "similar" types?
# not bot with human?
# but: we miss liam-claude-code-action
##

def normalize_email(email: str) -> str:
    if not email:
        return ""
    email = email.lower().strip()
    # remove GitHub noreply noise
    #email = re.sub(r"\+.*?@", "@", email)  # strip +tags
    email = email.replace("@users.noreply.github.com", "")
    return email

def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower()) if name else ""

def normalize_login(login: str) -> str:
    return re.sub(r"\[bot\]", "", login.lower()) if login else ""

@lru_cache(maxsize=1024)
def author_similarity(a: Author, b: Author) -> float:
    score, weight = 0, 0
    if a.login.lower() == b.login.lower():
        return 1
    # login exact
    if a.login and b.login:
        weight += 1
        ratio = difflib.SequenceMatcher(None, normalize_login(a.login), normalize_login(b.login)).ratio()
        score += ratio

    # email
    ea, eb = normalize_email(a.email), normalize_email(b.email)
    if ea and eb:
        weight += 1
        if ea == eb:
            score += 1
        elif ea.split("@")[0] == eb.split("@")[0]:  # local-part match
            score += 0.8

    # name fuzzy
    na, nb = normalize_name(a.name), normalize_name(b.name)
    if na and nb:
        weight += 1
        ratio = difflib.SequenceMatcher(None, na, nb).ratio()
        score += ratio

    sim = score / weight if weight else 0.0
    if sim < 0.6 and sim > 0.2:
        logging.info(f"matching {a} with: {b}, similarity is {sim}")
    return sim


class AuthorMatcher:
    def __init__(self, threshold: float):
        self.threshold: float = threshold

    def authors_match(self, a: Author, b: Author) -> bool:
        a.ensure()
        b.ensure()
        sim = author_similarity(a, b)
        return sim >= self.threshold


    def author_in_list(self, target_author: Author, author_list: list[Author]) -> bool:
        """Check if an author is in a list using fuzzy matching."""
        return any(self.authors_match(target_author, author) for author in author_list)



class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: int, y: int):
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[ry] = rx


def deduplicate_authors(authors: list[Author], matcher: AuthorMatcher) -> set[Author]:
    n = len(authors)
    uf = UnionFind(n)

    # O(n^2) matching – okay for small/medium sets
    for i in range(n):
        for j in range(i + 1, n):
            if matcher.authors_match(authors[i], authors[j]):
                uf.union(i, j)

    # Pick one representative per group
    groups: dict[int, Author] = {}
    for i, a in enumerate(authors):
        root = uf.find(i)
        if root not in groups:
            groups[root] = a  # pick first as canonical

    return set(groups.values())

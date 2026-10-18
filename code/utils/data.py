from dataclasses import dataclass, fields
from datetime import datetime

@dataclass
class Author:
    login: str
    name: str
    email: str

    def __str__(self):
        return f"{self.login} | {self.name} <{self.email}>"

    def ensure(self):
        if self.login is None: self.login = ""
        if self.name is None: self.name = ""
        if self.email is None: self.email = ""


    def __hash__(self):
        # hash on normalized identity fields
        return hash(self.__str__())

    def __eq__(self, other):
        if not isinstance(other, Author):
            return False
        return (self.__str__() == other.__str__())

@dataclass
class Commit:
    sha: str
    author: Author
    co_authors: list[Author]
    message: str
    date: datetime


@dataclass
class LogCommit(Commit):
    parents: list[str]
    in_main_branch: bool

    def is_log_merge(self) -> bool: return len(self.parents) > 1
    def is_orphan(self) -> bool: return len(self.parents) == 0

@dataclass
class PRCommit(Commit):
    pr_number: int



@dataclass
class BasePullRequest:
    url: str
    number: int
    owner: str
    repo: str
    branch_name: str
    title: str
    bodyText: str
    updated_at: datetime
    merged_at: datetime
    merge_commit_sha: str
    additions: int
    deletions: int
    changed_files: int
    labels: list[str]
    pr_author: Author

    @classmethod
    def from_subclass(cls, instance):
        """Create base class instance from any subclass instance"""
        base_field_names = {f.name for f in fields(cls)}
        base_data = {k: v for k, v in vars(instance).items() if k in base_field_names}
        return cls(**base_data)

@dataclass
class PullRequest(BasePullRequest):
    commits: list[PRCommit]

    @classmethod
    def from_superclass(cls, instance: BasePullRequest, commits: list[PRCommit]):
        base_field_names = {f.name for f in fields(BasePullRequest)}
        base_data = {k: v for k, v in vars(instance).items() if k in base_field_names}
        pr_commits = [cm for cm in commits if cm.pr_number == instance.number]
        return cls(commits = pr_commits, **base_data)

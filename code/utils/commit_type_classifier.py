import re


COMMIT_TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)

COMMIT_TYPE_PATTERN = re.compile(
    r"^\s*(?P<type>[A-Za-z]+)(?:\([^\n)]*\))?!?:"
)


def extract_commit_type(message: str) -> str:
    """Return the conventional-commit type from a commit message."""
    if not isinstance(message, str):
        return ""

    match = COMMIT_TYPE_PATTERN.match(message)
    if not match:
        return ""

    commit_type = match.group("type").lower()
    return commit_type if commit_type in COMMIT_TYPES else ""


def get_commit_type_categories() -> dict[str, list[str]]:
    """Return the categories used by the commit-size reports."""
    return {"types": list(COMMIT_TYPES), "all": [*COMMIT_TYPES, "none"]}
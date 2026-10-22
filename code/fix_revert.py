"""
Detects "fix" and "revert" commits from their message text alone - no API
calls, no clone. Three fix signals are kept separate (not merged into one
boolean) so precision vs. recall is a reporting choice, not a detection one.
"""
import re
from dataclasses import dataclass

# --- Revert: structural, near-zero false positives --------------------------

REVERT_TRAILER = re.compile(r"This reverts commit ([0-9a-f]{7,40})\.?", re.IGNORECASE)
REVERT_TITLE = re.compile(r'^\s*Revert\s+"(.+)"', re.IGNORECASE)
REVERT_STRICT_PREFIX = re.compile(r"^\s*revert(\([^)\n]*\))?!?:\s", re.IGNORECASE)

# --- Fix: no structural marker, so offer three signals ----------------------

FIX_STRICT_PREFIX = re.compile(r"^\s*fix(\([^)\n]*\))?!?:\s", re.IGNORECASE)
FIX_KEYWORD = re.compile(r"\b(fix(es|ed)?|hotfix|bug ?fix)\b", re.IGNORECASE)
FIX_ISSUE_LINKED = re.compile(
    r"\b(fix|fixes|fixed|close|closes|closed|resolve|resolves|resolved)\s+#\d+",
    re.IGNORECASE,
)


@dataclass
class CommitLabel:
    is_revert: bool
    revert_target_sha: str  # "" if is_revert but no structural target found
    is_fix_strict: bool
    is_fix_keyword: bool
    is_fix_issue_linked: bool

    @property
    def is_fix(self) -> bool:
        return self.is_fix_strict or self.is_fix_keyword or self.is_fix_issue_linked


def extract_revert_target_sha(message: str) -> str:
    """Pulls the target sha out of a `git revert` / GitHub-generated revert
    commit's trailer ("This reverts commit <sha>.")."""
    m = REVERT_TRAILER.search(message)
    return m.group(1) if m else ""


def _strip_quoted_revert_title(message: str) -> str:
    """A GitHub-generated revert's title quotes the ORIGINAL commit's title
    verbatim (`Revert "fix: ... fixes #9"`). That quoted text describes the
    commit being undone, not this commit, so it must not feed the fix
    detectors below - otherwise every revert of a fix is misread as itself
    being a fix."""
    m = REVERT_TITLE.match(message)
    return message[m.end():] if m else message


def classify_commit_label(message: str) -> CommitLabel:
    if not isinstance(message, str):
        message = ""

    revert_target = extract_revert_target_sha(message)
    is_revert = bool(
        revert_target or REVERT_TITLE.match(message) or REVERT_STRICT_PREFIX.match(message)
    )

    fix_text = _strip_quoted_revert_title(message) if is_revert else message

    return CommitLabel(
        is_revert=is_revert,
        revert_target_sha=revert_target,
        is_fix_strict=bool(FIX_STRICT_PREFIX.match(fix_text)),
        is_fix_keyword=bool(FIX_KEYWORD.search(fix_text)),
        is_fix_issue_linked=bool(FIX_ISSUE_LINKED.search(fix_text)),
    )

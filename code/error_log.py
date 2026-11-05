"""
Structured, persisted error log for the partial-clone pipeline - so failures
(blame timeouts, gateway errors, local git failures, etc.) can be reviewed
after a run instead of only scrolling through terminal output.

Not thread-local: fix/revert resolutions run inside their own
ThreadPoolExecutor (partial_analyzer.BLAME_WORKERS_PER_REPO), whose worker
threads are not the thread that started a given repo's analysis, so a
thread-local collector would silently miss everything logged from inside
that pool. A plain lock-guarded list, passed explicitly to every function
that can fail, works regardless of which thread actually raises.

Deliberately does NOT use `from __future__ import annotations` - ErrorLogEntry
is written to/read from CSV (utils/import_export.py), and that future import
turns every field's type into a plain string at runtime, breaking the
type-based round-trip for any non-string field.
"""

import threading
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ErrorLogEntry:
    github_url: str
    stage: str          # e.g. "file_manifest", "tool_file_history", "revision_count",
                         # "local_blame", "api_blame_fallback", "resolve_commit", "repo_analysis"
    context: str         # whatever identifies where it happened: sha, path, parent sha, etc.
    error_message: str
    timestamp: str


class ErrorCollector:
    def __init__(self, github_url: str):
        self.github_url = github_url
        self._lock = threading.Lock()
        self.entries: list[ErrorLogEntry] = []

    def record(self, stage: str, context: str, error: object) -> None:
        entry = ErrorLogEntry(
            github_url=self.github_url, stage=stage, context=context,
            error_message=str(error), timestamp=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self.entries.append(entry)

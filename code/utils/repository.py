# code/aie/git/repository.py
import os
import subprocess
from datetime import datetime
from utils import logging
from pathlib import Path

from .commit_hash import is_valid
from .data import LogCommit, Author
from .metrics import FileStats, CommitStats

class GitRepository:
    """A wrapper for a local Git repository."""

    def __init__(self, repo_path: Path):
        if not os.path.isdir(os.path.join(repo_path, '.git')):
            raise ValueError(f"{repo_path} is not a valid Git repository.")
        self.repo_path:Path = repo_path

    def run_command_err(self, command: list[str], check: bool = True) -> tuple[str,str]:
        """Runs a Git command and returns its stdout."""
        # print(f"DEBUG: git {' '.join(command)}")
        try:
            result = subprocess.run(
                ['git'] + command,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=check,
                encoding='utf-8',
                errors='ignore'
            )
            return result.stdout.strip(), result.stderr.strip()
        except subprocess.CalledProcessError as e:
            # Don't print for verify commands, they are expected to fail sometimes
            if '--verify' not in command and check:
                logging.error(f"Error running git command {' '.join(command)} in {self.repo_path}: {e.stderr.strip()}")
            else:
                logging.info(f"--verify Git command failed: {' '.join(command)} in {self.repo_path}: {e.stderr.strip()}")
            raise e
            #return "", ""

    def run_command(self, command: list[str], check: bool = True) -> str:
        return self.run_command_err(command, check)[0]

    def get_file_list(self):
        result = self.run_command(["ls-tree", "-r", "--name-only", "HEAD"])
        file_paths = result[0].split('\n') if result else []
        return file_paths


    def get_main_branch_name(self) -> str:
        """Determines the main branch name (e.g., main, master)."""
        remote_head = self.run_command(
            ['symbolic-ref', 'refs/remotes/origin/HEAD'], check=False)
        if remote_head:
            return remote_head.split('/')[-1].strip()

        for branch in ['main', 'master', 'develop']:
            # Check for local branch
            verify_local_cmd = ['show-ref', '--verify',
                                '--quiet', f'refs/heads/{branch}']
            if self.run_command(verify_local_cmd) != "":
                return branch
            # Check for remote branch
            verify_remote_cmd = ['show-ref', '--verify',
                                 '--quiet', f'refs/remotes/origin/{branch}']
            if self.run_command(verify_remote_cmd) != "":
                return branch
        return 'main'

    def get_branch_commits(self, branch_name: str, local_only:bool = True) -> set[str]:
        """Gets all commit hashes for a given branch."""
        # Try local then remote ref

        branch_names = [branch_name] if local_only else [branch_name, f'origin/{branch_name}']
        for ref in branch_names:
            commits_output = self.run_command(['rev-list', ref, '--'])
            if commits_output:
                return set(commits_output.strip().split('\n'))
        return set()

    def log_command(self, flags: list[str], include_merges: bool = False, start_date: datetime|None = None, end_date: datetime|None = None, all_branches: bool = True) -> list[str]:

        log_command = ['log', '--date=iso-strict']
        log_command.extend(flags)
        if all_branches:
            log_command.append('--all')

        if not include_merges:
            log_command.append('--no-merges')

        if start_date:
            log_command.append(f'--since={start_date.isoformat()}')
        if end_date:
            log_command.append(f'--until={end_date.isoformat()}')
        return log_command

    def get_commit_info(self,
        include_merges: bool = False,
        start_date: datetime|None = None,
        end_date: datetime|None = None,
        all_branches: bool = True,
        filter_orphan_commits: bool = False) -> list[LogCommit]:
        """
        Gets commit information.
        By default, it gets information from all branches.
        If all_branches is False, it gets information from the current branch only.
        """
        main_branch = self.get_main_branch_name()
        is_remote = len(self.run_command(["remote"]).strip()) > 0
        main_commits_output = self.run_command(['rev-list', f'origin/{main_branch}' if is_remote else main_branch, '--'])
        main_commits = set(main_commits_output.strip().split('\n')) if main_commits_output else set()

        ##
        # The issue here is with empty fields, we end up splitting on double null there
        # --pretty="format:%H %x00 %P %x00 %an %x00 %ae %x00 %ad %x00 %B %x00"
        # could work better??
        # old command:
        # "--pretty=format:%H%x00%P%x00%an%x00%ae%x00%ad%x00%B%x00",


        flags = [
            "--pretty=format:%H %x00 %P %x00 %an %x00 %ae %x00 %ad %x00 %B %x00",
            #"--date=iso-strict",
            "-z"
        ]
        log_command = self.log_command(flags, include_merges, start_date, end_date, all_branches)
        log_output = self.run_command(log_command)

        if not log_output:
            return []

        # Split on double null bytes first to separate commits
        commit_blocks = log_output.split('\0\0')
        commits: list[LogCommit] = []
        seen_hashes: set[str] = set()

        for block in commit_blocks:
            if not block:  # Skip empty blocks
                logging.warning("Commit Log Warning: found an empty commit block")
                continue

            # Split each commit block on single null bytes
            fields = block.split('\0')
            # remove extra spaces
            fields = [field.strip() for field in fields]

            # We expect exactly 6 fields per commit, but handle gracefully
            if len(fields) < 6:
                logging.warning(f"Commit Log Warning: Incomplete commit data, got {len(fields)} fields: {fields[:2]}")
                continue

            commit_hash, parents, author, email, date_str, message = fields[:6]

            #print(f"Processing hash: {commit_hash}")

            if not is_valid(commit_hash):
                logging.warning(f"Commit Log Warning: Invalid hash: {commit_hash}")
                continue

            if commit_hash in seen_hashes:
                # somehow we have seen this hash
                logging.warning(f"Commit Log Warning: Already seen this hash: {commit_hash}")
                continue

            auth = Author(login="", name=author, email=email)
            commit_parents = parents.split() if parents else []
            in_main = commit_hash in main_commits
            date = datetime.fromisoformat(date_str)
            # co_authors populated later
            commit = LogCommit(sha=commit_hash, author=auth, co_authors=[], message=message, date=date, \
                parents=commit_parents, in_main_branch=in_main)
            commits.append(commit)
            seen_hashes.add(commit_hash)

        if filter_orphan_commits:
            commits = [c for c in commits if c.parents]

        return commits

    def handle_rename(self, added: int, deleted: int, raw_path: str) -> FileStats:
        """
        Handle git numstat rename/move paths like:
          old_name => new_name
          {packages => plugins}/mock/.gitignore
        Returns a FileStats object with old_path and new_path set.
        """
        old_path, new_path = "", ""

        # Case 1: Simple "old => new" rename
        if " => " in raw_path and "{" not in raw_path:
            parts = raw_path.split(" => ")  # Added space for consistency
            old_path = parts[0].strip()
            new_path = parts[1].strip()

        # Case 2: Braced rename/move: "{old => new}/suffix" or "prefix/{old => new}/suffix"
        elif "{" in raw_path and " => " in raw_path:
            # Split at the braces
            prefix, rest = raw_path.split("{", 1)
            middle, suffix = rest.split("}", 1)

            if " => " in middle:
                old_mid, new_mid = [p.strip() for p in middle.split(" => ")]
                # Build the full paths
                old_path = prefix + old_mid + suffix
                new_path = prefix + new_mid + suffix
            else:
                logging.warning(f"Malformed brace rename format: {raw_path}")
                new_path = raw_path

        else:
            logging.warning(f"Unrecognized rename format: {raw_path}")
            new_path = raw_path  # Fallback so we don't lose data

        return FileStats(
            path=new_path,
            type="renamed",
            added_lines=added,
            deleted_lines=deleted,
            old_path=old_path
        )


    def get_commit_stats(self, include_merges: bool=False, start_date: datetime|None=None, end_date:datetime|None=None, all_branches:bool=True, filter_orphan_commits:bool=True) -> list[CommitStats]:
        """
        Two-pass approach for robust commit parsing:
        1. First pass: Get commit info using get_commit_info_nul
        2. Second pass: Get only numstat data and match with pre-loaded commit info
        """

        # PASS 1: Get commit info using existing method
        commits: list[LogCommit] = self.get_commit_info(include_merges, start_date, end_date, all_branches)
        # Create lookup dictionary by hash
        commit_lookup = {commit.sha.strip(): commit for commit in commits}
        logging.info(f"found {len(commits)} commits via get_commit_info")

        # PASS 2: Get only numstat data
        flags = ['--pretty=format:%x00%H%n', '--numstat']
        #flags = ['--pretty=format:%H%x00', '--numstat', '-z']
        stats_command = self.log_command(flags, include_merges, start_date, end_date, all_branches)
        log_output = self.run_command(stats_command)

        # Split on double null bytes first to separate commits
        commit_blocks = log_output.split('\0')
        commit_stats: list[CommitStats] = []

        for block in commit_blocks:
            if not block:  # Skip empty blocks
                logging.warning("Commit Log Warning: found an empty commit block")
                continue

            # Split each commit block on single null bytes
            hash, *stats = block.split('\n')
            current_commit_stats = CommitStats(
                commit=commit_lookup.get(hash.strip()),
                file_stats=[],
                type="unclassified"
            )

            if current_commit_stats.commit is None:
                logging.warning(f"Commit not found {hash}")
            for stat_line in stats:
                parts = stat_line.split('\t')
                if len(parts) >= 3:
                    added_str, deleted_str = parts[0], parts[1]
                    file_path = '\t'.join(parts[2:])  # Handle filenames with tabs

                    # Skip binary files or other special cases
                    if added_str != "-" and deleted_str != "-":
                        added = int(added_str) if added_str.isdigit() else 0
                        deleted = int(deleted_str) if deleted_str.isdigit() else 0

                        if "=>" in file_path:
                            #logging.info(f"handling a rename for line: {stat_line}")
                            file_stats = self.handle_rename(added, deleted, file_path)
                        else:

                            change_type = "modified"
                            if added > 0 and deleted == 0:
                                change_type = "added"
                            elif deleted > 0 and added == 0:
                                change_type = "deleted"

                            file_stats = FileStats(
                                path=file_path,
                                type=change_type,
                                added_lines=added,
                                deleted_lines=deleted
                            )
                        #logging.info(f"Correct format for numstat line: {stat_line}")
                        current_commit_stats.add_file(file_stats)
                else:
                    if stat_line:
                        logging.info(f"Incorrect format for numstat line: ___{stat_line}___")
            commit_stats.append(current_commit_stats)

        if filter_orphan_commits:
            commit_stats = [c for c in commit_stats if c.commit.parents]
        return commit_stats


    def get_github_url(self) -> str:
        """Gets the GitHub URL of the repository."""
        remote_url = self.run_command(['remote', 'get-url', 'origin'])
        if not remote_url:
            return ""

        if remote_url.startswith('git@github.com:'):
            repo_part = remote_url.replace(
                'git@github.com:', '').replace('.git', '')
            return f"https://github.com/{repo_part}"

        if 'github.com' in remote_url:
            url = remote_url.replace('.git', '')
            if '@github.com' in url:
                parts = url.split('@github.com')
                if len(parts) == 2:
                    return f"https://github.com{parts[1]}"
            return url
        return ""

    def checkout_file(self, file_path: str):
        """Checks out a specific file from the index."""
        _ =self.run_command(['checkout', '--', file_path])

    def populate_index(self):
        """Populates the index with HEAD for file operations."""
        _ = self.run_command(['read-tree', 'HEAD'])

    def get_file_history_metrics(self, file_paths: list[str]) -> dict[str, dict[str, int]]:
        """
        Gets add timestamp and modification count for files in the repository.
        If file_paths is provided, metrics are returned only for those files,
        processed in chunks to avoid "argument list too long" errors.
        Otherwise, it processes all files in the repository.

        Args:
            file_paths: An optional list of file paths to restrict the search to.

        Returns:
            A dictionary where keys are file paths and values are dictionaries
            with 'added_timestamp' and 'modifications' keys.
        """
        all_file_metrics: dict[str, dict[str, int]] = {}

        # Initialize metrics for all requested files
        if file_paths:
            for path in file_paths:
                all_file_metrics[path] = {
                    'added_timestamp': None, 'modifications': 0, 'last_mod': 0}

        base_log_command = [
            'log', '--all', '--pretty=format:"%H %ct"', '--name-status', '--no-merges', '--no-renames',
        ]

        def process_log_output(log_output: str, metrics_dict: dict[str, dict[str, int]]):
            lines = log_output.splitlines()
            current_commit_hash = None
            current_commit_timestamp = None
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('"') and line.endswith('"'):
                    parts = line.strip('"').split(' ')
                    if len(parts) == 2:
                        current_commit_hash, current_commit_timestamp = parts[0], int(
                            parts[1])
                    continue
                if current_commit_hash and current_commit_timestamp:
                    parts = line.split('\t', 1)
                    if len(parts) == 2:
                        status, file_path = parts[0], parts[1]
                        if file_path in metrics_dict:
                            if status == 'A' and metrics_dict[file_path]['added_timestamp'] is None:
                                metrics_dict[file_path]['added_timestamp'] = current_commit_timestamp
                            metrics_dict[file_path]['modifications'] += 1
                            if metrics_dict[file_path]['last_mod'] < current_commit_timestamp:
                                metrics_dict[file_path]['last_mod'] = current_commit_timestamp

        if not file_paths:
            # Process the entire repository
            log_output = self.run_command(base_log_command)
            if log_output:
                # First pass to discover all files and initialize metrics
                temp_metrics = {}
                process_log_output(log_output, temp_metrics)
                all_file_metrics = temp_metrics
        else:
            # Process in chunks to avoid arg length limits
            CHUNK_SIZE = 500
            for i in range(0, len(file_paths), CHUNK_SIZE):
                chunk = file_paths[i:i + CHUNK_SIZE]
                log_command = base_log_command + ['--'] + chunk
                log_output = self.run_command(log_command)
                if log_output:
                    process_log_output(log_output, all_file_metrics)

        # Adjust modification count for all processed files
        for file_path, metrics in all_file_metrics.items():
            if metrics['added_timestamp'] is not None and metrics['modifications'] > 0:
                all_file_metrics[file_path]['modifications'] = max(
                    0, metrics['modifications'] - 1)

        return all_file_metrics

    def get_file_content_from_blob(self, file_path: str) -> str:
        """Gets the content of a file directly from the Git index (blob)."""
        # Use git show :<path> to get content from the index
        # This assumes populate_index() has been called.
        try:
            content = self.run_command(['show', f':{file_path}'], check=False)
            return content if content else ""
        except Exception as e:
            logging.error(f"Error getting file content for {file_path}: {e}")
            raise e


    def set_remote_url(self, remote_name: str, url: str):
        """Sets the URL for a given remote."""
        _ = self.run_command(['remote', 'set-url', remote_name, url])

    def fetch(self, remote_name: str = 'origin', prune: bool = True):
        """Fetches from a remote."""
        command = ['fetch', remote_name]
        if prune:
            command.append('--prune')
        _ = self.run_command(command)

    def pull(self, remote_name: str = 'origin', branch_name: str|None = None):
        """Pulls changes from a remote."""
        if not branch_name:
            branch_name = self.get_main_branch_name()
        _ = self.run_command(['pull', remote_name, branch_name])

    def get_raw_diff_tree(self, commit_hash: str, parent_hash: str|None = None) -> str:
        """
        Gets the raw diff-tree output for a commit.

        Args:
            commit_hash: The hash of the commit to diff.
            parent_hash: The parent to diff against. If None, it's a root commit.

        Returns:
            The raw string output of the git diff-tree command.
        """
        command = ['diff-tree', '--patch', '--no-commit-id', '-r']
        if parent_hash:
            command.extend([parent_hash, commit_hash])
        else:
            # This is a root commit
            command.extend(['--root', commit_hash])

        return self.run_command(command)

    def deepen_shallow_history(self) -> str:
        """
        If the repository is a shallow clone, this fetches the immediate parent
        for all commits that currently have no parents, effectively "deepening"
        the history by one level. This is crucial for fixing diffs on the
        earliest commits in a shallow clone.

        Returns:
            A string indicating the result: 'DEEPENED', 'NOT_SHALLOW', or 'DEEPEN_FAILED'.
        """
        shallow_file = os.path.join(self.repo_path, '.git', 'shallow')
        if os.path.exists(shallow_file):
            print(f"Deepening shallow repository at {self.repo_path}...")
            try:
                output, err = self.run_command_err(['git', 'fetch', '--deepen=1'], check=True)
                # Check stderr for "unshallowing" message from Git
                if 'unshallowing' in err.lower():
                    logging.info("Deepening complete.")
                    return 'DEEPENED'
                else:
                    # This can happen if the repo is shallow but already has full history
                    logging.info("Skipping deepening: Repository is shallow but no new commits were fetched.")
                    return 'NOT_SHALLOW'
            except subprocess.CalledProcessError as e:
                logging.error(f"Error deepening repository at {self.repo_path}: {e.stderr.strip()}")
                return 'DEEPEN_FAILED'
        else:
            return 'NOT_SHALLOW'

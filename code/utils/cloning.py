# code/aie/utils/cloning.py
import os
import stat
import subprocess
import time
import random
import shutil
from utils import logging
from typing import List
from urllib.parse import urlparse
from pathlib import Path

from .repository import GitRepository
from .github import get_github_tokens, get_repo_name_from_url

def _inject_pat(url: str, token: str) -> str:
    """Injects a personal access token into a GitHub URL."""
    if not token:
        return url
    parsed = urlparse(url.strip())
    if "github.com" in parsed.netloc:
        # Ensure the path is included correctly
        return f"https://{token}@{parsed.netloc}{parsed.path}"
    return url

def _clear_readonly(func, path, exc_info):
    """onerror/onexc handler for shutil.rmtree: clear read-only bit and retry once."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def move_directory_robust(src: Path, dst: Path, max_retries: int = 5, retry_delay: float = 1.0) -> None:
    """
    Moves a directory, retrying on transient Windows file locks.

    Git pack files (.idx/.pack) are often briefly held open by antivirus/indexer
    scans right after a clone, which makes shutil.move fail with
    "[WinError 5] Access is denied" even though the lock clears a moment later.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            shutil.move(str(src), str(dst))
            return
        except OSError as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (2 ** attempt))
    raise last_error


def clone_repo(repo_url: str, dest_path: Path, token: str = None, shallow: bool = False, shallow_since: str = None, partial: bool = False, no_checkout: bool = False, max_retries: int = 3, retry_delay: int = 5, timeout: int = 600) -> bool:
    """
    Clones a repository with various strategies and robust retry logic.
    If no token is provided, it will attempt to load one automatically.

    Args:
        repo_url: The URL of the repository to clone.
        dest_path: The local directory to clone into.
        shallow: If True, performs a shallow clone (--depth 1).
        shallow_since: If set to a date (e.g., "YYYY-MM-DD"), performs a shallow
                       clone of commits since that date. This takes precedence over `shallow`.
        partial: If True, performs a partial clone (--filter=blob:none).
        no_checkout: If True, performs a clone with --no-checkout.
        max_retries: Number of times to retry on failure.
        retry_delay: Initial delay in seconds for retries.
        timeout: Timeout in seconds for the git clone command.

    Returns:
        True if the clone was successful, False otherwise.
    """
    if os.path.exists(dest_path):
        logging.info(f"⚠️  Skipping clone: {dest_path} already exists.")
        return True  # Consider existing as a success

    # If no token is provided, try to get one from the central pool
    if not token:
        available_tokens = get_github_tokens()
        if available_tokens:
            token = available_tokens[0] # Use the first available token
        else:
            logging.warning("Warning: No GitHub token provided or found. Cloning public repos only.")

    repo_url_with_auth = _inject_pat(repo_url, token)

    clone_command = ['git', 'clone']
    if shallow_since:
        clone_command.extend(['--shallow-since', shallow_since])
    elif shallow:
        clone_command.extend(['--depth', '1'])

    if partial:
        clone_command.append('--filter=blob:none')
    if no_checkout:
        clone_command.append('--no-checkout')

    clone_command.extend([repo_url_with_auth, dest_path])

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                logging.info(f"🔄 Retrying '{repo_url}' (attempt {attempt + 1}/{max_retries})...")
            else:
                logging.info(f"🔄 Cloning '{repo_url}'...")

            result = subprocess.run(
                clone_command,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='ignore'
            )

            if result.returncode == 0:
                logging.info(f"✅ Successfully cloned {repo_url} to {dest_path}")
                return True
            else:
                error_msg = result.stderr.strip().lower()
                if any(phrase in error_msg for phrase in ['rate limit', 'too many requests', '429', 'api rate limit']):
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logging.warning(f"⏱️  Rate limited, waiting {wait_time} seconds before retry...")
                        time.sleep(wait_time)
                        continue
                    else:
                        logging.error(f"❌ Rate limited and max retries reached for {repo_url}")
                        return False

                logging.error(f"❌ Failed to clone {repo_url}: {result.stderr.strip()}")
                return False

        except subprocess.TimeoutExpired:
            logging.warning(f"⏰ Timeout while cloning {repo_url}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (2 ** attempt))
                continue
            return False
        except Exception as e:
            logging.error(f"❌ An unexpected error occurred while cloning {repo_url}: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (2 ** attempt))
                continue
            return False

    return False

def get_file_list_by_clone(repo_url: str, token: str = None, timeout: int = 600) -> List[str]:
    """
    Gets a repository's file list by performing a temporary shallow clone.
    This is intended as a fallback for when the GitHub API is not viable (e.g., for very large repositories).

    Args:
        repo_url: The URL of the repository.
        token: Optional GitHub personal access token.
        timeout: Timeout in seconds for the git clone command.

    Returns:
        A list of file paths, or None if the operation fails.
    """
    repo_name = get_repo_name_from_url(repo_url)
    if not repo_name:
        # Fallback for non-standard URLs, though less ideal
        repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")

    temp_clone_dir = f"temp_clone_{repo_name}_{random.randint(1000, 9999)}"

    logging.info(f"Attempting to get file list for '{repo_url}' via shallow clone...")

    try:
        # Perform a shallow clone with no checkout into the temporary directory
        clone_successful = clone_repo(
            repo_url,
            temp_clone_dir,
            token=token,
            shallow=True,
            no_checkout=True,
            timeout=timeout
        )

        if not clone_successful:
            logging.error("Failed to clone repository for file listing.")
            raise RuntimeError("Failed to clone repository for file listing.")

        repo = GitRepository(temp_clone_dir)
        file_paths = repo.get_file_list()
        logging.info(f"✅ Successfully generated file list for {repo_url} via clone.")
        return file_paths

    except (subprocess.CalledProcessError, RuntimeError) as e:
        logging.error(f"❌ Failed to get file list for {repo_url} via clone: {e}")
        return None
    finally:
        # Clean up the temporary directory
        if os.path.exists(temp_clone_dir):
            shutil.rmtree(temp_clone_dir, ignore_errors=True)


def update_repo(repo_path: Path, repo_url: str, token: str = None):
    """
    Pulls the latest changes for an existing repository.

    Args:
        repo_path: The local path to the repository.
        repo_url: The canonical URL of the repository (for auth injection).
        token: Optional GitHub personal access token.
    """
    if not os.path.isdir(os.path.join(repo_path, '.git')):
        logging.error(f"Skipping update: {repo_path} is not a valid Git repository.")
        return

    logging.info(f"Updating repository: {repo_path}")
    try:
        repo = GitRepository(repo_path)

        # Update remote URL with auth token to ensure access
        if token:
            authed_url = _inject_pat(repo_url, token)
            repo.set_remote_url('origin', authed_url)

        # Fetch and pull
        repo.fetch('origin')
        repo.pull('origin')

        logging.info(f"Successfully updated {repo_path}")

    except Exception as e:
        logging.error(f"Error updating {repo_path}: {e}")

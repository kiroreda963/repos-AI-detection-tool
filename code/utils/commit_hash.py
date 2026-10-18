# code/aie/git/commit_hash.py
import re

# factored out of repository.py since the regex somehow confuses Gemini or the cli


commit_hash_regex = re.compile(r'^[a-f0-9]{40}$')


def is_valid(commit_hash: str) -> bool:
    """
    Check if the input string looks like a valid commit hash (SHA-1).

    Args:
        commit_hash (str): The string to validate.

    Returns:
        bool: True if the string matches the commit hash format, False otherwise.
    """
    return bool(commit_hash_regex.match(commit_hash))

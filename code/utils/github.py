# code/aie/api/github.py
import os
import configparser
import requests
import time
import threading
from collections import deque
from typing import List, Dict, Optional, Union
import base64
from datetime import datetime
from urllib.parse import urlparse


def get_github_tokens() -> List[str]:
    """
    Retrieves GitHub tokens from an environment variable or a config file.
    The primary source is the AIE_GITHUB_TOKENS environment variable, which
    should contain a comma-separated list of tokens.
    If the environment variable is not set, it falls back to 'config/tokens.ini'.
    """
    # 1. Try environment variable first
    env_tokens = os.environ.get("AIE_GITHUB_TOKENS")
    if env_tokens:
        tokens = [token.strip() for token in env_tokens.split(',')]
        if tokens:
            print("Loaded GitHub tokens from AIE_GITHUB_TOKENS environment variable.")
            return tokens

    # 2. Fallback to config file
    config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'tokens.ini')
    if not os.path.exists(config_path):
        print("Warning: No AIE_GITHUB_TOKENS env var and config/tokens.ini not found.")
        return []

    config = configparser.ConfigParser()
    try:
        config.read(config_path)
        tokens_str = config.get('github', 'tokens', fallback='')
        if tokens_str:
            tokens = [token.strip() for token in tokens_str.strip().split('\n') if token.strip()]
            if tokens:
                #print(f"Loaded {len(tokens)} GitHub tokens from {config_path}.")
                return tokens
    except Exception as e:
        print(f"Error reading tokens from {config_path}: {e}")

    return []


def parse_github_url(url: str):
    """Extracts owner and repo from a GitHub URL."""
    if not isinstance(url, str):
        return None, None
    parsed = urlparse(url)
    if "github.com" not in parsed.netloc:
        return None, None
    path_parts = parsed.path.strip('/').split('/')
    if len(path_parts) < 2:
        return None, None
    owner = path_parts[0]
    repo = path_parts[1].replace('.git', '')
    return owner, repo

def get_repo_name_from_url(url: str) -> str:
    """Extracts a unique, file-system-friendly name from a GitHub URL."""
    try:
        owner, repo = parse_github_url(url)
        return f'{owner}-{repo}' if owner and repo else None
    except Exception:
        return None



class RepositoryTooLargeError(Exception):
    """Custom exception for when a repository is too large for the API."""
    pass

class TokenPool:
    """Manages a pool of GitHub tokens to distribute API requests and handle rate limits."""
    def __init__(self, tokens: Optional[List[str]] = None):
            if tokens is None:
                tokens = get_github_tokens()

            self.tokens = deque(tokens)
            self.lock = threading.Lock()

            # Separate rate limit tracking for REST and GraphQL
            self.rest_rate_limits = {
                token: {'remaining': 5000, 'reset_time': time.time() + 3600}
                for token in tokens
            }
            self.graphql_rate_limits = {
                token: {'remaining': 5000, 'reset_time': time.time() + 3600}
                for token in tokens
            }

            # Track updates for each API type
            self.rest_updated_in_this_process = {}
            self.graphql_updated_in_this_process = {}

    def get_token(self, api_type: str = 'rest') -> Optional[str]:
            """Gets the best available token for the specified API type."""
            with self.lock:
                if not self.tokens:
                    return None

                # Choose the appropriate rate limit dict
                rate_limits = (self.rest_rate_limits if api_type == 'rest'
                                else self.graphql_rate_limits)

                current_time = time.time()
                available_tokens = []

                # Find all available tokens (not rate-limited)
                for token in self.tokens:
                    reset_time = rate_limits.get(token, {}).get('reset_time', 0)
                    remaining = rate_limits.get(token, {}).get('remaining', 1)

                    # Token is available if reset time passed OR has requests remaining
                    if current_time > reset_time or remaining > 50:
                        available_tokens.append((token, remaining))

                if available_tokens:
                    # Pick token with most remaining quota (better distribution)
                    best_token = max(available_tokens, key=lambda x: x[1])[0]
                    return best_token

                # All tokens are rate-limited for this API type
                best_token = min(rate_limits, key=lambda t: rate_limits[t]['reset_time'])
                reset_timestamp = rate_limits[best_token]['reset_time']
                wait_time = reset_timestamp - time.time()

                if wait_time > 0:
                    api_name = "GraphQL" if api_type == 'graphql' else "REST"
                    wait_minutes = wait_time / 60
                    if wait_minutes >= 1:
                        print(f"All tokens are {api_name} rate-limited. Waiting for {wait_minutes:.1f} minutes...")
                    else:
                        print(f"All tokens are {api_name} rate-limited. Waiting for {wait_time:.1f}s...")

                    while time.time() < reset_timestamp:
                        remaining_seconds = reset_timestamp - time.time()
                        if remaining_seconds <= 0:
                            break
                        sleep_duration = min(remaining_seconds, 60)
                        time.sleep(sleep_duration)

                        if wait_minutes >= 1:
                            new_remaining_minutes = (reset_timestamp - time.time()) / 60
                            if new_remaining_minutes > 0:
                                print(f"Still waiting... {new_remaining_minutes:.1f} minutes remaining.")

                    time.sleep(1)
                return best_token

    def update_rate_limit(self, token: str, response_headers: dict, api_type: str = 'rest'):
        """Updates rate limit information for a token from response headers."""
        with self.lock:
            if 'X-RateLimit-Remaining' in response_headers:
                new_limit = {
                    'remaining': int(response_headers['X-RateLimit-Remaining']),
                    'reset_time': int(response_headers['X-RateLimit-Reset'])
                }

                if api_type == 'rest':
                    self.rest_rate_limits[token] = new_limit
                    self.rest_updated_in_this_process[token] = new_limit
                else:  # graphql
                    self.graphql_rate_limits[token] = new_limit
                    self.graphql_updated_in_this_process[token] = new_limit

class GitHubAPI:
    """A client for interacting with the GitHub API, with token pooling and rate limit handling."""

    def __init__(self, tokens: Optional[List[str]] = None):
        self.base_url = "https://api.github.com"
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'AIE-Refactoring-Script'
        })
        self.token_pool = TokenPool(tokens)

    def get_updated_rate_limits(self) -> Dict[str, Dict]:
        """Returns only the rate limit information that has been updated by the API."""
        return self.token_pool.updated_in_this_process

    def print_rate_limit_status(self):
            """Prints rate limit status for both API types."""
            print("\n📊 Rate Limit Status:")
            if not self.token_pool.tokens:
                print("  - No tokens loaded.")
                return

            for token in self.token_pool.tokens:
                token_short = f"{token[:8]}...{token[-4:]}"

                # REST limits
                rest_limits = self.token_pool.rest_rate_limits.get(token, {})
                rest_remaining = rest_limits.get('remaining', 'N/A')
                rest_reset = rest_limits.get('reset_time', 0)
                rest_reset_str = datetime.fromtimestamp(rest_reset).strftime('%H:%M:%S') if rest_reset else 'N/A'

                # GraphQL limits
                gql_limits = self.token_pool.graphql_rate_limits.get(token, {})
                gql_remaining = gql_limits.get('remaining', 'N/A')
                gql_reset = gql_limits.get('reset_time', 0)
                gql_reset_str = datetime.fromtimestamp(gql_reset).strftime('%H:%M:%S') if gql_reset else 'N/A'

                print(f"  - Token {token_short}:")
                print(f"    REST remaining: {rest_remaining}        resets: {rest_reset_str}")
                print(f"    GraphQL remaining: {gql_remaining}        resets: {gql_reset_str}")

    def _make_request_old(self, method: str, url: str, **kwargs) -> requests.Response:
        """Makes an HTTP request, handling token selection and rate limiting."""
        token = self.token_pool.get_token()
        if not token:
            raise ValueError("No GitHub tokens available. Set AIE_GITHUB_TOKENS or create config/tokens.ini.")

        headers = kwargs.get('headers', {})
        headers['Authorization'] = f'token {token}'
        kwargs['headers'] = headers

        response = self.session.request(method, url, **kwargs)
        self.token_pool.update_rate_limit(token, response.headers)

        if response.status_code == 403 and 'rate limit' in response.text.lower():
            print("Rate limit hit. The token pool will handle waiting.")
            token = self.token_pool.get_token()
            headers['Authorization'] = f'token {token}'
            response = self.session.request(method, url, **kwargs)

        response.raise_for_status()
        return response

    def _make_request(self, method: str, url: str, api_type: str = 'rest', **kwargs) -> requests.Response:
            """Makes an HTTP request with proper API type handling."""
            while True:
                token = self.token_pool.get_token(api_type)
                if not token:
                    raise ValueError("No GitHub tokens available. Set AIE_GITHUB_TOKENS or create config/tokens.ini.")

                headers = kwargs.get('headers', {})
                headers['Authorization'] = f'Bearer {token}' if api_type == 'graphql' else f'token {token}'
                kwargs['headers'] = headers

                response = self.session.request(method, url, **kwargs)

                # Always update the pool with fresh headers
                self.token_pool.update_rate_limit(token, response.headers, api_type)

                if response.status_code == 403 and 'rate limit' in response.text.lower():
                    api_name = "GraphQL" if api_type == 'graphql' else "REST"
                    print(f"⚠️ Token {token[:8]}...{token[-4:]} hit {api_name} rate limit. Retrying with another token...")
                    continue

                response.raise_for_status()
                return response




    def _paginated_get_callback(self, url: str, params: dict, max_results: int, stop_condition_callback: Optional[callable] = None) -> List[Dict]:
        """Handles pagination for GET requests."""
        results = []
        should_stop = False
        page = 0
        while url and len(results) < max_results and not should_stop:
            response = self._make_request('get', url, params=params)
            data = response.json()
            #print("===========================================================")
            print(f"page: {page}")
            page += 1
            if isinstance(data, list):
                items = data
            else:
                items = data.get('items', [])

            for item in items:
                if len(results) >= max_results:
                    should_stop = True
                    break

                results.append(item)

                # Check stop condition AFTER adding the item
                if stop_condition_callback and stop_condition_callback(item):
                    should_stop = True
                    break

            if not should_stop:
                url = response.links.get('next', {}).get('url')
                params = {}  # Params are only for the first request

        return results

    def _paginated_get(self, url: str, params: dict, max_results: int) -> List[Dict]:
            """Handles pagination for GET requests."""
            results = []
            while url and len(results) < max_results:
                response = self._make_request('get', url, params=params)
                data = response.json()

                if isinstance(data, list):
                      items = data
                else:
                      items = data.get('items', [])
                results.extend(items)

                url = response.links.get('next', {}).get('url')
                params = {}  # Params are only for the first request

            return results[:max_results]


    def get_file_list_by_api(self, owner: str, repo: str) -> List[str]:
        """
        Gets a repository's file list using the Git Trees API.

        Args:
            owner: The repository owner.
            repo: The repository name.

        Returns:
            A list of file paths.

        Raises:
            RepositoryTooLargeError: If the API response is truncated.
            requests.exceptions.HTTPError: For other API errors.
        """
        api_url = f"{self.base_url}/repos/{owner}/{repo}/git/trees/HEAD"
        #print(f"Attempting to get file list for '{owner}/{repo}' via API...")

        response = self._make_request('get', api_url, params={'recursive': '1'})

        tree_data = response.json()

        if tree_data.get('truncated', False):
            raise RepositoryTooLargeError(f"Repository {owner}/{repo} is too large to list files via the API.")

        return [
            item['path'] for item in tree_data.get('tree', [])
            if item['type'] == 'blob'
        ]

    def search_commits(self, query: str, max_results: int = 1000) -> List[Dict]:
        """Searches for commits."""
        url = f"{self.base_url}/search/commits"
        params = {'q': query, 'per_page': 100}
        # Special header for commit search
        self.session.headers['Accept'] = 'application/vnd.github.cloak-preview+json'
        results = self._paginated_get(url, params, max_results)
        # Reset header to default
        self.session.headers['Accept'] = 'application/vnd.github.v3+json'
        return results

    def search_pull_requests(self, query: str, max_results: int = 1000) -> List[Dict]:
        """Searches for pull requests (via issues search)."""
        url = f"{self.base_url}/search/issues"
        params = {'q': f'type:pr {query}', 'per_page': 100}
        return self._paginated_get(url, params, max_results)

    def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: Optional[str] = None,
        decode: bool = True
    ) -> Union[str, bytes, Dict]:
        """
        Fetches the contents of a single file from a GitHub repository.

        Args:
            owner: Repository owner (e.g., 'openai')
            repo: Repository name (e.g., 'openai-cookbook')
            path: File path within the repo (e.g., 'README.md')
            ref: Optional branch, tag, or commit SHA (default: default branch)
            decode: If True, Base64-decode the file content.

        Returns:
            If decode=True → str (decoded text)
            If decode=False → dict (raw GitHub API response)
        """
        api_url = f"{self.base_url}/repos/{owner}/{repo}/contents/{path}"
        params = {'ref': ref} if ref else {}

        response = self._make_request('get', api_url, params=params)
        data = response.json()

        if isinstance(data, list):
            # Directory listing instead of a file
            raise ValueError(f"The path '{path}' appears to be a directory, not a file.")

        if 'content' not in data:
            raise ValueError(f"No content field found for {owner}/{repo}/{path}")

        if decode and data.get('encoding') == 'base64':
            try:
                decoded_bytes = base64.b64decode(data['content'])
                # Try to decode to UTF-8 text; if fails, return bytes
                return decoded_bytes.decode('utf-8', errors='replace')
            except Exception as e:
                print(f"⚠️ Failed to decode file {path}: {e}")
                return decoded_bytes  # raw bytes

        return data


    def run_graphQL_query(self, query, variables):
        """GraphQL query with proper rate limit handling."""
        url = "https://api.github.com/graphql"
        payload = {"query": query, "variables": variables}

        response = self._make_request('post', url, api_type='graphql', json=payload)

        data = response.json()
        if "errors" in data:
            raise RuntimeError(data["errors"])
        return data["data"]

GITHUB_API = GitHubAPI()

if __name__ == "__main__":
    api = GitHubAPI()
    api.print_rate_limit_status()

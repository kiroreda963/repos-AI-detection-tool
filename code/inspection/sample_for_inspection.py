"""
Sample AI-generated commits, PRs, and files for manual inspection.

Usage:
    python sample_for_inspection.py <analysis_dir> <N> <inspector1> <inspector2> [<inspector3> ...]

Example:
    python sample_for_inspection.py ../temp/october-31 10 Alice Bob Charlie
"""

import sys
import os
import re
import random
import csv
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd
from tqdm import tqdm
from utils.data import BasePullRequest, LogCommit, Author, PullRequest, PRCommit
from utils.import_export import read_dataclasses_from_csv
from heuristics import is_ai_pr, any_authors_of_type, is_ai_pr_meta, any_ai_commits, is_ai_pr_branch, is_ai_pr_label


def extract_owner_repo(github_url: str) -> tuple[str, str]:
    """Extract owner/repo from github URL. Returns ('owner', 'repo')."""
    # Remove protocol and domain
    path = re.sub(r'https?://github\.com/', '', github_url)
    # Remove trailing .git if present
    path = path.rstrip('/')
    parts = path.split('/')
    if len(parts) >= 2:
        return parts[0], parts[1]
    return '', ''


@dataclass
class CommitSample:
    github_url: str
    commit_url: str  # Direct link to commit
    commit_sha: str
    author: str
    date: str
    message: str
    tool: str
    auth_type: str = ""  # "AI" or "AI-FOLLOWUP"


@dataclass
class FileSample:
    github_url: str
    file_url: str  # Direct link to file (assumes main branch)
    alternative_url: str  # Alternative branch (master if main, main if master)
    file_path: str
    tool: str


@dataclass
class PRSample:
    github_url: str
    pr_url: str  # Direct link to PR
    pr_number: int
    title: str
    branch: str
    ai_signal: str


def extract_github_url_from_log(log_path: Path) -> Optional[str]:
    """
    Parse analysis.log to extract github_url.
    Looks for a line like: "Starting analysis for https://github.com/..."
    """
    try:
        with open(log_path, 'r') as f:
            for line in f:
                match = re.search(r'Starting analysis for (https?://github\.com/[^\s]+)', line)
                if match:
                    return match.group(1)
    except Exception as e:
        print(f"Warning: Could not read log {log_path}: {e}", file=sys.stderr)
    return None


def load_ai_commits(repo_dir: Path, github_url: str) -> list[CommitSample]:
    """Load AI-attributed commits from commit_stats_main.csv"""
    samples = []
    csv_path = repo_dir / "commit_stats_main.csv"

    if not csv_path.exists():
        return samples

    try:
        df = pd.read_csv(csv_path)
        # Filter for AI commits (auth_type == "AI")
        ai_commits = df[df.get('auth_type', '').eq('AI')]

        for _, row in ai_commits.iterrows():
            samples.append(CommitSample(
                github_url=github_url,
                commit_sha=str(row['sha'])[:8],  # short SHA
                author=str(row.get('author', '')),
                date=str(row.get('date', '')),
                message=str(row.get('message', ''))[:100],  # truncate
                tool=str(row.get('tool', ''))
            ))
    except Exception as e:
        print(f"Warning: Could not load commits from {csv_path}: {e}", file=sys.stderr)

    return samples


def load_ai_files(repo_dir: Path, github_url: str) -> list[FileSample]:
    """Load files using AI tools from tools/tool_files.csv"""
    samples = []
    csv_path = repo_dir / "tools" / "tool_files.csv"

    if not csv_path.exists():
        return samples

    try:
        df = pd.read_csv(csv_path)

        for _, row in df.iterrows():
            samples.append(FileSample(
                github_url=github_url,
                file_path=str(row.get('file_path', '')),
                tool=str(row.get('tool', ''))
            ))
    except Exception as e:
        print(f"Warning: Could not load files from {csv_path}: {e}", file=sys.stderr)

    return samples


def load_ai_prs(repo_dir: Path, github_url: str) -> list[PRSample]:
    """Load AI-generated PRs from pr_list.csv"""
    samples = []
    csv_path = repo_dir / "pull_requests" / "pr_list.csv"

    if not csv_path.exists():
        return samples

    try:
        pr_list: list[BasePullRequest] = read_dataclasses_from_csv(csv_path, BasePullRequest)

        for pr in pr_list:
            # Determine what signal made us think this PR is AI
            ai_signal = ""

            # Check branch name for AI patterns
            if re.search(r'(?i)(copilot|claude|ai|gpt|codex)', pr.branch_name or ""):
                ai_signal = "branch_name"

            # Check labels for AI patterns
            if pr.labels:
                for label in pr.labels:
                    if re.search(r'(?i)(copilot|claude|ai|gpt|codex)', label):
                        ai_signal = "label"
                        break

            # If we have a signal, add to samples
            if ai_signal:
                samples.append(PRSample(
                    github_url=github_url,
                    pr_number=pr.number,
                    pr_url=pr.url,
                    title=pr.title[:80],
                    branch=pr.branch_name or "",
                    ai_signal=ai_signal
                ))

    except Exception as e:
        print(f"Warning: Could not load PRs from {csv_path}: {e}", file=sys.stderr)

    return samples


def get_repo_directories(analysis_dir: Path) -> list[tuple[Path, str]]:
    """
    Find all repo directories and their GitHub URLs.
    Returns list of (repo_dir, github_url) tuples.
    """
    repos = []

    for repo_dir in analysis_dir.iterdir():
        if not repo_dir.is_dir():
            continue

        log_path = repo_dir / "analysis.log"
        if not log_path.exists():
            continue

        github_url = extract_github_url_from_log(log_path)
        if not github_url:
            continue

        repos.append((repo_dir, github_url))

    return repos


def sample_commit_from_repo(repo_dir: Path, github_url: str, auth_type: str = "AI") -> Optional[CommitSample]:
    """
    Try to get a random commit from a repo filtered by auth_type.
    auth_type can be "AI" or "AI-FOLLOWUP". Return None if not available.
    """
    csv_path = repo_dir / "commit_stats_main.csv"

    if not csv_path.exists():
        return None

    try:
        df = pd.read_csv(csv_path)
        filtered_commits = df[df.get('auth_type', '').eq(auth_type)]

        if filtered_commits.empty:
            return None

        row = filtered_commits.sample(1).iloc[0]
        sha = str(row['sha'])
        owner, repo = extract_owner_repo(github_url)
        commit_url = f"https://github.com/{owner}/{repo}/commit/{sha}"

        return CommitSample(
            github_url=github_url,
            commit_url=commit_url,
            commit_sha=sha[:8],
            author=str(row.get('author', '')),
            date=str(row.get('date', '')),
            message=str(row.get('message', ''))[:100],
            tool=str(row.get('tool', '')),
            auth_type=auth_type
        )
    except Exception:
        return None


def sample_commit_any_from_repo(repo_dir: Path, github_url: str) -> Optional[CommitSample]:
    """
    Try to get a random commit from a repo that is either AI or AI-FOLLOWUP.
    Return None if not available.
    """
    csv_path = repo_dir / "commit_stats_main.csv"

    if not csv_path.exists():
        return None

    try:
        df = pd.read_csv(csv_path)
        # Filter for commits that are either AI or AI-FOLLOWUP
        filtered_commits = df[df.get('auth_type', '').isin(['AI', 'AI-FOLLOWUP'])]

        if filtered_commits.empty:
            return None

        row = filtered_commits.sample(1).iloc[0]
        sha = str(row['sha'])
        auth_type_val = str(row['auth_type'])
        owner, repo = extract_owner_repo(github_url)
        commit_url = f"https://github.com/{owner}/{repo}/commit/{sha}"

        return CommitSample(
            github_url=github_url,
            commit_url=commit_url,
            commit_sha=sha[:8],
            author=str(row.get('author', '')),
            date=str(row.get('date', '')),
            message=str(row.get('message', ''))[:100],
            tool=str(row.get('tool', '')),
            auth_type=auth_type_val
        )
    except Exception:
        return None


def sample_file_from_repo(repo_dir: Path, github_url: str) -> Optional[FileSample]:
    """Try to get a random AI file from a repo. Return None if not available."""
    csv_path = repo_dir / "tools" / "tool_files.csv"

    if not csv_path.exists():
        return None

    try:
        df = pd.read_csv(csv_path)

        if df.empty:
            return None

        row = df.sample(1).iloc[0]
        file_path = str(row.get('file_path', ''))
        owner, repo = extract_owner_repo(github_url)
        file_url = f"https://github.com/{owner}/{repo}/blob/main/{file_path}"
        alternative_url = f"https://github.com/{owner}/{repo}/blob/master/{file_path}"

        return FileSample(
            github_url=github_url,
            file_url=file_url,
            alternative_url=alternative_url,
            file_path=file_path,
            tool=str(row.get('tool', ''))
        )
    except Exception:
        return None


def sample_pr_meta_from_repo(repo_dir: Path, github_url: str) -> Optional[PRSample]:
    """Try to get a random PR from a repo using only branch/label metadata heuristics."""
    pr_list_path = repo_dir / "pull_requests" / "pr_list.csv"

    if not pr_list_path.exists():
        return None

    try:
        # Load base PRs
        pr_list: list[BasePullRequest] = read_dataclasses_from_csv(pr_list_path, BasePullRequest)

        # Filter for AI PRs using only metadata (branch/label)
        ai_prs = []
        for pr in pr_list:
            # Check branch and label using heuristics functions
            has_ai_branch = is_ai_pr_branch(pr)
            has_ai_label = is_ai_pr_label(pr)

            if has_ai_branch:
                ai_prs.append((pr, "branch"))
            elif has_ai_label:
                ai_prs.append((pr, "label"))

        if not ai_prs:
            return None

        pr, ai_signal = random.choice(ai_prs)
        return PRSample(
            github_url=github_url,
            pr_url=pr.url,
            pr_number=pr.number,
            title=pr.title[:80],
            branch=pr.branch_name or "",
            ai_signal=ai_signal
        )
    except Exception:
        return None


def sample_pr_from_repo(repo_dir: Path, github_url: str) -> Optional[PRSample]:
    """Try to get a random AI PR from a repo. Return None if not available."""
    pr_list_path = repo_dir / "pull_requests" / "pr_list.csv"
    pr_commits_path = repo_dir / "pull_requests" / "pr_commits.csv"

    if not pr_list_path.exists():
        return None

    try:
        # Load base PRs
        pr_list: list[BasePullRequest] = read_dataclasses_from_csv(pr_list_path, BasePullRequest)

        # Load PR commits if available
        pr_commits: list[PRCommit] = []
        if pr_commits_path.exists():
            pr_commits = read_dataclasses_from_csv(pr_commits_path, PRCommit)

        # Reconstruct full PullRequest objects
        prs: list[PullRequest] = [PullRequest.from_superclass(bpr, pr_commits) for bpr in pr_list]

        # Filter for AI PRs using is_ai_pr() heuristic
        ai_prs = []
        for pr in prs:
            if is_ai_pr(pr):
                # Determine signal: meta (branch/label) or commits
                if is_ai_pr_meta(pr):
                    ai_signal = "branch/label"
                elif any_ai_commits(pr.commits):
                    ai_signal = "commits"
                else:
                    ai_signal = "unknown"

                ai_prs.append((pr, ai_signal))

        if not ai_prs:
            return None

        pr, ai_signal = random.choice(ai_prs)
        return PRSample(
            github_url=github_url,
            pr_url=pr.url,
            pr_number=pr.number,
            title=pr.title[:80],
            branch=pr.branch_name or "",
            ai_signal=ai_signal
        )
    except Exception as e:
        return None


def collect_samples_from_repos(repos: list[tuple[Path, str]], n: int) -> tuple[list[CommitSample], list[CommitSample], list[CommitSample], list[FileSample], list[PRSample], list[PRSample]]:
    """
    Sample n of each artifact type by randomly picking repos until we have enough.
    Returns: (ai_commits, ai_followup_commits, ai_or_followup_commits, files, prs_with_commits, prs_meta_only)
    Shows progress bars for each artifact type.
    """
    ai_commits = []
    ai_followup_commits = []
    ai_or_followup_commits = []
    files = []
    prs = []
    prs_meta = []

    max_attempts = len(repos) * 3  # Try up to 3x the number of repos

    # Sample AI commits
    """pbar = tqdm(total=n, desc="Sampling AI commits", unit="commit")
    attempts = 0
    while len(ai_commits) < n and attempts < max_attempts:
        repo_dir, github_url = random.choice(repos)
        commit = sample_commit_from_repo(repo_dir, github_url, auth_type="AI")
        if commit:
            ai_commits.append(commit)
            pbar.update(1)
        attempts += 1
    pbar.close()"""

    # Sample AI-FOLLOWUP commits
    """pbar = tqdm(total=n, desc="Sampling AI-FOLLOWUP commits", unit="commit")
    attempts = 0
    while len(ai_followup_commits) < n and attempts < max_attempts:
        repo_dir, github_url = random.choice(repos)
        commit = sample_commit_from_repo(repo_dir, github_url, auth_type="AI-FOLLOWUP")
        if commit:
            ai_followup_commits.append(commit)
            pbar.update(1)
        attempts += 1
    pbar.close()"""

    # Sample AI or AI-FOLLOWUP commits (combined)
    pbar = tqdm(total=n, desc="Sampling AI/AI-FOLLOWUP commits", unit="commit")
    attempts = 0
    while len(ai_or_followup_commits) < n and attempts < max_attempts:
        repo_dir, github_url = random.choice(repos)
        commit = sample_commit_any_from_repo(repo_dir, github_url)
        if commit:
            ai_or_followup_commits.append(commit)
            pbar.update(1)
        attempts += 1
    pbar.close()

    # Sample files
    pbar = tqdm(total=n, desc="Sampling files", unit="file")
    attempts = 0
    while len(files) < n and attempts < max_attempts:
        repo_dir, github_url = random.choice(repos)
        file = sample_file_from_repo(repo_dir, github_url)
        if file:
            files.append(file)
            pbar.update(1)
        attempts += 1
    pbar.close()

    # Sample PRs (using full heuristic: branch/label/commits)
    """pbar = tqdm(total=n, desc="Sampling PRs (all heuristics)", unit="pr")
    attempts = 0
    while len(prs) < n and attempts < max_attempts:
        repo_dir, github_url = random.choice(repos)
        pr = sample_pr_from_repo(repo_dir, github_url)
        if pr:
            prs.append(pr)
            pbar.update(1)
        attempts += 1
    pbar.close()"""

    # Sample PRs (using only metadata heuristics: branch/label)
    pbar = tqdm(total=n, desc="Sampling PRs (metadata only)", unit="pr")
    attempts = 0
    while len(prs_meta) < n and attempts < max_attempts:
        repo_dir, github_url = random.choice(repos)
        pr = sample_pr_meta_from_repo(repo_dir, github_url)
        if pr:
            prs_meta.append(pr)
            pbar.update(1)
        attempts += 1
    pbar.close()

    print(f"Collected: {len(ai_commits)} AI commits, {len(ai_followup_commits)} AI-FOLLOWUP commits, {len(ai_or_followup_commits)} AI/AI-FOLLOWUP commits, {len(files)} files, {len(prs)} PRs (all heuristics), {len(prs_meta)} PRs (metadata only)")

    return ai_commits, ai_followup_commits, ai_or_followup_commits, files, prs, prs_meta


def sample_and_assign(items, n: int, inspectors: list[str]) -> list[dict]:
    """
    Random sample n items and assign pairs of inspectors.
    Inspectors are rotated to balance workload.
    """
    if len(items) <= n:
        sampled = items
    else:
        sampled = random.sample(items, n)

    # Rotate through inspector pairs
    inspector_pairs = []
    for i in range(len(inspectors)):
        for j in range(i + 1, len(inspectors)):
            inspector_pairs.append((inspectors[i], inspectors[j]))

    if not inspector_pairs:
        raise ValueError("Need at least 2 inspectors")

    result = []
    for idx, item in enumerate(sampled):
        pair_idx = idx % len(inspector_pairs)
        inspector1, inspector2 = inspector_pairs[pair_idx]

        item_dict = vars(item).copy() if hasattr(item, '__dict__') else item
        item_dict['inspector_1'] = inspector1
        item_dict['inspector_2'] = inspector2
        item_dict['notes_1'] = ""
        item_dict['notes_2'] = ""
        item_dict['verdict_1'] = ""
        item_dict['verdict_2'] = ""

        result.append(item_dict)

    return result


def write_samples_csv(samples: list[dict], output_path: Path, field_order: list[str]):
    """Write samples to CSV with specified column order."""
    if not samples:
        print(f"No samples to write to {output_path}")
        return

    # Ensure all fields exist
    all_fields = set()
    for sample in samples:
        all_fields.update(sample.keys())

    # Ordered columns: specified fields first, then others
    columns = [f for f in field_order if f in all_fields]
    columns.extend([f for f in all_fields if f not in field_order])

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(samples)

    print(f"Wrote {len(samples)} samples to {output_path}")


def write_samples_by_annotator(samples: list[dict], output_dir: Path, filename_base: str, inspectors: list[str], field_order: list[str]):
    """
    Write samples split by annotator. Each annotator gets only their assigned samples.
    Each file contains: original fields + notes + verdict (without annotator name prefix)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for inspector in inspectors:
        inspector_samples = []
        
        for sample in samples:
            # Check if this inspector is assigned to this sample (in either position)
            position = None
            if sample.get('inspector_1') == inspector:
                position = 1
            elif sample.get('inspector_2') == inspector:
                position = 2
            
            if position is None:
                continue
            
            # Create a copy with only the relevant fields
            new_sample = {k: v for k, v in sample.items() 
                         if k not in ['inspector_1', 'inspector_2', 'notes_1', 'notes_2', 'verdict_1', 'verdict_2']}
            
            # Add this inspector's columns (without prefixing annotator name)
            new_sample['notes'] = sample.get(f'notes_{position}', '')
            new_sample['verdict'] = sample.get(f'verdict_{position}', '')
            
            inspector_samples.append(new_sample)
        
        # Build field order: original order, then notes and verdict
        inspector_field_order = [f for f in field_order 
                                if f not in ['inspector_1', 'inspector_2', 'notes_1', 'notes_2', 'verdict_1', 'verdict_2']]
        inspector_field_order.extend(['notes', 'verdict'])
        
        output_path = output_dir / f"{filename_base}_{inspector}.csv"
        write_samples_csv(inspector_samples, output_path, inspector_field_order)
        print(f"Wrote {len(inspector_samples)} samples for {inspector}")


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    # Parse arguments
    analysis_dir = Path(sys.argv[1])
    n = int(sys.argv[2])

    # Check for --by-annotator flag
    by_annotator = False
    remaining_args = sys.argv[3:]
    if '--by-annotator' in remaining_args:
        by_annotator = True
        remaining_args.remove('--by-annotator')

    inspectors = remaining_args

    if not analysis_dir.exists():
        print(f"Error: {analysis_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    if len(inspectors) < 2:
        print("Error: Need at least 2 inspectors", file=sys.stderr)
        sys.exit(1)

    random.seed(42)  # Reproducible sampling

    print(f"Finding repos in {analysis_dir}...")
    repos = get_repo_directories(analysis_dir)
    print(f"Found {len(repos)} repos")

    print(f"\nSampling {n} of each type...")
    ai_commits, ai_followup_commits, ai_or_followup_commits, files, prs, prs_meta = collect_samples_from_repos(repos, n)

    print(f"\nAssigning inspectors...")
    #ai_commit_samples = sample_and_assign(ai_commits, len(ai_commits), inspectors)
    #ai_followup_samples = sample_and_assign(ai_followup_commits, len(ai_followup_commits), inspectors)
    ai_or_followup_samples = sample_and_assign(ai_or_followup_commits, len(ai_or_followup_commits), inspectors)
    file_samples = sample_and_assign(files, len(files), inspectors)
    #pr_samples = sample_and_assign(prs, len(prs), inspectors)
    pr_meta_samples = sample_and_assign(prs_meta, len(prs_meta), inspectors)

    # Write output CSVs to parent directory
    output_dir = analysis_dir.parent / "inspection_samples"

    if by_annotator:
        # Write separate files for each annotator
        print(f"\nWriting separate files for each annotator...")
        #write_samples_by_annotator(ai_commit_samples, output_dir, "ai_commits_for_inspection", inspectors,
        #    ['commit_url', 'commit_sha', 'author', 'date', 'message', 'tool', 'auth_type', 'github_url'])
        #write_samples_by_annotator(ai_followup_samples, output_dir, "ai_followup_commits_for_inspection", inspectors,
        #    ['commit_url', 'commit_sha', 'author', 'date', 'message', 'tool', 'auth_type', 'github_url'])
        write_samples_by_annotator(ai_or_followup_samples, output_dir, "ai_or_followup_commits_for_inspection", inspectors,
            ['commit_url', 'commit_sha', 'author', 'date', 'message', 'tool', 'auth_type', 'github_url'])
        write_samples_by_annotator(file_samples, output_dir, "files_for_inspection", inspectors,
            ['file_url', 'alternative_url', 'file_path', 'tool', 'github_url'])
        #write_samples_by_annotator(pr_samples, output_dir, "prs_for_inspection", inspectors,
        #    ['pr_url', 'pr_number', 'title', 'branch', 'ai_signal', 'github_url'])
        write_samples_by_annotator(pr_meta_samples, output_dir, "prs_metadata_for_inspection", inspectors,
            ['pr_url', 'pr_number', 'title', 'branch', 'ai_signal', 'github_url'])
    else:
        # Write combined files
        #write_samples_csv(
        #    ai_commit_samples,
        #    output_dir / "ai_commits_for_inspection.csv",
        #    ['commit_url', 'commit_sha', 'author', 'date', 'message', 'tool', 'auth_type', 'github_url',
        #     'inspector_1', 'inspector_2', 'notes_1', 'notes_2', 'verdict_1', 'verdict_2']
        #)

        #write_samples_csv(
        #    ai_followup_samples,
        #    output_dir / "ai_followup_commits_for_inspection.csv",
        #    ['commit_url', 'commit_sha', 'author', 'date', 'message', 'tool', 'auth_type', 'github_url',
        #     'inspector_1', 'inspector_2', 'notes_1', 'notes_2', 'verdict_1', 'verdict_2']
        #)

        write_samples_csv(
            ai_or_followup_samples,
            output_dir / "ai_or_followup_commits_for_inspection.csv",
            ['commit_url', 'commit_sha', 'author', 'date', 'message', 'tool', 'auth_type', 'github_url',
             'inspector_1', 'inspector_2', 'notes_1', 'notes_2', 'verdict_1', 'verdict_2']
        )

        write_samples_csv(
            file_samples,
            output_dir / "files_for_inspection.csv",
            ['file_url', 'alternative_url', 'file_path', 'tool', 'github_url',
             'inspector_1', 'inspector_2', 'notes_1', 'notes_2', 'verdict_1', 'verdict_2']
        )

        #write_samples_csv(
        #    pr_samples,
        #    output_dir / "prs_for_inspection.csv",
        #    ['pr_url', 'pr_number', 'title', 'branch', 'ai_signal', 'github_url',
        #     'inspector_1', 'inspector_2', 'notes_1', 'notes_2', 'verdict_1', 'verdict_2']
        #)

        write_samples_csv(
            pr_meta_samples,
            output_dir / "prs_metadata_for_inspection.csv",
            ['pr_url', 'pr_number', 'title', 'branch', 'ai_signal', 'github_url',
             'inspector_1', 'inspector_2', 'notes_1', 'notes_2', 'verdict_1', 'verdict_2']
        )

    mode = "by-annotator" if by_annotator else "combined"
    print(f"\nAll samples written to {output_dir}/ ({mode} mode)")


if __name__ == "__main__":
    main()

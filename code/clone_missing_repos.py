"""
Clone repositories for non-adopter analysis directories.

This script reads analysis.log files from a directory of non-adopter repositories,
extracts their GitHub URLs, and performs shallow clones for each one in parallel.
With --check-repos, it first verifies shallow clones for issues before re-cloning.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from repo_analyzer import RepoAnalyzer, StatSummary
from utils.parallel import run_parallel
from utils.import_export import read_dataclasses_from_csv, write_dataclasses_to_csv
from utils.data import LogCommit

def needs_reclone(shallow_path: Path) -> bool:
    """Check if a shallow repo needs re-cloning."""
    if not (shallow_path / ".git").exists():
        return True
    try:
        subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=shallow_path,
            capture_output=True,
            text=True,
            check=True
        )
        return False  # Valid repo with HEAD
    except Exception:
        return True  # No HEAD or git error → re-clone



def clone_repo(task: dict) -> dict:
    """Worker function to clone a single repository and run analysis."""
    github_url = task["github_url"]
    output_dir = task["output_dir"]
    
    try:
        analyzer = RepoAnalyzer(
            exp_name="",
            github_url=github_url,
            base_dir=str(output_dir),
            analysis_period="2025-01-01",
            resume=True
        )
        analyzer.shallow_clone_repo()
        
        # Verify the clone was successful
        shallow_clone_path = Path(output_dir) / "clones" / "shallow"
        git_dir = shallow_clone_path / ".git"
        
        if not git_dir.exists():
            error_msg = f"Shallow clone directory is not a valid Git repository (missing .git at {git_dir})"
            return {"success": False, "url": github_url, "error": error_msg}
        
        if not shallow_clone_path.is_dir():
            error_msg = f"Shallow clone path does not exist: {shallow_clone_path}"
            return {"success": False, "url": github_url, "error": error_msg}
        
        # Run full analysis
        result = analyzer.do_analysis()
        
        # Verify analysis completed (check for analysis log or result)
        log_file = Path(output_dir) / "analysis.log"
        if not log_file.exists():
            error_msg = f"Analysis did not complete (no analysis.log)"
            return {"success": False, "url": github_url, "error": error_msg}
        
        # Check if error occurred
        if result[0].error:
            error_msg = f"Analysis error: {result[0].error}"
            return {"success": False, "url": github_url, "error": error_msg}
        
        # For non-adopters without commit_stats_main.csv, create one from commit_list.csv
        commit_stats_main = Path(output_dir) / "commit_stats_main.csv"
        if not commit_stats_main.exists():
            commit_list_path = Path(output_dir) / "commit_list.csv"
            if commit_list_path.exists():
                try:
                    commits = read_dataclasses_from_csv(commit_list_path, LogCommit)
                    # Convert LogCommit to StatSummary with auth_type="Human"
                    stats = [StatSummary.from_log_commit("Human", "", commit) for commit in commits]
                    write_dataclasses_to_csv(commit_stats_main, stats, StatSummary)
                except Exception as e:
                    error_msg = f"Failed to create commit_stats_main.csv: {str(e)}"
                    return {"success": False, "url": github_url, "error": error_msg}
        
        return {"success": True, "url": github_url}
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        return {"success": False, "url": github_url, "error": error_msg}


def result_callback(result: dict):
    """Callback to handle individual results."""
    if result["success"]:
        print(f"✅ Cloned {result['url']}")
    else:
        print(f"❌ Failed to clone {result['url']}: {result['error']}")


def clone_missing_repos(root_dir: str, max_workers: int = 4, check_repos: bool = False):
    """Clone repositories for non-adopter analysis directories."""
    root_path = Path(root_dir)

    if not root_path.exists():
        print(f"❌ Directory not found: {root_dir}")
        return

    log_files = list(root_path.glob("*/analysis.log"))
    if not log_files:
        print(f"⚠️  No analysis.log files found in {root_dir}")
        return

    print(f"📂 Found {len(log_files)} repositories to process\n")

    # Extract tasks from log files
    tasks = []
    for log_file in log_files:
        try:
            with open(log_file) as f:
                content = f.read()
        except Exception as e:
            print(f"❌ Failed to read {log_file}: {e}")
            continue

        match = re.search(r"Starting analysis for (https://github\.com/\S+)", content)
        if not match:
            print(f"⚠️  Could not extract URL from {log_file}")
            continue

        github_url = match.group(1)
        output_dir = log_file.parent
        shallow_clone_path = Path(output_dir) / "clones" / "shallow"

        if check_repos:
            if not needs_reclone(shallow_clone_path):
                print(f"✅ {github_url} - OK, skipping")
                continue
            print(f"⚠️  {github_url} - needs re-clone")

        tasks.append({"github_url": github_url, "output_dir": str(output_dir), "url": github_url})
    
    if not tasks:
        print("⚠️  No repositories need cloning")
        return
    
    # Run cloning in parallel
    results = run_parallel(
        clone_repo,
        tasks,
        max_workers=max_workers,
        executor_type='thread',
        description="Cloning repositories",
        result_callback=result_callback
    )
    
    # Summary
    success_count = sum(1 for r in results if r.get("success"))
    failed_count = len(results) - success_count
    print(f"\n📊 Summary: {success_count} succeeded, {failed_count} failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clone missing repositories")
    parser.add_argument("root_directory", help="Root directory containing analysis subdirectories")
    parser.add_argument("max_workers", nargs='?', type=int, default=4,
                        help="Number of parallel workers (default: 4)")
    parser.add_argument("--check-repos", action="store_true",
                        help="Check shallow repos first; only re-clone if .git missing or git log matches reference")
    args = parser.parse_args()

    clone_missing_repos(args.root_directory, max_workers=args.max_workers, check_repos=args.check_repos)

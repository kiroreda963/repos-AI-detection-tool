#!/usr/bin/env python3
"""
Generate GitHub search URL tables from heuristic pattern CSVs.

Usage:
    python generate_heuristics_tables.py <input_directory> [--per-tool-md]

The input directory should contain:
    - authors.csv
    - files.csv
    - branches.csv
    - labels.csv
    - tools.csv (optional: name,url)

Outputs (in current directory):
    - heuristics.csv
    - heuristics.md
    - heuristics.tex
    - tools/ (if --per-tool-md is specified)
"""

import csv
import urllib.parse
import sys
import re
from pathlib import Path
from collections import defaultdict
import argparse

def build_file_url(pattern: str) -> str:
    """Build a GitHub search URL for a file/directory path pattern (readable, not percent-encoded)."""
    if pattern.endswith('/'):
        suffix = ''  # for directories
    else:
        suffix = '$'  # for files, match end of path

    escaped_pattern = pattern.replace('.', r'\.').replace('-', r'\-').replace('/', r'\/')
    regex = f"(?:^|\\/)({escaped_pattern}){suffix}/"
    encoded = urllib.parse.quote(f"path:/{regex}")
    return f"https://github.com/search?q={encoded}&type=code"

def build_file_url_old(pattern: str) -> str:
    """Build a GitHub search URL for a file/directory path pattern (readable, not percent-encoded)."""
    if pattern.endswith('/'):
        suffix = ''  # for directories
    else:
        suffix = '$'  # for files, match end of path

    escaped_pattern = pattern.replace('.', r'\.').replace('-', r'\-').replace('/', r'\/')
    regex = f"(?:^|\/)({escaped_pattern}){suffix}/"
    # Don't percent-encode; GitHub search supports raw query strings
    query = f"path:/{regex}"
    return f"https://github.com/search?q={query}&type=code"

def build_author_url(pattern: str) -> str:
    """Build a GitHub commits search URL for commits authored by the pattern."""
    # readable form without encoding
    #query = f'author:"{pattern}"'
    encoded = urllib.parse.quote(f'author:"{pattern}"')
    return f"https://github.com/search?q={encoded}&type=commits"


def build_coauthor_url(pattern: str) -> str:
    """Build a GitHub commits search URL for Co-authored-by matching the pattern."""
    encoded = urllib.parse.quote(f'Co-authored-by:"{pattern}"')
    return f"https://github.com/search?q={encoded}&type=commits"


def build_branch_url(pattern: str) -> str:
    """Build a GitHub search URL for PR branch names."""
    query = urllib.parse.quote(f'head:{pattern} type:pr')
    return f"https://github.com/search?q={query}&type=pullrequests"


def build_label_url(pattern: str) -> str:
    """Build a GitHub search URL for PR labels."""
    query = urllib.parse.quote(f'label:{pattern} type:pr')
    return f"https://github.com/search?q={query}&type=pullrequests"


def sanitize_filename(name: str) -> str:
    """Sanitize tool name for use as filename."""
    # Replace spaces and special characters with underscores
    sanitized = re.sub(r'[^\w\-]', '_', name)
    # Remove multiple consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip('_')
    return sanitized.lower()


def format_count(count: int) -> str:
    """Format a large number into a human-readable string (e.g., 12345 -> 12.3K)."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def read_patterns(csv_path: Path) -> list[dict]:
    """Read patterns from a CSV file."""
    patterns = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            patterns.append({
                'pattern': row['pattern'].strip(),
                'type': row['type'].strip(),
                'subtype': row['subtype'].strip()
            })
    return patterns


def read_tool_urls(csv_path: Path) -> dict:
    """Read tool URLs from tools.csv."""
    tool_urls = {}
    if not csv_path.exists():
        return tool_urls
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row['name'].strip()
            url = row['url'].strip()
            tool_urls[name] = url

    return tool_urls


def read_counts(csv_path: Path) -> dict[tuple[str, str, str], int]:
    """Read heuristic counts from counts.csv."""
    counts = {}
    if not csv_path.exists():
        print(f"Warning: {csv_path} not found. Using default count of 0 for all heuristics.")
        return counts

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                tool = row['tool'].strip()
                heuristic_type = row['heuristic_type'].strip()
                pattern = row['pattern'].strip()
                count = int(row['count'])
                counts[(tool, heuristic_type, pattern)] = count
            except (KeyError, ValueError) as e:
                print(f"Skipping row in {csv_path} due to error: {e} in row {row}")
                continue

    return counts


def group_by_tool(input_dir: Path, counts: dict[tuple[str, str, str], int]) -> dict:
    """
    Read all CSV files and group patterns by tool (subtype), including counts.

    Returns:
        dict mapping tool_name -> {
            'files': [(pattern, url, count), ...],
            'authors': [(pattern, url, count), ...],
            'coauthors': [(pattern, url, count), ...],
            'branches': [(pattern, url, count), ...],
            'labels': [(pattern, url, count), ...]
        }
    """
    tools = defaultdict(lambda: {
        'files': [],
        'authors': [],
        'coauthors': [],
        'branches': [],
        'labels': []
    })

    # Helper to get count
    def get_count(tool_name, heuristic_type, pattern):
        return counts.get((tool_name, heuristic_type, pattern), 0)

    # Process files
    files_path = input_dir / "files.csv"
    if files_path.exists():
        for row in read_patterns(files_path):
            tool_name = row['subtype']
            pattern = row['pattern']
            url = build_file_url(pattern)
            count = get_count(tool_name, 'file', pattern)
            tools[tool_name]['files'].append((pattern, url, count))

    # Process authors (both author and coauthor)
    authors_path = input_dir / "authors.csv"
    if authors_path.exists():
        for row in read_patterns(authors_path):
            tool_name = row['subtype']
            pattern = row['pattern']
            author_url = build_author_url(pattern)
            coauthor_url = build_coauthor_url(pattern)

            # Author
            author_count = get_count(tool_name, 'author', pattern)
            tools[tool_name]['authors'].append((pattern, author_url, author_count))

            # Co-author
            coauthor_count = get_count(tool_name, 'co_author', pattern)
            tools[tool_name]['coauthors'].append((pattern, coauthor_url, coauthor_count))

    # Process branches
    branches_path = input_dir / "branches.csv"
    if branches_path.exists():
        for row in read_patterns(branches_path):
            tool_name = row['subtype']
            pattern = row['pattern']
            url = build_branch_url(pattern)
            count = get_count(tool_name, 'branch', pattern)
            tools[tool_name]['branches'].append((pattern, url, count))

    # Process labels
    labels_path = input_dir / "labels.csv"
    if labels_path.exists():
        for row in read_patterns(labels_path):
            tool_name = row['subtype']
            pattern = row['pattern']
            url = build_label_url(pattern)
            count = get_count(tool_name, 'label', pattern)
            tools[tool_name]['labels'].append((pattern, url, count))

    return dict(tools)


def generate_csv_table(tools: dict, tool_urls: dict, output_path: Path):
    """Generate CSV table with double columns (pattern, URL, Count)."""
    fieldnames = [
        'Tool', 'Tool_URL',
        'File_Pattern', 'File_URL', 'File_Count',
        'Commit_CoAuth_Pattern', 'Commit_CoAuth_URL', 'Commit_CoAuth_Count',
        'Commit_Auth_Pattern', 'Commit_Auth_URL', 'Commit_Auth_Count',
        'Branch_Pattern', 'Branch_URL', 'Branch_Count',
        'Label_Pattern', 'Label_URL', 'Label_Count'
    ]

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for tool in sorted(tools.keys()):
            data = tools[tool]

            # Join multiple patterns/URLs/Counts with semicolon
            file_patterns = '; '.join(p for p, _, _ in data['files'])
            file_urls = '; '.join(u for _, u, _ in data['files'])
            file_counts = '; '.join(str(c) for _, _, c in data['files'])

            coauth_patterns = '; '.join(p for p, _, _ in data['coauthors'])
            coauth_urls = '; '.join(u for _, u, _ in data['coauthors'])
            coauth_counts = '; '.join(str(c) for _, _, c in data['coauthors'])

            auth_patterns = '; '.join(p for p, _, _ in data['authors'])
            auth_urls = '; '.join(u for _, u, _ in data['authors'])
            auth_counts = '; '.join(str(c) for _, _, c in data['authors'])

            branch_patterns = '; '.join(p for p, _, _ in data['branches'])
            branch_urls = '; '.join(u for _, u, _ in data['branches'])
            branch_counts = '; '.join(str(c) for _, _, c in data['branches'])

            label_patterns = '; '.join(p for p, _, _ in data['labels'])
            label_urls = '; '.join(u for _, u, _ in data['labels'])
            label_counts = '; '.join(str(c) for _, _, c in data['labels'])

            writer.writerow({
                'Tool': tool,
                'Tool_URL': tool_urls.get(tool, ''),
                'File_Pattern': file_patterns,
                'File_URL': file_urls,
                'File_Count': file_counts,
                'Commit_CoAuth_Pattern': coauth_patterns,
                'Commit_CoAuth_URL': coauth_urls,
                'Commit_CoAuth_Count': coauth_counts,
                'Commit_Auth_Pattern': auth_patterns,
                'Commit_Auth_URL': auth_urls,
                'Commit_Auth_Count': auth_counts,
                'Branch_Pattern': branch_patterns,
                'Branch_URL': branch_urls,
                'Branch_Count': branch_counts,
                'Label_Pattern': label_patterns,
                'Label_URL': label_urls,
                'Label_Count': label_counts
            })

    print(f"✅ CSV table generated: {output_path}")


def get_markdown_style(text: str, count: int) -> str:
    """Apply Markdown styling based on count thresholds."""
    if count < 20:
        return '' # Filtered out
    elif count < 5000:
        # Normal text for counts < 5K (combining lightgray, gray, and black tiers)
        return text
    elif count < 25000:
        # Italics
        return f'*{text}*'
    elif count < 100000:
        # Bold
        return f'**{text}**'
    else: # count >= 100000 (combining bold orange and bold red tiers)
        # Bold and Italics for maximum emphasis
        return f'***{text}***'


def generate_markdown_table(tools: dict, tool_urls: dict, output_path: Path):
    """Generate Markdown table with clickable links and count-based styling."""
    with open(output_path, 'w', encoding='utf-8') as f:
        # Header
        f.write("| Tool | File | Commit | Branches | Labels |\n")
        f.write("|------|------|--------|----------|--------|\n")

        for tool in sorted(tools.keys()):
            data = tools[tool]

            # Tool column with link to the per-tool markdown file
            sanitized_name = sanitize_filename(tool)
            tool_link = f"tools/{sanitized_name}.md"
            tool_cell = f"[{tool}]({tool_link})"

            # File column
            file_links = []
            for p, u, c in data['files']:
                styled_text = get_markdown_style(p, c)
                if styled_text:
                    file_links.append(f"[{styled_text}]({u})")
            file_links_str = ', '.join(file_links) if file_links else '-'

            # Commit column - pair coauthor and author links
            commit_parts = []
            max_len = max(len(data['coauthors']), len(data['authors']))
            for i in range(max_len):
                if i < len(data['coauthors']):
                    p, u, c = data['coauthors'][i]
                    styled_text = get_markdown_style(p, c)
                    if styled_text:
                        commit_parts.append(f"[{styled_text}]({u})")
                if i < len(data['authors']):
                    p, u, c = data['authors'][i]
                    styled_text = get_markdown_style('auth', c)
                    if styled_text:
                        commit_parts.append(f"[{styled_text}]({u})")
            commit_links_str = ', '.join(commit_parts) if commit_parts else '-'

            # Branches column
            branch_links = []
            for p, u, c in data['branches']:
                styled_text = get_markdown_style(p, c)
                if styled_text:
                    branch_links.append(f"[{styled_text}]({u})")
            branch_links_str = ', '.join(branch_links) if branch_links else '-'

            # Labels column
            label_links = []
            for p, u, c in data['labels']:
                styled_text = get_markdown_style(p, c)
                if styled_text:
                    label_links.append(f"[{styled_text}]({u})")
            label_links_str = ', '.join(label_links) if label_links else '-'

            f.write(f"| {tool_cell} | {file_links_str} | {commit_links_str} | {branch_links_str} | {label_links_str} |\n")

    print(f"✅ Markdown table generated: {output_path}")

def write_latex_command(f):
    # Write LaTeX command definition
    f.write("% Styling command for heuristic links based on result count\n")
    f.write("% Usage: \\heuristiclink{text}{url}{count}\n")
    f.write("% Count thresholds:\n")
    f.write("%   < 250: hidden (no output)\n")
    f.write("%   250-1000: gray text\n")
    f.write("%   1000-5000: black text (normal)\n")
    f.write("%   5K-25K: italics\n")
    f.write("%   25K-100K: bold black\n")
    f.write("%   100K-500K: bold orange\n")
    f.write("%   > 500K: bold red\n")
    f.write("% Automatically adds commas between links (but not after hidden or last link)\n")
    f.write("\\makeatletter\n")
    f.write("\\newcommand{\\heuristiclink}[3]{%\n")
    f.write("  \\ifnum#3<250\n")
    f.write("    % Hide links with < 250 results\n")
    f.write("  \\else\\ifnum#3<1000\n")
    f.write("    % 250-1000: gray\n")
    f.write("    {\\color{gray}\\href{#2}{#1}}%\n")
    f.write("    \\@ifnextchar\\heuristiclink{, }{}%\n")
    f.write("  \\else\\ifnum#3<5000\n")
    f.write("    % 1000-5000: normal black\n")
    f.write("    \\href{#2}{#1}%\n")
    f.write("    \\@ifnextchar\\heuristiclink{, }{}%\n")
    f.write("  \\else\\ifnum#3<25000\n")
    f.write("    % 5K-25K: italics\n")
    f.write("    \\textit{\\href{#2}{#1}}%\n")
    f.write("    \\@ifnextchar\\heuristiclink{, }{}%\n")
    f.write("  \\else\\ifnum#3<100000\n")
    f.write("    % 25K-100K: bold\n")
    f.write("    \\textbf{\\href{#2}{#1}}%\n")
    f.write("    \\@ifnextchar\\heuristiclink{, }{}%\n")
    f.write("  \\else\\ifnum#3<500000\n")
    f.write("    % 100K-500K: bold orange\n")
    f.write("    {\\color{orange}\\textbf{\\href{#2}{#1}}}%\n")
    f.write("    \\@ifnextchar\\heuristiclink{, }{}%\n")
    f.write("  \\else\n")
    f.write("    % > 500K: bold red\n")
    f.write("    {\\color{red}\\textbf{\\href{#2}{#1}}}%\n")
    f.write("    \\@ifnextchar\\heuristiclink{, }{}%\n")
    f.write("  \\fi\\fi\\fi\\fi\\fi\\fi\n")
    f.write("}\n")
    f.write("\\makeatother\n\n")

def generate_latex_table(tools: dict, tool_urls: dict, output_path: Path, min_tool_count: int):
    """Generate LaTeX table with clickable hyperlinks and count-based styling, with filtering."""
    def escape_latex_text(text: str) -> str:
        """Escape special LaTeX characters in visible text (labels)."""
        replacements = {
            '\\': r'\textbackslash{}',
            '&': r'\&',
            '%': r'\%',
            '$': r'\$',
            '#': r'\#',
            '_': r'\_',
            '{': r'\{',
            '}': r'\}',
            '~': r'\textasciitilde{}',
            '^': r'\textasciicircum{}',
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    def escape_latex_url(url: str) -> str:
        """Escape only LaTeX-breaking characters in URLs for \href{}."""
        replacements = {
            '\\': r'\\',
            '&': r'\&',
            '%': r'\%',
            '$': r'\$',
            '#': r'\#',
            '_': r'\_',
        }
        for old, new in replacements.items():
            url = url.replace(old, new)
        return url

    with open(output_path, 'w', encoding='utf-8') as f:
        # Write LaTeX command definition
        write_latex_command(f)

        # Table preamble
        f.write("\\begin{table*}[htbp]\n")
        f.write("\\centering\n")
        # Added a column for Total
        f.write("\\begin{tabular}{>{\\raggedright\\arraybackslash}p{2.5cm}>{\\raggedright\\arraybackslash}p{4cm}>{\\raggedright\\arraybackslash}p{3.5cm}>{\\raggedright\\arraybackslash}p{1.5cm}>{\\raggedright\\arraybackslash}p{1.5cm}>{\\raggedright\\arraybackslash}p{1.5cm}}\n")
        f.write("\\hline\n")
        f.write("\\textbf{Tool} & \\textbf{File} & \\textbf{Commit} & \\textbf{Branches} & \\textbf{Labels} & \\textbf{Total} \\\\\n")
        f.write("\\hline\n")

        for tool in sorted(tools.keys()):
            data = tools[tool]
            tool_escaped = escape_latex_text(tool)

            # Filter heuristics (count >= 250) and calculate total count
            filtered_files = [(p, u, c) for p, u, c in data['files'] if c >= 250]
            filtered_coauthors = [(p, u, c) for p, u, c in data['coauthors'] if c >= 250]
            filtered_authors = [(p, u, c) for p, u, c in data['authors'] if c >= 250]
            filtered_branches = [(p, u, c) for p, u, c in data['branches'] if c >= 250]
            filtered_labels = [(p, u, c) for p, u, c in data['labels'] if c >= 250]

            total_count = (
                sum(c for _, _, c in filtered_files) +
                sum(c for _, _, c in filtered_coauthors) +
                sum(c for _, _, c in filtered_authors) +
                sum(c for _, _, c in filtered_branches) +
                sum(c for _, _, c in filtered_labels)
            )

            # Tool filtering: skip if total count is less than min_tool_count
            if total_count < min_tool_count:
                continue

            # Tool column with optional hyperlink
            if tool in tool_urls:
                tool_cell = f"\\href{{{escape_latex_url(tool_urls[tool])}}}{{{tool_escaped}}}"
            else:
                tool_cell = tool_escaped

            # File column
            if filtered_files:
                file_links = ' '.join(
                    f"\\heuristiclink{{{escape_latex_text(p)} ({format_count(c)})}}{{{escape_latex_url(u)}}}{{{c}}}"
                    for p, u, c in filtered_files
                )
            else:
                file_links = '-'

            # Commit column
            commit_parts = []
            max_len = max(len(filtered_coauthors), len(filtered_authors))
            for i in range(max_len):
                if i < len(filtered_coauthors):
                    p, u, c = filtered_coauthors[i]
                    # Co-author: pattern (count)
                    formatted_text = f"{escape_latex_text(p)} ({format_count(c)})"
                    commit_parts.append(
                        f"\\heuristiclink{{{formatted_text}}}{{{escape_latex_url(u)}}}{{{c}}}"
                    )
                if i < len(filtered_authors):
                    p_auth, u, c = filtered_authors[i]
                    # Author: pattern (auth, count)
                    formatted_text = f"{escape_latex_text(p_auth)} (auth, {format_count(c)})"
                    commit_parts.append(
                        f"\\heuristiclink{{{formatted_text}}}{{{escape_latex_url(u)}}}{{{c}}}"
                    )
            commit_links = ' '.join(commit_parts) if commit_parts else '-'

            # Branches column
            if filtered_branches:
                branch_links = ' '.join(
                    f"\\heuristiclink{{{escape_latex_text(p)} ({format_count(c)})}}{{{escape_latex_url(u)}}}{{{c}}}"
                    for p, u, c in filtered_branches
                )
            else:
                branch_links = '-'

            # Labels column
            if filtered_labels:
                label_links = ' '.join(
                    f"\\heuristiclink{{{escape_latex_text(p)} ({format_count(c)})}}{{{escape_latex_url(u)}}}{{{c}}}"
                    for p, u, c in filtered_labels
                )
            else:
                label_links = '-'

            # Write table row
            f.write(f"{tool_cell} & {file_links} & {commit_links} & {branch_links} & {label_links} & {total_count:,} \\\\\n")
            f.write("\\hline\n")

        f.write("\\end{tabular}\n")
        f.write("\\caption{GitHub Search Heuristics by Tool}\n")
        f.write("\\label{tab:heuristics}\n")
        f.write("\\end{table*}\n")

    print(f"✅ LaTeX table generated: {output_path}")


def generate_per_tool_markdown(tools: dict, tool_urls: dict, output_dir: Path):
    """Generate individual markdown files for each tool."""

    def escape_markdown_url(url: str) -> str:
        """Escape characters that can interfere with Markdown formatting."""
        escape_map = {
            '|': r'\|',
            '[': r'\[',
            ']': r'\]',
            '(': r'\(',
            ')': r'\)',
        }
        for char, escaped in escape_map.items():
            url = url.replace(char, escaped)
        return url


    output_dir.mkdir(exist_ok=True)

    for tool in sorted(tools.keys()):
        data = tools[tool]
        filename = sanitize_filename(tool) + ".md"
        filepath = output_dir / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            # Title
            f.write(f"# {tool}\n\n")

            # Tool URL if available
            if tool in tool_urls:
                f.write(f"[Tool Website]({tool_urls[tool]})\n\n")

            # Files section
            f.write("## Files\n\n")
            if data['files']:
                for pattern, url, count in data['files']:
                    f.write(f"- [{pattern}]({url}) (Count: {count:,})\n")
            else:
                f.write("No heuristic yet\n")
            f.write("\n")

            # Commits section
            f.write("## Commits\n\n")
            if data['coauthors'] or data['authors']:
                for i in range(max(len(data['coauthors']), len(data['authors']))):
                    parts = []
                    if i < len(data['coauthors']):
                        p, u, c = data['coauthors'][i]
                        parts.append(f"[{p}]({u}) (Count: {c:,})")
                    if i < len(data['authors']):
                        _, u, c = data['authors'][i]
                        parts.append(f"[auth]({u}) (Count: {c:,})")
                    f.write(f"- {', '.join(parts)}\n")
            else:
                f.write("No heuristic yet\n")
            f.write("\n")

            # Branches section
            f.write("## Branches\n\n")
            if data['branches']:
                for pattern, url, count in data['branches']:
                    f.write(f"- [{pattern}]({url}) (Count: {count:,})\n")
            else:
                f.write("No heuristic yet\n")
            f.write("\n")

            # Labels section
            f.write("## Labels\n\n")
            if data['labels']:
                for pattern, url, count in data['labels']:
                    f.write(f"- [{pattern}]({url}) (Count: {count:,})\n")
            else:
                f.write("No heuristic yet\n")
            f.write("\n")

    print(f"✅ Per-tool markdown files generated in {output_dir}/ ({len(tools)} files)")

def main():
    parser = argparse.ArgumentParser(
        description="Generate GitHub search URL tables from heuristic pattern CSVs.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "input_directory",
        type=Path,
        help="The directory containing authors.csv, files.csv, branches.csv, labels.csv, and tools.csv (optional)."
    )
    parser.add_argument(
        "--per-tool-md",
        action="store_true",
        help="Generate individual markdown files for each tool in a 'tools/' directory."
    )
    parser.add_argument(
        "--min-tool-count",
        type=int,
        default=1000,
        help="Minimum total heuristic count for a tool to be included in the LaTeX table (default: 1000)."
    )
    args = parser.parse_args()

    input_dir = args.input_directory
    min_tool_count = args.min_tool_count

    if not input_dir.is_dir():
        print(f"Error: {input_dir} is not a directory")
        sys.exit(1)

    # Check for required files
    required_files = ['authors.csv', 'files.csv', 'branches.csv', 'labels.csv']
    for filename in required_files:
        if not (input_dir / filename).exists():
            print(f"Warning: {filename} not found in {input_dir}")

    # Read tool URLs (optional)
    tool_urls = read_tool_urls(input_dir / "tools.csv")
    if tool_urls:
        print(f"Loaded URLs for {len(tool_urls)} tools")

    # Read heuristic counts
    counts = read_counts(input_dir / "counts.csv")
    print(f"Loaded counts for {len(counts)} heuristics")

    # Read and group patterns by tool
    print(f"Reading patterns from {input_dir}...")
    tools = group_by_tool(input_dir, counts)
    print(f"Found {len(tools)} tools")

    # Generate output files in current directory
    generate_csv_table(tools, tool_urls, Path("heuristics.csv"))
    generate_markdown_table(tools, tool_urls, Path("heuristics.md"))
    generate_latex_table(tools, tool_urls, Path("heuristics.tex"), min_tool_count)

    # Generate per-tool markdown if requested
    if args.per_tool_md:
        generate_per_tool_markdown(tools, tool_urls, Path("tools"))

    print("\n✅ All tables generated successfully!")


if __name__ == "__main__":
    main()

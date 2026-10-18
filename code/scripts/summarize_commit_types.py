#!/usr/bin/env python3
import os
import csv
import sys
from pathlib import Path
from collections import Counter, defaultdict

# ✅ Increase CSV field size limit to handle big commit messages
csv.field_size_limit(sys.maxsize)

def main(base_dir):
    base = Path(base_dir)
    output_file = Path("auth_summary.csv")

    # First pass: discover all tools (so we know all columns)
    all_tools = set()
    dir_data = []

    for entry in base.iterdir():
        if not entry.is_dir():
            continue

        dir_path = entry.resolve()
        commit_csv = dir_path / "commit_stats_main.csv"
        adoption_csv = dir_path / "adoption_dates.csv"

        counts = Counter({"Human": 0, "Bot": 0, "AI": 0, "AI-FOLLOWUP": 0})
        tools = set()

        # --- Count auth_type values ---
        if commit_csv.exists():
            try:
                with open(commit_csv, newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        auth_type = row.get("auth_type", "").strip()
                        if auth_type in counts:
                            counts[auth_type] += 1
            except Exception as e:
                print(f"⚠️ Error reading {commit_csv}: {e}")

        # --- Read tool names ---
        if adoption_csv.exists():
            try:
                with open(adoption_csv, newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        tool = row.get("tool", "").strip()
                        if tool:
                            tools.add(tool)
                            all_tools.add(tool)
            except Exception as e:
                print(f"⚠️ Error reading {adoption_csv}: {e}")

        dir_data.append({
            "dir": entry.name,
            "Human": counts["Human"],
            "Bot": counts["Bot"],
            "AI": counts["AI"],
            "AI-FOLLOWUP": counts["AI-FOLLOWUP"],
            "tools": tools
        })

    # --- Prepare header ---
    base_fields = ["dir", "Human", "Bot", "AI", "AI-FOLLOWUP"]
    tool_fields = sorted(all_tools)
    fieldnames = base_fields + tool_fields

    # --- Write output CSV ---
    with open(output_file, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()

        for d in dir_data:
            row = {k: d[k] for k in base_fields}
            for t in tool_fields:
                row[t] = "true" if t in d["tools"] else ""
            writer.writerow(row)

    print(f"✅ Wrote summary to {output_file}")
    print(f"🧰 Tools found: {', '.join(tool_fields) if tool_fields else '(none)'}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <dir_with_links>")
        sys.exit(1)
    main(sys.argv[1])

# python scripts/tool_use_percent.py
# takes as input the output of scripts/count_org_repos.py, but with some manual changes for a manual grouping

import pandas as pd
import re
from pathlib import Path
import subprocess

from reports.common import merge_dataframes_by_github_url, default_args, filter_df, find_and_select_report, set_input_path, write_file

# === Input file paths ===
orgs_file = "../config/patterns/top-orgs.csv"         # CSV with org names (no header)
groups_file = "../data/org_repo_counts.csv"     # Output CSV
output_file = "../temp/org_adoption.csv"



parser = default_args("Generates decile-based adoption statistics ")
parser.add_argument("--metric-file", default="../data/projects-29-08.csv", help="Path where project metrics are, if needed")
args = parser.parse_args()
set_input_path(args.input)
adoption_file = find_and_select_report(Path(args.input))



# === Read input files ===
groups_df = pd.read_csv(groups_file)
repos_df = merge_dataframes_by_github_url(args.metric_file, adoption_file)
repos_df = filter_df(repos_df, args, "github_url")


# === Clean up ===
repos_df['tool_use'] = repos_df['tool_use'] | repos_df['ignored_tools']
repos_df["tool_use"] = repos_df["tool_use"].astype(str).str.upper().eq("TRUE")
groups_df = groups_df.dropna(subset=["group"])  # drop orgs with no group

# === Build group mapping: group -> list of orgs ===
group_to_orgs = (
    groups_df.groupby("group")["organization"]
    .apply(list)
    .to_dict()
)

# === Function to check if URL belongs to an org ===
def url_matches_org(url, org):
    pattern = fr"github\.com/{re.escape(org)}(/|$)"
    return re.search(pattern, url, re.IGNORECASE) is not None

# === Compute tool_use percentage for each group ===
results = []
for group, orgs in group_to_orgs.items():
    # Find repos matching any org in this group
    mask = repos_df["github_url"].apply(
        lambda url: any(url_matches_org(url, org) for org in orgs)
    )
    group_repos = repos_df[mask]

    if len(group_repos) == 0:
        continue

    tool_use_pct = group_repos["tool_use"].mean() * 100
    repo_count = len(group_repos)
    results.append({
        "group": group,
        "tool_use_percentage": round(tool_use_pct, 2),
        "repo_count": repo_count
    })
# === Save results ===
result_df = pd.DataFrame(results).sort_values(by="repo_count", ascending=False)
result_df.to_csv(output_file, index=False)

print(f"✅ Done! Results saved to {output_file}")

# === Compute overall adoption rate (same logic as basic_stats.py) ===
total_projects = len(repos_df)
known_users_count = repos_df[repos_df["tool_use"] | repos_df["ignored_tools"]].shape[0]
extrapolation_group_total = repos_df[(~repos_df["tool_use"]) & (~repos_df["ignored_tools"])].shape[0]
sampled_extrapolation_group = repos_df[
    (repos_df["sampled"]) & (~repos_df["tool_use"]) & (~repos_df["ignored_tools"])
]

commit_use_rate_in_sample = 0.0
if not sampled_extrapolation_group.empty:
    commit_use_rate_in_sample = sampled_extrapolation_group["commit_use"].sum() / len(sampled_extrapolation_group)

extrapolated_commit_users = extrapolation_group_total * commit_use_rate_in_sample
total_users_refined = known_users_count + extrapolated_commit_users
overall_adoption_rate = (total_users_refined / total_projects) * 100

# === Compute macro and micro averages ===
result_df = result_df.head(20)  # Top 20 organizations
macro_avg = result_df["tool_use_percentage"].mean()
micro_avg = (result_df["tool_use_percentage"] * result_df["repo_count"]).sum() / result_df["repo_count"].sum()

# === Compute relative difference ===
relative_diff_macro = ((macro_avg - overall_adoption_rate) / overall_adoption_rate) * 100
relative_diff_micro = ((micro_avg - overall_adoption_rate) / overall_adoption_rate) * 100

# === Print numeric summary ===
print("\n=== Summary of tool_use adoption ===")
print(f"Macro average (unweighted): {macro_avg:.2f}%")
print(f"Micro average (weighted by repo count): {micro_avg:.2f}%")
print(f"Overall adoption rate: {overall_adoption_rate:.2f}%")
print(f"Relative difference (macro): {relative_diff_macro:+.2f}%")
print(f"Relative difference (micro): {relative_diff_micro:+.2f}%")

# === Define LaTeX commands for inclusion in paper ===
latex_macros = f"""% ==========================================
% LaTeX command definitions for averages
% ==========================================
\\newcommand{{\\TopOrganizationsMacroToolUse}}{{{macro_avg:.2f}\\%}}
\\newcommand{{\\TopOrganizationsMicroToolUse}}{{{micro_avg:.2f}\\%}}
\\newcommand{{\\TopOrganizationsRelativeDiffMacro}}{{{relative_diff_macro:+.2f}\\%}}
\\newcommand{{\\TopOrganizationsRelativeDiffMicro}}{{{relative_diff_micro:+.2f}\\%}}
"""

print(latex_macros)

# === Define per-group LaTeX commands ===
latex_cmds = ["% ==========================================",
              "% LaTeX command definitions per group",
              "% =========================================="]

for _, row in result_df.iterrows():
    group = row["group"]
    pct = row["tool_use_percentage"]
    repos = int(row["repo_count"])
    # Create safe command names (letters only, no dashes or numbers)
    safe_group = ''.join(ch for ch in group.title() if ch.isalnum())
    latex_cmds.append(f"\\newcommand{{\\{safe_group}ToolUse}}{{{pct:.2f}\\%}}")
    latex_cmds.append(f"\\newcommand{{\\{safe_group}RepoCount}}{{{repos}}}")

latex_cmd_block = "\n".join(latex_cmds)
print(latex_cmd_block)
# === Generate compact two-column LaTeX table using macros ===
latex_df = result_df.sort_values(by="repo_count", ascending=False).reset_index(drop=True)

# Capitalize group names
latex_df["GroupName"] = latex_df["group"].str.title()

# Build the LaTeX-safe command names
latex_df["MacroName"] = latex_df["group"].apply(lambda g: ''.join(ch for ch in g.title() if ch.isalnum()))

# Create references to LaTeX macros
latex_df["ToolUseCmd"] = latex_df["MacroName"].apply(lambda x: f"\\{x}ToolUse")
latex_df["RepoCountCmd"] = latex_df["MacroName"].apply(lambda x: f"\\{x}RepoCount")

# Split into two halves
mid = (len(latex_df) + 1) // 2
left = latex_df.iloc[:mid].reset_index(drop=True)
right = latex_df.iloc[mid:].reset_index(drop=True)

# Pad right side if needed
while len(right) < len(left):
    right.loc[len(right)] = [None] * len(right.columns)

# Build custom LaTeX table with better header
latex_table = "\\begin{table}[ht]\n"
latex_table += "\\scriptsize\n"
latex_table += "\\centering\n"
latex_table += "\\caption{File-based adoption for top 20 organizations}\n"
latex_table += "\\label{tab:organization_adoption}\n"
latex_table += "\\begin{tabular}{l|rr@{\\hspace{0.5cm}}|@{\\hspace{0.5cm}}l|rr}\n"
latex_table += "\\toprule\n"
latex_table += "\\textbf{Organization} & \\textbf{Adoption pct} & \\textbf{Repositories} & \\textbf{Organization} & \\textbf{Adoption pct} & \\textbf{Repositories} \\\\\n"
latex_table += "\\midrule\n"

# Add data rows
for i in range(len(left)):
    left_org = left.iloc[i]["GroupName"] if pd.notna(left.iloc[i]["GroupName"]) else ""
    left_adopt = left.iloc[i]["ToolUseCmd"] if pd.notna(left.iloc[i]["ToolUseCmd"]) else ""
    left_repos = left.iloc[i]["RepoCountCmd"] if pd.notna(left.iloc[i]["RepoCountCmd"]) else ""
    
    right_org = right.iloc[i]["GroupName"] if pd.notna(right.iloc[i]["GroupName"]) else ""
    right_adopt = right.iloc[i]["ToolUseCmd"] if pd.notna(right.iloc[i]["ToolUseCmd"]) else ""
    right_repos = right.iloc[i]["RepoCountCmd"] if pd.notna(right.iloc[i]["RepoCountCmd"]) else ""
    
    latex_table += f"{left_org} & {left_adopt} & {left_repos} & {right_org} & {right_adopt} & {right_repos} \\\\\n"

latex_table += "\\bottomrule\n"
latex_table += "\\end{tabular}\n"
latex_table += "\\end{table}\n"

print("\n=== LaTeX table (two-column layout, capitalized, macro-based) ===")
print(latex_table)


# Build complete output
output_content = "% Generated by reports/top_organizations.py\n"
output_content += latex_macros
output_content += "\n\n"
output_content += latex_cmd_block
output_content += "\n\n%====== \n\n"
output_content += latex_table

# Save using write_file
write_file("rq3/organization_adoption.tex", output_content)
print(f"\n\nLaTeX table saved to rq3/organization_adoption.tex")

import pandas as pd

# === Input file paths ===
orgs_file = "../config/patterns/top-orgs.csv"         # CSV with org names (no header)
repos_file = "../data/projects-29-08.csv"                # CSV with column 'github_url'
output_file = "../temp/org_repo_counts.csv"     # Output CSV

# === Read input CSVs ===
orgs = pd.read_csv(orgs_file, header=None, names=["organization"])
repos = pd.read_csv(repos_file)

# === Ensure github_url column exists ===
if "github_url" not in repos.columns:
    raise ValueError("The repos CSV must have a 'github_url' column.")

# === Count matches for each organization ===
counts = []
for org in orgs["organization"]:
    # Count URLs containing the org name in the expected position
    match_count = repos["github_url"].str.contains(
        fr"github\.com/{org}(/|$)", case=False, regex=True
    ).sum()
    counts.append({"organization": org, "repo_count": match_count})

# === Create output DataFrame ===
result = pd.DataFrame(counts)

# === Save to CSV ===
result.to_csv(output_file, index=False)

print(f"✅ Done! Results saved to {output_file}")


#!/bin/bash

set -euo pipefail

# Default values
ADOP="../temp/new_adopters/"
METRICS="../data/projects-aug-25-feb-26.csv"
STEP=0  # 0 = run all
OUTPUT_DIR=""
# Change this to match your Python installation
# Examples: "python", "python3", "uv run python", "poetry run python"
PY="uv run python"

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --step)
      STEP="$2"
      shift 2
      ;;
    --output)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --adopters)
      ADOP="$2"
      shift 2
      ;;
    --metrics)
      METRICS="$2"
      shift 2
      ;;
    --help)
      echo "Usage: ./total_repro.sh [--step N] [--output DIR] [--adopters DIR] [--metrics FILE]"
      echo "  --step N        Run only step N (0 = all steps)"
      echo "  --output DIR    Output directory (required first positional arg)"
      echo "  --adopters DIR  Adopters directory (default: ../temp/new_adopters/)"
      echo "  --metrics FILE  Metrics file (default: ../data/projects-aug-25-feb-26.csv)"
      exit 0
      ;;
    *)
      # First positional arg is output directory
      if [[ -z "$OUTPUT_DIR" ]]; then
        OUTPUT_DIR="$1"
      fi
      shift
      ;;
  esac
done

# Use ADOP as default if OUTPUT_DIR not provided
if [[ -z "$OUTPUT_DIR" ]]; then
  OUTPUT_DIR="$ADOP"
fi

if [[ -z "$OUTPUT_DIR" ]]; then
  echo "Error: output directory required"
  exit 1
fi

REPORT_FILE=$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name 'analysis_report_*.csv' -print -quit)
if [[ -z "$REPORT_FILE" ]]; then
  echo "Error: no analysis report found in $OUTPUT_DIR" >&2
  echo "Run run.py successfully before generating reports." >&2
  exit 1
fi

if [[ $(wc -l < "$REPORT_FILE") -le 1 ]]; then
  echo "Error: analysis report contains no successful projects: $REPORT_FILE" >&2
  echo "Check retry_list.csv and fix the analysis errors before generating reports." >&2
  exit 1
fi

export OUT="$OUTPUT_DIR/report"

mkdir -p "$OUT"
mkdir -p "$OUT/rq1"
mkdir -p "$OUT/rq2"

run_step() {
  local step_num=$1
  local description=$2
  if [[ $STEP -eq 0 || $STEP -eq $step_num ]]; then
    echo "Running step $step_num: $description"
    shift 2
    "$@"
  fi
}

# Step 1: overall adoption table
run_step 1 "overall adoption table" \
  $PY reports/basic_stats.py $ADOP --exclude dotfiles

# Step 2: file level adoption and commit level adoption
run_step 2 "file level adoption and commit level adoption" \
  $PY reports/tool_metrics_table_spark.py $ADOP --exclude dotfiles

# Step 3: correlation between adoption metrics
run_step 3 "correlation between adoption metrics" \
  $PY reports/tool_metrics_report.py $ADOP --exclude dotfiles

# Step 4: file adoption stats vs project-level metrics (part 1)
run_step 4 "file adoption stats (baseline)" \
  $PY reports/table_ratios.py --exclude dotfiles $ADOP --metric-file $METRICS

# Step 4: file adoption stats vs project-level metrics (part 2)
run_step 4 "file adoption stats (no-one-year)" \
  $PY reports/table_ratios.py --exclude dotfiles $ADOP --metric-file $METRICS --no-one-year

# Step 4: file adoption stats vs project-level metrics (part 3)
run_step 4 "file adoption stats (no-two-year)" \
  $PY reports/table_ratios.py --exclude dotfiles $ADOP --metric-file $METRICS --no-two-year

# Step 5: commit adoption by project categories
run_step 5 "commit adoption by project categories" \
  $PY reports/commit_ratio_spark.py --exclude dotfiles $ADOP --metric-file $METRICS

# Step 6: top 20 organizations
run_step 6 "top 20 organizations" \
  $PY reports/top_organizations.py --exclude dotfiles $ADOP --metric-file $METRICS

# Step 7: topic adoption
run_step 7 "topic adoption" \
  $PY reports/repo_properties_report.py --exclude dotfiles $ADOP --metric-file $METRICS --topic-threshold 100 --lang-threshold 50

# Step 8: consolidate metrics for programming languages
run_step 8 "consolidate metrics for programming languages" \
  $PY reports/consolidate_metrics.py --exclude dotfiles $ADOP --metric-file $METRICS

# Step 8: ratio by programming language
run_step 8 "ratio by programming language" \
  $PY reports/ratio_pl_report.py --exclude dotfiles $ADOP

# Step 9: timeline evolution figures
run_step 9 "timeline evolution figures" \
  $PY reports/timelines.py --exclude dotfiles $ADOP

# Step 10: commit size figures
run_step 10 "commit size figures" \
  $PY reports/commit_sizes.py --exclude dotfiles --no-cache $ADOP

echo "✓ Complete"

#!/bin/bash

# Full reproduction script that runs run.py followed by run_analysis.sh
# Usage: ./full_reproduction.sh <repo_list> <output_dir> [num_workers]

set -euo pipefail

# Change this to match your Python installation
# Examples: "python", "python3", "uv run python", "poetry run python"
PY="uv run python"

# Parse arguments
ANALYSIS_DATE="2025-01-01"

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <repo_list> <output_dir> [options]"
    echo "  <repo_list>        CSV file containing projects to analyze"
    echo "  <output_dir>       Full path to output directory (will be created if needed)"
    echo "  Options:"
    echo "    --num-workers N     Number of parallel workers (default: 12)"
    echo "    --analysis-date D   Analysis start date in YYYY-MM-DD format (default: 2025-01-01)"
    exit 1
fi

REPO_LIST="$1"
OUTPUT_DIR="$2"
NUM_WORKERS=12
shift 2

# Parse optional arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --num-workers)
            NUM_WORKERS="$2"
            shift 2
            ;;
        --analysis-date)
            ANALYSIS_DATE="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Validate that the file exists
if [[ ! -f "$REPO_LIST" ]]; then
    echo "Error: File not found: $REPO_LIST"
    exit 1
fi

echo "Running full reproduction..."
echo "  Repository list: $REPO_LIST"
echo "  Output directory: $OUTPUT_DIR"
echo "  Workers: $NUM_WORKERS"
echo "  Analysis start date: $ANALYSIS_DATE"
echo ""

# Create output directory if needed
mkdir -p "$OUTPUT_DIR"

# Step 1: Run run.py
echo "Step 1: Running run.py..."
$PY run.py "$REPO_LIST" "$(basename "$OUTPUT_DIR")" --max-workers "$NUM_WORKERS" --analysis-date "$ANALYSIS_DATE" --output-dir "$(dirname "$OUTPUT_DIR")" --analyze-all

# Step 2: Run run_analysis.sh
echo ""
echo "Step 2: Running run_analysis.sh..."
./run_analysis.sh --output "$OUTPUT_DIR" --adopters "$OUTPUT_DIR" --metrics "$REPO_LIST"

echo ""
echo "✓ Full reproduction complete"

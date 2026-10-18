# Agent Adoption Study

Empirical study on the adoption and usage of software engineering agents in Github. Code and initial data for the paper: "Agentic Much? Adoption of Coding Agents on GitHub" (https://arxiv.org/abs/2601.18341). The data produced by the mining, used in the paper, is available on Zenodo (https://zenodo.org/records/19256968).

- code contains the code and its configuration; see AGENTS.md there for an overview
- data contains projects lists to kick start the analysis
- config contains the patterns we look for, your github tokens, the github yaml linguist stuff
- a temp directory is generated with all the data generated

## Setup

The recommended way to set up the project is using [uv](https://docs.astral.sh/uv/):

```bash
cd code
uv sync
```

This will install all dependencies from `pyproject.toml`.

For manual setup with pip:

```bash
pip install -r ../requirements.txt
```

## Dependencies

Requires Python 3.12+. Key dependencies include:

- pandas
- pyyaml
- requests
- seaborn
- tqdm
- upsetplot
- cliffs_delta
- adjustText
- emoji
- statsmodels
- scikit-learn
- plotly

## How to run the data gathering

- add one or more github tokens in config/tokens.ini, following config/tokens.ini.template

### Quick Start: Using `full_reproduction.sh`

The recommended way to run the full pipeline (analysis + reports):

```bash
cd code
./full_reproduction.sh ../data/<project csv> ../temp/my_experiment
```

This runs both `run.py` (data gathering) and `run_analysis.sh` (report generation) in sequence. When the number of repositories is large (e.g. 130k repositories), the data gathering phase can be very long, and consume a lot of space (more than one terabyte).

Options:
```bash
./full_reproduction.sh ../data/<project csv> ../temp/my_experiment --num-workers 24 --analysis-date 2025-01-01
```

The data directories has several datasets to run on:
- `data/projects-29-08.csv`: dataset used in the paper.
- `data/projects-aug-25-feb-26.csv`: a more recent sample of projects
- `data/new_600.csv`: first 600 lines of the previous, for a quicker check.

### Running the analysis only
This is possible if you already have some data, for instance if you download the data from Zenodo (https://zenodo.org/records/19256968).

### Manual Run: Using `run.py` directly

- in the code directory, run the following for a small test (20 projects, a couple of minutes):
```
python run.py ../data/claude-test.csv ../data/claude-test.csv git-test
```
- the first argument is the list of projects to analyze; the second is the list of projects to sample; the third is the name of the directory in ../temp

- to run on all the data (overnight, maybe start early, ~300 GBs):
```
python run.py ../data/projects-29-08.csv ../data/non_adopters_10k.csv <dir_name>
```

- or for full analysis of all projects (no sampling):
```
python run.py ../data/projects.csv <dir_name> --analyze-all
```
- this generates a lot of data:
	- an overall analysis report with the main petrics
	- metrics about the pull requests gathered for each project
	- most important commit ratios computed

- in addition each project has data in its directory, which is found in a subdirectory
	- tool_only_adopters 
	- commit_only_adopters
	- tool_commit_adopters
	- non_adopters

- in each project directory, you will have:
	- file lists
	- file matching a file heuristic checked out
	- pull request data
	- commit data
	- commit statistics
	- computed metrics and commit ratios



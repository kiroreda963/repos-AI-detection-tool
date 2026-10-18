from datetime import datetime, timedelta, timezone
from copy import copy
from typing import Callable, Literal
from dataclasses import dataclass
from collections import defaultdict



from utils import logging
from utils.data import Author
from utils.metrics import CommitStats, FileStats
from utils.file_types import classify_file_type_language
from heuristics import commit_type_tool
from pr_merge_types import ClassifiedPR
from tag_commits import CommitTagger
from match_authors import deduplicate_authors, AuthorMatcher

def split_by_type_language(all_stats: list[CommitStats]):
    by_type: defaultdict[str, list[CommitStats]] = defaultdict(list)
    by_language: defaultdict[str, list[CommitStats]] = defaultdict(list)

    for stat in all_stats:
        cm_types: defaultdict[str, list[FileStats]] = defaultdict(list)
        cm_languages: defaultdict[str, list[FileStats]] = defaultdict(list)

        for file_stat in stat.file_stats:
             type, language = classify_file_type_language(file_stat.path)
             #logging.info(f"Classified          {file_stat.path}")
             #logging.info(f"        type:       {type}")
             #logging.info(f"        language:   {language}")
             cm_types[type].append(file_stat)
             cm_languages[language].append(file_stat)

        for key, stats in cm_types.items():
            sub_commit: CommitStats = copy(stat)
            sub_commit.file_stats = stats
            by_type[key].append(sub_commit)

        for key, stats in cm_languages.items():
            sub_commit_2: CommitStats = copy(stat)
            sub_commit_2.file_stats = stats
            by_language[key].append(sub_commit_2)

    return by_type, by_language

def split_by_commit_type(all_stats: list[CommitStats]):
    commit_type: defaultdict[str, list[CommitStats]] = defaultdict(list)
    for stat in all_stats:
        commit_type[stat.type].append(stat)
    return commit_type



def classify_commits_classified_prs(
    log_commit_stats: list[CommitStats],
    pull_requests: list[ClassifiedPR]
    )-> list[CommitStats]:
    """
    - map all PR commits to main branch commits, including the case of rebase, stack merge, etc
    - handles the fact that commits can appear in several PRs
    - from PR information and author information, infer whether they are human, AI, or bot commits
    - map all commits to their tool, if any
    - save this information in CommitStats
    - remove merge commits we don't care about
    """
    sha_to_stats = {lcs.commit.sha : lcs for lcs in log_commit_stats}
    tagger = CommitTagger(similarity_threshold = 0.4)
    remove_commits: set[str] = set()
    orphan_merges: set[str] = set()

    # step 1: assign categories to the commit stats from the main branch
    for stat in log_commit_stats:
        stat.update_type(commit_type_tool(stat.commit))
        if stat.commit.is_log_merge() or stat.commit.is_orphan():
            orphan_merges.add(stat.commit.sha)

    # step 2: refine categores with PR info
    for cl_pr in pull_requests:
        pr_tags = tagger.process_pr(cl_pr.pr)
        for hash, typetool in pr_tags.items():
            if hash in sha_to_stats:
                sha_to_stats[hash].update_type(typetool)
            elif hash in cl_pr.commit_mapping:
               mapped_hash =  cl_pr.commit_mapping[hash]
               if mapped_hash in sha_to_stats:
                   sha_to_stats[mapped_hash].update_type(typetool)

        # exclude merge commits when the real commits are in the main branch
        match cl_pr.merge_type:
            case "rebase"|"merge"|"partial-rebase"|"merge (fast-forward)":
                remove_commits.add(cl_pr.pr.merge_commit_sha)
            case _:
                pass

    # remove merge commits or orphans
    result = [lcs for hash, lcs in sha_to_stats.items() if hash not in remove_commits and hash not in orphan_merges]
    return result

def get_ratio(num: int, den: int):
    if not den:
        # we return None to differentiate a true zero ratio from "no info"
        return 0, None
    return den, round(100 * (num / den), 3)

def compute_ratio(stats: list[CommitStats], metric: Callable[[CommitStats],int]) -> tuple[int,float|None]:
    # we won't access "BOT" commits
    groups = split_by_commit_type(stats)

    sum_ai_metrics = sum([metric(stat) for stat in groups["AI"]])
    sum_human_metrics = sum([metric(stat) for stat in groups["Human"]])

    return get_ratio(sum_ai_metrics, sum_ai_metrics + sum_human_metrics)

def commit_ratio(stats: list[CommitStats]):
    return compute_ratio(stats, lambda st: 1)

def added_lines_ratio(stats: list[CommitStats]):
    return compute_ratio(stats, lambda st: sum([fst.added_lines for fst in st.file_stats]))

def deleted_lines_ratio(stats: list[CommitStats]):
    return compute_ratio(stats, lambda st: sum([fst.deleted_lines for fst in st.file_stats]))

def add_minus_del_ratio(stats: list[CommitStats]):
    return compute_ratio(stats, lambda st: sum([fst.added_lines - fst.deleted_lines for fst in st.file_stats]))

###
# this is broken, it should take into account not only the main author ...
# also, when resuming, the directories should not move again
# otherwise we have a/a/a/a/a/a ....
###
def human_user_ratio(stats: list[CommitStats]):
    all_authors: set[Author] = set()
    ai_using_authors: set[Author] = set()

    for st in stats:
        all_authors.add(st.commit.author)
        if st.type == "AI":
            ai_using_authors.add(st.commit.author)

    return get_ratio(len(ai_using_authors), len(all_authors))

    """
    logging.info(f"all authors before dedup: {len(all_authors)}")
    logging.info(f"all AI authors before dedup: {len(ai_using_authors)}")


    matcher = AuthorMatcher(threshold=1)
    deduped_all = deduplicate_authors(list(all_authors), matcher)
    logging.info(f"all authors after dedup: {len(deduped_all)}")

    # Map subset authors into canonical groups from all_authors
    final_subset: set[Author] = {
            a for a in deduped_all
            if any(matcher.authors_match(a, s) for s in ai_using_authors)
        }

    logging.info(f"all AI authors after dedup: {len(final_subset)}")


    if not deduped_all: return 0, 0.0

    return len(deduped_all), (len(final_subset) / len(deduped_all))
    """

def touched_file_ratio(stats: list[CommitStats]):
    all_files: set[str] = set()
    all_ai_files: set[str] = set()

    for st in stats:
        paths = [f.path for f in st.file_stats]
        all_files.update(paths)
        if st.type == "AI":
            all_ai_files.update(paths)

    return get_ratio(len(all_ai_files), len(all_files))

@dataclass
class RatioMetrics:
    name: str
    type: str
    period_start: datetime|None
    period_end: datetime|None
    commits: int
    commit_ratio: float|None
    added_lines: int
    added_lines_ratio: float|None
    deleted_lines: int
    deleted_lines_ratio: float|None
    add_minus_del: int
    add_minus_del_ratio: float|None
    contributors: int
    contributor_ratio: float|None
    files: int
    files_ratio: float|None

def empty_ratio_metrics(name: str):
    return RatioMetrics(name, "default", None, None, 0, None, 0, None, 0, None, 0, None, 0, None, 0, None)

def compute_ratios(name: str, stats: list[CommitStats], start: datetime|None = None, end: datetime|None = None, with_types: bool = False, with_languages: bool = False)-> list[RatioMetrics]:
    logging.info(f"computing ratios for {len(stats)} commits")
    all_buckets: list[tuple[str, list[CommitStats]]] = [("overall", stats)]
    if with_types or with_languages:
        by_type, by_language = split_by_type_language(stats)
        if with_types: all_buckets.extend(by_type.items())
        if with_languages: all_buckets.extend(by_language.items())
    all_stats: list[RatioMetrics] = []
    for bucket_name, bucket_stats in all_buckets:
        commits, cmr = commit_ratio(bucket_stats)
        added_lines, alr = added_lines_ratio(bucket_stats)
        deleted_lines, dlr = deleted_lines_ratio(bucket_stats)
        add_minus_del, amdr = add_minus_del_ratio(bucket_stats)
        human_users, hmr = human_user_ratio(bucket_stats)
        files, fr = touched_file_ratio(bucket_stats)
        all_stats.append(RatioMetrics(name, bucket_name, start, end, commits, cmr, added_lines, alr, deleted_lines, dlr, add_minus_del, amdr, human_users, hmr, files, fr))
    return all_stats

def compute_ratios_by_periods(name: str, stats: list[CommitStats], periods: list[datetime], with_types: bool = False, with_languages: bool = False) -> list[RatioMetrics]:
    """
    Split CommitStats into groups by time periods, then run compute_all_ratios on each group.
    periods = [t0, t1, t2] → creates 2 periods: [t0, t1) and [t1, t2)
    Returns a flat list of RatioMetrics with period_start/period_end filled in.
    """
    if len(periods) < 2:
        raise ValueError("Need at least two timestamps to define periods.")

    all_results: list[RatioMetrics] = []

    # For each consecutive pair (period boundary)
    for start, end in zip(periods[:-1],periods[1:]):
        # Filter commits in this period: start <= date < end
        period_stats = [s for s in stats if start <= s.commit.date < end]
        metrics = compute_ratios(name, period_stats, start=start, end=end, with_types=with_types, with_languages=with_languages)
        all_results.extend(metrics)

    return all_results

def parse_date(d: str|datetime) -> datetime: return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc) if isinstance(d, str) else d

TimeInterval = Literal["weekly","monthly","yearly"]

def generate_periods(start: str|datetime, end: str|datetime, interval: TimeInterval) -> list[datetime]:
    """
    Generate period start/end timestamps between `start` and `end`, using a specified interval.

    Args:
        start: Start date as str ("YYYY-MM-DD") or datetime
        end: End date as str or datetime
        interval: Interval specification
        - "weekly"    → every 7 days
        - "monthly"   → first day of each month
        - "yearly"    → January 1 of each year

    Returns:
        list[datetime]: Start dates of each period (last one is before end)
    """


    start_dt = parse_date(start)
    end_dt = parse_date(end)
    if start_dt >= end_dt: return []

    generator: Callable[[datetime], datetime] = lambda d: d + timedelta(days=7)
    match interval:
        case "monthly":
            generator = lambda d: datetime(d.year + (d.month // 12), (d.month % 12) + 1, 1, tzinfo=timezone.utc)
            start_dt = datetime(start_dt.year, start_dt.month, 1, tzinfo=timezone.utc)
        case "yearly":
            generator = lambda d: datetime(d.year + 1, 1, 1, tzinfo=timezone.utc)
            start_dt = datetime(start_dt.year, 1, 1, tzinfo=timezone.utc)
        case _: pass

    periods: list[datetime] = []
    current = start_dt

    while current < end_dt:
        periods.append(current)
        current = generator(current)

    periods.append(end_dt)
    return periods

def compute_all_ratios(stats_orig: list[CommitStats], analysis_start: str, adoption_date: datetime|None) -> tuple[dict[str, RatioMetrics], list[RatioMetrics]]:
    start = parse_date(analysis_start)
    end = datetime.now(timezone.utc)
    stats = [s for s in stats_orig if not s.filter_out()]
    logging.info(f"filtered {len(stats_orig) - len(stats)} out of {len(stats_orig)}, remaining: {len(stats)}")
    overall = compute_ratios_by_periods("overall", stats, [start, end], True, True)
    if adoption_date:
        recent = max(adoption_date, end - timedelta(days = 60))
        since_adoption = compute_ratios_by_periods("since_adoption", stats, [adoption_date, end], True, True)
        recent_adoption = compute_ratios_by_periods("recent_adoption", stats, [recent, end], True, True)
        intervals = generate_periods(start, end, "monthly")
        monthly = compute_ratios_by_periods("monthly", stats, intervals)
        all = overall + since_adoption + recent_adoption + monthly
        return {"overall": overall[0], "since_adoption": since_adoption[0], "recent_adoption": recent_adoption[0]}, all
    return {"overall": overall[0]}, overall

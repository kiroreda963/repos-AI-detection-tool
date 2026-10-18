from utils import logging
from match_authors import AuthorMatcher
from utils.data import Author, Commit, PullRequest
from utils.metrics import AuthorType, TypeTool
from heuristics import commit_type, commit_type_tool, human_authors, is_ai_pr_meta, pr_type_tool, is_ai_pr_body

class CommitTagger:
    def __init__(self,similarity_threshold: float = 0.8):
           self.author_matcher:AuthorMatcher = AuthorMatcher(similarity_threshold)

    def find_first_pr_author_commit(self, pr: PullRequest) -> Commit|None:
        """Find the earliest commit authored by the PR author using fuzzy matching."""
        #if pr.pr_author is None:
        #    print("PR AUTHOR is None")
        #print("PR AUTHOR:     ", pr.pr_author)

        for commit in pr.commits:
            #if commit.author is None:
            #    print("COMMIT AUTHOR is None")
            #print("COMMIT AUTHOR", commit.author)
            if self.author_matcher.authors_match(commit.author, pr.pr_author):
                return commit
        return None

    def maybe_filter_commits_after_first_author(self, pr: PullRequest, tags: dict[str, TypeTool]) -> PullRequest:
        """
        Remove commits earlier than the first PR-author commit if no AI commits,
        otherwise remove commits earlier than the first AI-authored commit.
        """
        # Find first AI-authored commit if any
        ai_commit = next((c for c in pr.commits if tags.get(c.sha) == 'AI-PR-authored'), None)

        if ai_commit:
            # Trim before the first AI-authored commit
            idx = next((i for i, c in enumerate(pr.commits) if c.sha == ai_commit.sha), 0)
            pr.commits = pr.commits[idx:]
            return pr

        # Otherwise, fall back to trimming before first PR-author commit
        first_commit = self.find_first_pr_author_commit(pr)
        if not first_commit:
            return pr  # nothing to trim

        idx = next((i for i, c in enumerate(pr.commits) if c.sha == first_commit.sha), 0)
        pr.commits = pr.commits[idx:]
        return pr

    def tag_commits_in_pr(self, pr: PullRequest, secondpass:bool=False, commit_pr_auth: dict[str,bool]|None=None) -> dict[str, TypeTool]:
        """
        Tag commits in a PR based on AI tool usage patterns.

        Returns:
            Dict mapping commit SHA to tag ('AI-PR-authored' or 'AI-PR-followup')
        """
        tags: dict[str, TypeTool] = {}

        # Check for commits directly authored or co-authored by AI tools
        ai_authored_commits = [cm for cm in pr.commits if commit_type(cm) == "AI"]
        # bot_commits = [cm for cm in pr.commits if commit_type(cm) == "Bot"]

        #for cm in ai_authored_commits:
        for cm in ai_authored_commits:
            tags[cm.sha] = commit_type_tool(cm)

        # If we found AI-authored or co-authored commits, tag follow-ups
        if ai_authored_commits:
            ##
            # MAYBE change to a set??
            human_developers: list[Author] = []

            # Extract human authors from AI-authored commits (co-authors)
            for commit in ai_authored_commits:
                humans = human_authors(commit)
                for human in humans:
                    if not self.author_matcher.author_in_list(human, human_developers):
                        human_developers.append(human)

            # Tag follow-up commits by these human developers
            # or not, too noisy?
            # or just the one of the same day?
            # and write email tomorrow
            if False:
                for commit in pr.commits:
                    if commit.sha not in tags:
                        for human in human_authors(commit):
                        # Check if commit author matches any human developer
                            author_match = self.author_matcher.author_in_list(human, human_developers)

                            if author_match:
                                tags[commit.sha] = 'AI-FOLLOWUP', "followup"

                    # tag commits by the PR author as well
                    if commit_pr_auth and commit_pr_auth[commit.sha]:
                        tags[commit.sha] = 'AI-FOLLOWUP', "followup"

        # If no direct AI commits found, check PR metadata
        elif secondpass and (is_ai_pr_meta(pr) or is_ai_pr_body(pr)):
            type, tool = pr_type_tool(pr)
            if pr.commits:
                # Tag first commit as AI-authored
                first_commit = pr.commits[0]
                tags[first_commit.sha] = type, tool
                first_author = first_commit.author

                # Tag subsequent commits by similar authors as follow-up
                for commit in pr.commits[1:]:
                    if commit_pr_auth and commit_pr_auth[commit.sha]:
                        tags[commit.sha] = 'AI-FOLLOWUP', tool
                    elif self.author_matcher.authors_match(commit.author, first_author):
                        tags[commit.sha] = 'AI-FOLLOWUP', tool

        return tags

    def process_pr(self, pr: PullRequest) -> dict[str, TypeTool]:
        tags = self.tag_commits_in_pr(pr)
        pr = self.maybe_filter_commits_after_first_author(pr,tags)

        # build a dict of which commits are by the PR author
        is_pr_author_commit: dict[str,bool] = {
            commit.sha: self.author_matcher.authors_match(commit.author, pr.pr_author)
            for commit in pr.commits
        }

        tags = self.tag_commits_in_pr(pr, secondpass = True, commit_pr_auth = is_pr_author_commit)
        return tags

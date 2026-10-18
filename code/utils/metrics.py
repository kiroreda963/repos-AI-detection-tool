from dataclasses import dataclass
from typing import Literal
from .filters import FileFilter

from .data import LogCommit

thefilter = FileFilter()

@dataclass
class FileStats:
    path: str
    type: str
    added_lines: int
    deleted_lines: int
    old_path: str = "" # in case it's a rename


AuthorType = Literal["Human", "Bot","AI","AI-FOLLOWUP"]
CommitType = Literal["unclassified"] | AuthorType
TypeTool = tuple[AuthorType,str]

def best_classification(t1: TypeTool, t2: TypeTool) -> TypeTool:
    match t1[0]:
        case "AI": return t1
        case "AI-FOLLOWUP":
            match t2[0]:
                case "AI": return t2
                case _: return t1
        case "Bot":
            match t2[0]:
                case "Human": return t1
                case _: return t2
        case "Human": return t2

@dataclass
class CommitStats:
    commit: LogCommit
    file_stats: list[FileStats]
    type: CommitType
    tool: str = ""
    def add_file(self, file: FileStats):
        self.file_stats.append(file)
    def total_added_lines(self): return sum(s.added_lines for s in self.file_stats)
    def total_deleted_lines(self): return sum(s.deleted_lines for s in self.file_stats)

    def update_type(self, tt: TypeTool):
        type_tool: TypeTool = ("Human", "") if self.type == "unclassified" else (self.type, self.tool)
        self.type, self.tool = best_classification(tt, type_tool)

    def simplify(self):
        if self.type == "AI-FOLLOWUP":
            self.type = "AI"
        return self

    def filter_out(self) -> bool:
        remaining = [file for file in self.file_stats if thefilter.should_include(file.path)]
        return len(remaining) == 0

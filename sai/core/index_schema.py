"""SAI index dataclasses — full schema produced by any language tokenizer."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class SymbolEntry:
    name: str
    kind: str            # "var" | "varip" | "function" | "type" | "enum"
    type_: str           # "float" | "int" | "bool" | "string" | "color" | "UDT" | "unknown"
    qualifier: str       # "const" | "input" | "simple" | "series" | "unknown"
    scope: str           # "global" | "local:<function_name>"
    decl_line: int
    usages: list[int] = field(default_factory=list)
    is_persistent: bool = False
    is_intrabar: bool = False
    inferred_meaning: str = ""
    rename_safe: bool = True
    rename_regex: str = ""


@dataclass
class PatternMatch:
    pattern_id: str
    rule_id: str
    severity: str        # "error" | "warning" | "info"
    line: int
    line_end: int = 0
    expr: str = ""
    message: str = ""
    fix: str = ""
    auto_fixable: bool = False


@dataclass
class SectionEntry:
    name: str
    line_start: int
    line_end: int


@dataclass
class BudgetStats:
    global_var_count: int = 0
    global_var_limit: int = 1000
    request_call_count: int = 0
    request_call_limit: int = 40
    tuple_element_count: int = 0
    tuple_element_limit: int = 127
    series_var_count: int = 0
    series_var_limit: int = 902

    def warnings(self) -> list[str]:
        msgs = []
        if self.global_var_count > 900:
            msgs.append(f"Global var count {self.global_var_count} approaching limit {self.global_var_limit}")
        if self.request_call_count > 35:
            msgs.append(f"request.*() calls {self.request_call_count} approaching limit {self.request_call_limit}")
        if self.tuple_element_count > 100:
            msgs.append(f"Tuple elements {self.tuple_element_count} approaching limit {self.tuple_element_limit}")
        if self.series_var_count > 800:
            msgs.append(f"Series vars {self.series_var_count} near crash threshold {self.series_var_limit} (B1)")
        return msgs


@dataclass
class RenameCandidate:
    original: str
    suggested: str
    occurrences: int
    lines: list[int]
    confidence: float
    regex: str
    risk: str
    risk_notes: str = ""


@dataclass
class SAIIndex:
    filepath: str
    language: str
    total_lines: int
    total_chars: int
    indexed_at: str
    symbols: dict[str, SymbolEntry] = field(default_factory=dict)
    sections: list[SectionEntry] = field(default_factory=list)
    patterns: list[PatternMatch] = field(default_factory=list)
    rename_candidates: list[RenameCandidate] = field(default_factory=list)
    budget: BudgetStats = field(default_factory=BudgetStats)
    safe_renames: dict[str, RenameCandidate] = field(default_factory=dict)

    def summary(self) -> str:
        errs = [p for p in self.patterns if p.severity == "error"]
        warns = [p for p in self.patterns if p.severity == "warning"]
        lines = [
            f"SAI Index \u2014 {self.filepath} ({self.language})",
            f"  Lines: {self.total_lines:,}  Chars: {self.total_chars:,}",
            f"  Symbols: {len(self.symbols)}  Sections: {len(self.sections)}",
            f"  Patterns: {len(self.patterns)}  Renames: {len(self.rename_candidates)}",
            f"  Budget \u2014 vars: {self.budget.global_var_count}/1000  "
            f"requests: {self.budget.request_call_count}/40  "
            f"tuples: {self.budget.tuple_element_count}/127",
            f"  Issues \u2014 errors: {len(errs)}  warnings: {len(warns)}",
        ]
        for w in self.budget.warnings():
            lines.append(f"  WARNING: {w}")
        return "\n".join(lines)

    def issues_by_rule(self, rule_id: str) -> list[PatternMatch]:
        return [p for p in self.patterns if p.rule_id == rule_id]

    def issues_by_severity(self, severity: str) -> list[PatternMatch]:
        return [p for p in self.patterns if p.severity == severity]

    def symbol(self, name: str) -> SymbolEntry | None:
        return self.symbols.get(name)

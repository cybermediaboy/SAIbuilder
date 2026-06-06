"""Language-agnostic tokenizer base."""
from __future__ import annotations
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from sai.core.index_schema import SAIIndex, SymbolEntry, PatternMatch, SectionEntry


class TokenizerBase(ABC):
    """Base class for all language tokenizers.

    Subclass and implement:
        SECTION_PATTERNS  -- list of (regex_str, section_name)
        scan_line()       -- called per non-blank, non-comment line
        post_process()    -- called once after all lines scanned
    """

    SECTION_PATTERNS: list[tuple[str, str]] = []

    def __init__(self):
        self.index: SAIIndex | None = None
        self._lines: list[str] = []
        self._scope_stack: list[str] = ["global"]
        self._indent_level: int = 0

    def build(self, src: str, filepath: str = "<stdin>") -> SAIIndex:
        self._lines = src.splitlines()
        self.index = SAIIndex(
            filepath=filepath,
            language=self.language_name(),
            total_lines=len(self._lines),
            total_chars=len(src),
            indexed_at=datetime.now(timezone.utc).isoformat(),
        )
        self._first_pass_sections()
        for lineno, line in enumerate(self._lines, start=1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped == "":
                self._scan_comment_or_blank(lineno, line)
                continue
            self._update_scope(lineno, line)
            self.scan_line(lineno, line, stripped)
        self.post_process()
        self._build_rename_candidates()
        return self.index

    @abstractmethod
    def language_name(self) -> str: ...
    @abstractmethod
    def scan_line(self, lineno: int, raw: str, stripped: str) -> None: ...
    @abstractmethod
    def post_process(self) -> None: ...

    def flag(self, lineno: int, pattern_id: str, rule_id: str, severity: str,
             expr: str, message: str, fix: str = "", auto_fixable: bool = False,
             line_end: int = 0) -> None:
        self.index.patterns.append(PatternMatch(
            pattern_id=pattern_id, rule_id=rule_id, severity=severity,
            line=lineno, line_end=line_end or lineno,
            expr=expr, message=message, fix=fix, auto_fixable=auto_fixable,
        ))

    def add_symbol(self, name: str, kind: str, type_: str, qualifier: str,
                   scope: str, decl_line: int, **kwargs) -> SymbolEntry:
        entry = SymbolEntry(name=name, kind=kind, type_=type_, qualifier=qualifier,
                            scope=scope, decl_line=decl_line, **kwargs)
        self.index.symbols[name] = entry
        if scope == "global":
            self.index.budget.global_var_count += 1
        return entry

    def record_usage(self, name: str, lineno: int) -> None:
        if name in self.index.symbols:
            self.index.symbols[name].usages.append(lineno)

    def current_scope(self) -> str:
        return self._scope_stack[-1] if self._scope_stack else "global"

    def is_global_scope(self) -> bool:
        return len(self._scope_stack) == 1

    def _first_pass_sections(self) -> None:
        compiled = [(re.compile(pat), name) for pat, name in self.SECTION_PATTERNS]
        active: dict[str, int] = {}
        for lineno, line in enumerate(self._lines, start=1):
            for rx, name in compiled:
                if rx.search(line):
                    if name in active:
                        self.index.sections.append(
                            SectionEntry(name=name, line_start=active[name], line_end=lineno - 1))
                    active[name] = lineno
        eof = len(self._lines)
        for name, start in active.items():
            self.index.sections.append(SectionEntry(name=name, line_start=start, line_end=eof))

    def _scan_comment_or_blank(self, lineno: int, line: str) -> None:
        if line.strip().startswith("// REMOVED:"):
            self.flag(lineno, "dead_code_comment", "-", "info",
                      line.strip(), "Orphan // REMOVED: comment \u2014 safe to delete",
                      fix="", auto_fixable=True)

    def _update_scope(self, lineno: int, line: str) -> None:
        self._indent_level = (len(line) - len(line.lstrip())) // 4

    def _build_rename_candidates(self) -> None:
        from sai.core.index_schema import RenameCandidate
        for sym in self.index.symbols.values():
            if sym.inferred_meaning and sym.rename_safe:
                rc = RenameCandidate(
                    original=sym.name, suggested=sym.inferred_meaning,
                    occurrences=len(sym.usages) + 1,
                    lines=[sym.decl_line] + sym.usages,
                    confidence=0.9,
                    regex=rf"\b{re.escape(sym.name)}\b",
                    risk="low",
                )
                self.index.rename_candidates.append(rc)
                self.index.safe_renames[sym.name] = rc

"""pine_guard.py — rule-runner that surfaces SAIIndex patterns as structured lint.

Usage::

    from sai.pine_guard import PineGuard
    guard = PineGuard(index)
    guard.run()          # returns list[LintIssue]
    guard.report()       # prints formatted output
    guard.exit_code()    # 0 = clean, 1 = errors present
"""
from __future__ import annotations
from dataclasses import dataclass
from sai.core.index_schema import SAIIndex, PatternMatch


# ── Issue model ───────────────────────────────────────────────────────────────

@dataclass
class LintIssue:
    filepath: str
    line: int
    line_end: int
    rule_id: str
    pattern_id: str
    severity: str        # "error" | "warning" | "info"
    message: str
    expr: str
    fix: str
    auto_fixable: bool

    def __str__(self) -> str:
        loc = f"{self.filepath}:{self.line}"
        badge = {"error": "ERR ", "warning": "WARN", "info": "INFO"}.get(self.severity, "    ")
        fix_hint = "  [auto-fix available]" if self.auto_fixable else ""
        return f"  {badge}  {loc:45s}  [{self.rule_id:12s}]  {self.message}{fix_hint}"


# ── Severity ordering ──────────────────────────────────────────────────────────

_SEV_ORDER = {"error": 0, "warning": 1, "info": 2}


# ── PineGuard ────────────────────────────────────────────────────────────────────

class PineGuard:
    """Rule-runner and formatter built on top of a pre-built SAIIndex.

    Args:
        index:          SAIIndex from Pine6Tokenizer.build()
        min_severity:   Only surface issues at this level and above.
                        "error" → errors only.
                        "warning" → errors + warnings (default).
                        "info" → everything.
        ignore_rules:   Set of rule_ids to suppress, e.g. {"B6", "-"}.
    """

    def __init__(
        self,
        index: SAIIndex,
        min_severity: str = "warning",
        ignore_rules: set[str] | None = None,
    ):
        self.index = index
        self._min_sev = _SEV_ORDER.get(min_severity, 1)
        self._ignore = ignore_rules or set()
        self._issues: list[LintIssue] | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> list[LintIssue]:
        """Convert index patterns → LintIssues, filtered by severity and ignore list."""
        issues: list[LintIssue] = []
        for p in self.index.patterns:
            if _SEV_ORDER.get(p.severity, 99) > self._min_sev:
                continue
            if p.rule_id in self._ignore or p.pattern_id in self._ignore:
                continue
            issues.append(LintIssue(
                filepath=self.index.filepath,
                line=p.line,
                line_end=p.line_end,
                rule_id=p.rule_id,
                pattern_id=p.pattern_id,
                severity=p.severity,
                message=p.message,
                expr=p.expr,
                fix=p.fix,
                auto_fixable=p.auto_fixable,
            ))
        self._issues = sorted(issues, key=lambda i: (_SEV_ORDER.get(i.severity, 99), i.line))
        return self._issues

    def report(self, verbose: bool = False) -> str:
        """Return a formatted multi-line report string."""
        issues = self._issues if self._issues is not None else self.run()
        if not issues:
            return f"\u2705  {self.index.filepath} — no issues found"

        lines: list[str] = []
        lines.append(f"\n\u2b50  SAI Pine Guard Report")
        lines.append(f"   File    : {self.index.filepath}")
        lines.append(f"   Lines   : {self.index.total_lines:,}")
        lines.append(f"   Symbols : {len(self.index.symbols)}")
        lines.append(f"   Budget  : vars {self.index.budget.global_var_count}/1000  "
                     f"requests {self.index.budget.request_call_count}/40  "
                     f"tuples {self.index.budget.tuple_element_count}/127")
        lines.append("")

        by_sev: dict[str, list[LintIssue]] = {"error": [], "warning": [], "info": []}
        for iss in issues:
            by_sev.setdefault(iss.severity, []).append(iss)

        for sev in ("error", "warning", "info"):
            grp = by_sev[sev]
            if not grp:
                continue
            label = {"error": "🔴 ERRORS", "warning": "🟡 WARNINGS", "info": "🔵 INFO"}.get(sev, sev.upper())
            lines.append(f"  {label} ({len(grp)})")
            lines.append("  " + "─" * 78)
            for iss in grp:
                lines.append(str(iss))
                if verbose and iss.expr:
                    lines.append(f"          expr : {iss.expr[:120]}")
                if verbose and iss.fix:
                    lines.append(f"          fix  : {iss.fix[:120]}")
            lines.append("")

        auto_fixable = [i for i in issues if i.auto_fixable]
        if auto_fixable:
            lines.append(f"  ⚡  {len(auto_fixable)} auto-fixable issue(s) — run apply_change('auto_fix_all') to resolve")

        budget_warns = self.index.budget.warnings()
        if budget_warns:
            lines.append("")
            lines.append("  📈 BUDGET WARNINGS")
            for w in budget_warns:
                lines.append(f"      {w}")

        return "\n".join(lines)

    def print_report(self, verbose: bool = False) -> None:
        """Print the formatted report to stdout."""
        print(self.report(verbose=verbose))

    def exit_code(self) -> int:
        """Return 1 if any errors exist, 0 otherwise (CI-friendly)."""
        issues = self._issues if self._issues is not None else self.run()
        return 1 if any(i.severity == "error" for i in issues) else 0

    def errors(self) -> list[LintIssue]:
        issues = self._issues if self._issues is not None else self.run()
        return [i for i in issues if i.severity == "error"]

    def warnings(self) -> list[LintIssue]:
        issues = self._issues if self._issues is not None else self.run()
        return [i for i in issues if i.severity == "warning"]

    def auto_fixes(self) -> list[LintIssue]:
        """Return all issues where auto_fixable=True."""
        issues = self._issues if self._issues is not None else self.run()
        return [i for i in issues if i.auto_fixable]

    def issues_for_rule(self, rule_id: str) -> list[LintIssue]:
        issues = self._issues if self._issues is not None else self.run()
        return [i for i in issues if i.rule_id == rule_id]

    def as_dict_list(self) -> list[dict]:
        """Serialisable list of dicts — useful for JSON output or webhook payloads."""
        issues = self._issues if self._issues is not None else self.run()
        return [
            {
                "filepath": i.filepath, "line": i.line, "line_end": i.line_end,
                "rule_id": i.rule_id, "pattern_id": i.pattern_id,
                "severity": i.severity, "message": i.message,
                "expr": i.expr, "fix": i.fix, "auto_fixable": i.auto_fixable,
            }
            for i in issues
        ]

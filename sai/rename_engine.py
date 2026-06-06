"""rename_engine.py — safe variable rename pipeline.

Uses SAIIndex.safe_renames and rename_candidates to apply surgical
identifier renames with conflict detection, dry-run mode, and rollback.

Usage::

    from sai.rename_engine import RenameEngine
    engine = RenameEngine(src, index)

    # Preview all safe renames
    plan = engine.plan()
    for r in plan:
        print(r)

    # Apply a single rename
    new_src = engine.apply("sc01", "price_btc")

    # Apply all safe renames at once
    new_src = engine.apply_all()

    # Check for conflicts before applying
    conflicts = engine.conflicts("sc01", "price_btc")
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from sai.core.index_schema import SAIIndex, RenameCandidate


# ── Plan entry ─────────────────────────────────────────────────────────────

@dataclass
class RenamePlanEntry:
    original: str
    suggested: str
    occurrences: int
    lines: list[int]
    confidence: float
    risk: str            # "none" | "low" | "medium" | "high"
    risk_notes: str
    conflicts: list[str]

    def __str__(self) -> str:
        conf = f"{self.confidence:.0%}"
        risk_badge = {"none": "✅", "low": "🟢", "medium": "🟡", "high": "🔴"}.get(self.risk, "?")
        conflict_note = f"  ⚠️  conflicts: {self.conflicts}" if self.conflicts else ""
        return (
            f"  {risk_badge} {self.original!r:20s} → {self.suggested!r:30s}"
            f"  {self.occurrences:3d}x  conf={conf}  risk={self.risk}{conflict_note}"
        )


# ── RenameEngine ──────────────────────────────────────────────────────────────

class RenameEngine:
    """Safe variable rename pipeline.

    All renames are whole-word, regex-based (\\bNAME\\b) to avoid
    partial matches. Conflict detection checks whether the target
    name already exists in the index or appears in the source.

    Args:
        src:    Original source text.
        index:  SAIIndex produced by any language tokenizer.
    """

    def __init__(self, src: str, index: SAIIndex):
        self._src = src
        self._index = index
        self._history: list[tuple[str, str, str]] = []  # (original, suggested, src_before)

    # ── Planning ─────────────────────────────────────────────────────────────

    def plan(self, max_risk: str = "low") -> list[RenamePlanEntry]:
        """Return a sorted rename plan for all candidates up to *max_risk*.

        Order: confidence desc, then occurrences desc.
        """
        risk_levels = {"none": 0, "low": 1, "medium": 2, "high": 3}
        max_level = risk_levels.get(max_risk, 1)

        entries: list[RenamePlanEntry] = []
        for rc in self._index.rename_candidates:
            if risk_levels.get(rc.risk, 99) > max_level:
                continue
            conflicts = self.conflicts(rc.original, rc.suggested)
            entries.append(RenamePlanEntry(
                original=rc.original,
                suggested=rc.suggested,
                occurrences=rc.occurrences,
                lines=rc.lines,
                confidence=rc.confidence,
                risk=rc.risk,
                risk_notes=rc.risk_notes,
                conflicts=conflicts,
            ))
        return sorted(entries, key=lambda e: (-e.confidence, -e.occurrences))

    def conflicts(self, original: str, suggested: str) -> list[str]:
        """Return list of conflict descriptions, empty if rename is safe."""
        issues: list[str] = []

        # Target name already declared in index
        if suggested in self._index.symbols and suggested != original:
            issues.append(f"'{suggested}' already declared at line {self._index.symbols[suggested].decl_line}")

        # Target name already appears in source (could be a built-in or external)
        if re.search(rf"\b{re.escape(suggested)}\b", self._src):
            if suggested not in (self._index.symbols.get(original, object).__class__.__name__,):
                # Only flag if it's not just the symbol itself renamed
                existing_lines = [
                    i + 1 for i, l in enumerate(self._src.splitlines())
                    if re.search(rf"\b{re.escape(suggested)}\b", l)
                ]
                if existing_lines:
                    issues.append(
                        f"'{suggested}' already appears in source at lines: "
                        f"{existing_lines[:5]}{'...' if len(existing_lines) > 5 else ''}"
                    )

        # Source name doesn't appear in source (already renamed or typo)
        if not re.search(rf"\b{re.escape(original)}\b", self._src):
            issues.append(f"'{original}' not found in current source — may have been renamed already")

        return issues

    # ── Applying ─────────────────────────────────────────────────────────────

    def apply(self, original: str, suggested: str, force: bool = False) -> str:
        """Apply a single rename. Raises ValueError on conflicts unless force=True.

        Returns updated source. Call .src property to get current state.
        """
        if not force:
            conflicts = self.conflicts(original, suggested)
            # Filter out the "already appears" conflict when the appearance
            # is ONLY the original symbol itself (safe to ignore)
            real_conflicts = [
                c for c in conflicts
                if "already declared" in c or "not found in current source" in c
            ]
            if real_conflicts:
                raise ValueError(
                    f"Rename '{original}' → '{suggested}' has conflicts:\n"
                    + "\n".join(f"  - {c}" for c in real_conflicts)
                )

        self._history.append((original, suggested, self._src))
        self._src = re.sub(rf"\b{re.escape(original)}\b", suggested, self._src)
        return self._src

    def apply_all(
        self,
        max_risk: str = "low",
        skip_conflicts: bool = True,
    ) -> str:
        """Apply all rename candidates up to *max_risk* in one pass.

        If skip_conflicts=True (default), candidates with conflicts are skipped
        and logged. If False, raises on first conflict.

        Returns final source text.
        """
        skipped: list[str] = []
        plan = self.plan(max_risk=max_risk)

        # Sort by length descending to avoid partial-rename cascades
        # (e.g. rename 'sc01' before 'sc0' if both exist)
        plan_sorted = sorted(plan, key=lambda e: -len(e.original))

        for entry in plan_sorted:
            if entry.conflicts:
                if skip_conflicts:
                    skipped.append(
                        f"  SKIPPED {entry.original!r} → {entry.suggested!r}: "
                        + "; ".join(entry.conflicts)
                    )
                    continue
                else:
                    raise ValueError(
                        f"Conflict on '{entry.original}' → '{entry.suggested}': "
                        + "; ".join(entry.conflicts)
                    )
            try:
                self.apply(entry.original, entry.suggested, force=False)
            except ValueError as exc:
                if skip_conflicts:
                    skipped.append(f"  SKIPPED {entry.original!r}: {exc}")
                else:
                    raise

        if skipped:
            import sys
            print("RenameEngine.apply_all — skipped:", file=sys.stderr)
            for s in skipped:
                print(s, file=sys.stderr)

        return self._src

    # ── Rollback ──────────────────────────────────────────────────────────────

    def rollback(self, steps: int = 1) -> str:
        """Undo the last *steps* renames. Returns source after rollback."""
        for _ in range(min(steps, len(self._history))):
            _, _, src_before = self._history.pop()
            self._src = src_before
        return self._src

    def rollback_all(self) -> str:
        """Revert to original source before any renames."""
        if self._history:
            self._src = self._history[0][2]
            self._history.clear()
        return self._src

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def src(self) -> str:
        """Current source text (after any applied renames)."""
        return self._src

    @property
    def history(self) -> list[tuple[str, str]]:
        """List of (original, suggested) pairs applied so far."""
        return [(orig, sug) for orig, sug, _ in self._history]

    def diff_summary(self) -> str:
        """Human-readable summary of all renames applied so far."""
        if not self._history:
            return "No renames applied."
        lines = [f"Renames applied ({len(self._history)}):"]
        for orig, sug, _ in self._history:
            count = len(re.findall(rf"\b{re.escape(sug)}\b", self._src))
            lines.append(f"  {orig!r:20s} → {sug!r}  ({count} occurrences in result)")
        return "\n".join(lines)

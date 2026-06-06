"""Post-edit verifier — consistency checks after apply_change()."""
from __future__ import annotations
import re
from sai.core.index_schema import SAIIndex


def verify(src: str, index: SAIIndex) -> list[str]:
    """Run all post-edit checks. Returns list of issue strings; empty = clean."""
    issues: list[str] = []
    issues.extend(_check_renamed_vars(src, index))
    issues.extend(_check_no_orphan_removed_comments(src))
    issues.extend(_check_duplicate_ta_calls(src))
    issues.extend(_check_udt_field_history(src))
    issues.extend(_check_bool_na(src))
    issues.extend(_check_forward_references(src, index))
    issues.extend(_check_duplicate_param_names(src))
    issues.extend(_check_request_call_count(src))
    return issues


def _check_renamed_vars(src: str, index: SAIIndex) -> list[str]:
    return [
        f"RENAME_INCOMPLETE: {orig!r} still appears "
        f"{len(re.findall(rf'\\b{re.escape(orig)}\\b', src))}x after rename to {rc.suggested!r}"
        for orig, rc in index.safe_renames.items()
        if re.search(rf"\b{re.escape(orig)}\b", src)
    ]


def _check_no_orphan_removed_comments(src: str) -> list[str]:
    return [
        f"LINE {i}: Orphan // REMOVED: comment"
        for i, line in enumerate(src.splitlines(), 1)
        if line.strip().startswith("// REMOVED:")
    ]


def _check_duplicate_ta_calls(src: str, min_count: int = 2) -> list[str]:
    from collections import Counter
    counts = Counter(m.group(1) for m in re.finditer(r"(ta\.\w+\([^)]*\))", src))
    return [
        f"DUPLICATE_TA: {e!r} called {c}x — consider caching"
        for e, c in counts.items() if c >= min_count
    ]


def _check_udt_field_history(src: str) -> list[str]:
    return [
        f"LINE {i}: UDT field history ref — use (myUDT[n]).field instead of myUDT.field[n]"
        for i, line in enumerate(src.splitlines(), 1)
        if re.search(r"\w+\.\w+\[\d+\]", line)
    ]


def _check_bool_na(src: str) -> list[str]:
    return [
        f"LINE {i}: bool initialised to na — illegal in Pine v6 (CE10097)"
        for i, line in enumerate(src.splitlines(), 1)
        if re.match(r"bool\s+\w+\s*=\s*na\b", line.strip())
    ]


def _check_forward_references(src: str, index: SAIIndex) -> list[str]:
    issues = []
    for sym in index.symbols.values():
        for ul in sym.usages:
            if ul < sym.decl_line:
                issues.append(
                    f"FORWARD_REF (CE10272): {sym.name!r} used line {ul}, declared line {sym.decl_line}"
                )
    return issues


def _check_duplicate_param_names(src: str) -> list[str]:
    issues = []
    for i, line in enumerate(src.splitlines(), 1):
        for m in re.finditer(r"\w+\s*\([^)]{20,}\)", line):
            seen: set[str] = set()
            for p in re.findall(r"(\w+)\s*=", m.group(0)):
                if p in seen:
                    issues.append(f"LINE {i}: Duplicate named argument {p!r} (CE10072)")
                seen.add(p)
    return issues


def _check_request_call_count(src: str, warn_at: int = 35) -> list[str]:
    count = len(re.findall(r"request\.\w+\s*\(", src))
    return [f"W001: {count} request.*() calls — approaching limit of 40"] if count > warn_at else []

"""Edit engine — apply named changes to source using pre-indexed patterns."""
from __future__ import annotations
import re
from sai.core.index_schema import SAIIndex


def apply_change(src: str, index: SAIIndex, change_id: str, **kwargs) -> str:
    """Apply a named refactor change to *src* using the pre-built *index*.

    All transforms are idempotent.

    Available change_ids:
        rename_cryptic_vars       -- apply all safe_renames from the index
        cache_duplicate_ta_calls  -- insert cached vars for repeated ta.*() calls
        add_nz_guards             -- wrap unguarded request.security() results
        extract_ring_buffers      -- replace inline push/shift with f_push_ring()
        unify_barstate            -- replace raw barstate.* with normalized flags
        remove_dead_comments      -- delete // REMOVED: orphan comment lines
        fix_literal_history_refs  -- rewrite myUDT.field[n] to (myUDT[n]).field
        auto_fix_all              -- run every auto_fixable pattern from the index
    """
    dispatch = {
        "rename_cryptic_vars":       _rename_cryptic_vars,
        "cache_duplicate_ta_calls":  _cache_duplicate_ta_calls,
        "add_nz_guards":             _add_nz_guards,
        "extract_ring_buffers":      _extract_ring_buffers,
        "unify_barstate":            _unify_barstate,
        "remove_dead_comments":      _remove_dead_comments,
        "fix_literal_history_refs":  _fix_literal_history_refs,
        "auto_fix_all":              _auto_fix_all,
    }
    fn = dispatch.get(change_id)
    if fn is None:
        raise ValueError(f"Unknown change_id: {change_id!r}. Available: {list(dispatch)}")
    return fn(src, index, **kwargs)


def _rename_cryptic_vars(src: str, index: SAIIndex, **_) -> str:
    for orig, rc in index.safe_renames.items():
        src = re.sub(rc.regex, rc.suggested, src)
    return src


def _cache_duplicate_ta_calls(src: str, index: SAIIndex, min_count: int = 2, **_) -> str:
    from collections import Counter
    pattern = re.compile(r"(ta\.\w+\([^)]*\))")
    counts = Counter(m.group(1) for m in pattern.finditer(src))
    for expr, cnt in sorted(
        ((e, c) for e, c in counts.items() if c >= min_count), key=lambda x: -x[1]
    ):
        var_name = "_cache_" + re.sub(r"[^a-zA-Z0-9]", "", expr.replace("ta.", ""))
        if var_name in src:
            continue
        first = next(m for m in pattern.finditer(src) if m.group(1) == expr)
        insert_pos = src.rfind("\n", 0, first.start()) + 1
        cache_line = f"float {var_name} = {expr}  // cached: {cnt}x\n"
        src = src[:insert_pos] + cache_line + src[insert_pos:]
        src = src.replace(expr, var_name, cnt - 1)
    return src


def _add_nz_guards(src: str, index: SAIIndex, **_) -> str:
    for p in index.patterns:
        if p.pattern_id == "nullable_arithmetic" and p.auto_fixable and p.fix:
            if p.expr in src and p.fix not in src:
                src = src.replace(p.expr, p.fix, 1)
    return src


def _extract_ring_buffers(src: str, index: SAIIndex, **_) -> str:
    ring = re.compile(
        r"array\.push\((\w+),\s*([^)]+)\)\s*\n\s*"
        r"(?:if\s+array\.size\(\1\)\s*>\s*\w+\s*\n\s*array\.shift\(\1\)|"
        r"array\.shift\(\1\)\s*//.*?oversized)",
        re.MULTILINE,
    )
    return ring.sub(lambda m: f"f_push_ring({m.group(1)}, {m.group(2)}, max_bars)", src)


def _unify_barstate(src: str, index: SAIIndex, **_) -> str:
    for p in index.patterns:
        if p.pattern_id == "raw_barstate" and p.auto_fixable and p.fix and p.expr in src:
            src = src.replace(p.expr, p.fix, 1)
    return src


def _remove_dead_comments(src: str, index: SAIIndex, **_) -> str:
    return "".join(
        l for l in src.splitlines(keepends=True)
        if not l.strip().startswith("// REMOVED:")
    )


def _fix_literal_history_refs(src: str, index: SAIIndex, **_) -> str:
    return re.compile(r"\b(\w+)\.(\w+)\[(\d+)\]").sub(
        lambda m: f"({m.group(1)}[{m.group(3)}]).{m.group(2)}", src
    )


def _auto_fix_all(src: str, index: SAIIndex, **_) -> str:
    for p in sorted(index.patterns, key=lambda x: -x.line):
        if p.auto_fixable and p.fix and p.expr and p.expr in src:
            src = src.replace(p.expr, p.fix, 1)
    return src

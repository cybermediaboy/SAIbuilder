"""run_guard.py — called by the perp-orch pg_validate MCP tool.

Returns a JSON report to stdout. The MCP tool captures stdout and
returns it directly to Perplexity as the tool response, or writes
it to reports/<stem>.json if the connection is inactive.

Usage (called by MCP tool, not directly):
    python -m sai.run_guard <filepath> [min_severity] [--write-report]

Exit codes:
    0  — clean (no errors)
    1  — errors found
    2  — file not found or parse failure
"""
from __future__ import annotations
import json
import sys
from dataclasses import asdict
from pathlib import Path


def run_guard(
    filepath: str,
    min_severity: str = "warning",
    write_report: bool = False,
    reports_dir: str = "reports",
) -> dict:
    """Index *filepath* and run PineGuard. Returns structured result dict."""
    import sai
    from sai.pine_guard import PineGuard

    path = Path(filepath)
    if not path.exists():
        return {
            "ok": False,
            "error": f"File not found: {filepath}",
            "filepath": filepath,
        }

    try:
        src, index = sai.bootstrap(str(path), lang="pine6")
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Bootstrap failed: {exc}",
            "filepath": filepath,
        }

    guard = PineGuard(index, min_severity=min_severity)
    issues = guard.run()

    result = {
        "ok": True,
        "filepath": str(path),
        "language": index.language,
        "indexed_at": index.indexed_at,
        "total_lines": index.total_lines,
        "total_chars": index.total_chars,
        "summary": index.summary(),
        "exit_code": guard.exit_code(),
        "counts": {
            "error":   len(guard.errors()),
            "warning": len(guard.warnings()),
            "auto_fixable": len(guard.auto_fixes()),
            "total":   len(issues),
        },
        "budget": {
            "vars":     index.budget.global_var_count,
            "vars_limit": index.budget.global_var_limit,
            "requests": index.budget.request_call_count,
            "requests_limit": index.budget.request_call_limit,
            "tuples":   index.budget.tuple_element_count,
            "tuples_limit": index.budget.tuple_element_limit,
            "series":   index.budget.series_var_count,
            "warnings": index.budget.warnings(),
        },
        "symbols": {
            "total": len(index.symbols),
            "functions": sum(1 for s in index.symbols.values() if s.kind == "function"),
            "types":     sum(1 for s in index.symbols.values() if s.kind == "type"),
            "enums":     sum(1 for s in index.symbols.values() if s.kind == "enum"),
        },
        "rename_candidates": [
            {
                "original": rc.original,
                "suggested": rc.suggested,
                "occurrences": rc.occurrences,
                "confidence": rc.confidence,
                "risk": rc.risk,
                "lines": rc.lines[:10],
            }
            for rc in index.rename_candidates
        ],
        "issues": guard.as_dict_list(),
        "report": guard.report(verbose=True),
    }

    if write_report:
        rdir = Path(reports_dir)
        rdir.mkdir(exist_ok=True)
        rfile = rdir / (path.stem + ".guard.json")
        rfile.write_text(json.dumps(result, indent=2), encoding="utf-8")
        result["report_written"] = str(rfile)

    return result


def _cli() -> None:
    import argparse
    p = argparse.ArgumentParser(description="SAI PineGuard runner")
    p.add_argument("filepath")
    p.add_argument("--min-severity", default="warning", choices=["error", "warning", "info"])
    p.add_argument("--write-report", action="store_true")
    p.add_argument("--reports-dir", default="reports")
    p.add_argument("--pretty", action="store_true", help="Indent JSON output")
    args = p.parse_args()

    result = run_guard(
        filepath=args.filepath,
        min_severity=args.min_severity,
        write_report=args.write_report,
        reports_dir=args.reports_dir,
    )
    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent))
    sys.exit(result.get("exit_code", 2))


if __name__ == "__main__":
    _cli()

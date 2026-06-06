#!/usr/bin/env python3
"""SAIbuilder CLI.

Usage:
    python builder.py <file> [--lang pine6] [--output path.json] [--summary]
"""
from __future__ import annotations
import argparse, json, sys
from dataclasses import asdict
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description="Build a SAI index from a source file.")
    p.add_argument("filepath")
    p.add_argument("--lang", default="pine6", choices=["pine6"])
    p.add_argument("--output", default=None)
    p.add_argument("--summary", action="store_true")
    args = p.parse_args()

    filepath = Path(args.filepath)
    if not filepath.exists():
        print(f"Error: not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    sys.path.insert(0, str(Path(__file__).parent))
    import sai
    src, index = sai.bootstrap(str(filepath), lang=args.lang)

    if args.summary:
        print(index.summary())
        print()

    for severity, label in (("error", "ERRORS"), ("warning", "WARNINGS"), ("info", "INFO")):
        items = index.issues_by_severity(severity)
        if items:
            print(f"{label} ({len(items)}):")
            for q in items:
                print(f"  Line {q.line:4d} [{q.rule_id}] {q.message}")
            print()

    out = args.output or str(filepath) + ".sai.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(asdict(index), f, indent=2)
    print(f"Index written: {out}")
    sys.exit(1 if index.issues_by_severity("error") else 0)


if __name__ == "__main__":
    main()

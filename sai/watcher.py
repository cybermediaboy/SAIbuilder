"""watcher.py — filesystem watcher that auto-runs bootstrap() on save.

Watches one or more .pine files (or directories). On each save:
    1. Runs Pine6Tokenizer.build() to produce a fresh SAIIndex
    2. Runs PineGuard to surface lint issues
    3. Fires a webhook alert if errors/warnings changed since last run
    4. Optionally writes a .sai.json sidecar next to the source file

Dependencies: watchdog  (pip install watchdog)

Usage::

    # CLI
    python -m sai.watcher MyScript.pine --webhook https://hooks.example.com/pine
    python -m sai.watcher ./scripts/ --ext .pine --min-severity warning

    # Programmatic
    from sai.watcher import SAIWatcher
    w = SAIWatcher(
        paths=["MyScript.pine"],
        webhook_url="https://hooks.example.com/pine",
        write_sidecar=True,
    )
    w.start()   # non-blocking
    w.join()    # block until Ctrl-C
"""
from __future__ import annotations
import json
import sys
import time
import threading
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Callable

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent
except ImportError:
    Observer = None  # type: ignore
    FileSystemEventHandler = object  # type: ignore

import sai
from sai.pine_guard import PineGuard
from sai.core.index_schema import SAIIndex


# ── Debounce helper ────────────────────────────────────────────────────────────

class _Debouncer:
    """Delays calling *fn* until no new calls arrive within *delay* seconds."""
    def __init__(self, fn: Callable, delay: float = 0.4):
        self._fn = fn
        self._delay = delay
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def call(self, *args, **kwargs) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._fn, args, kwargs)
            self._timer.daemon = True
            self._timer.start()


# ── Watchdog event handler ─────────────────────────────────────────────────────

class _PineEventHandler(FileSystemEventHandler):
    def __init__(self, watcher: "SAIWatcher", extensions: set[str]):
        super().__init__()
        self._watcher = watcher
        self._extensions = extensions
        self._debouncers: dict[str, _Debouncer] = {}

    def on_modified(self, event) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix not in self._extensions:
            return
        if path not in self._debouncers:
            self._debouncers[str(path)] = _Debouncer(self._watcher._on_file_changed, delay=0.4)
        self._debouncers[str(path)].call(path)


# ── SAIWatcher ──────────────────────────────────────────────────────────────────

class SAIWatcher:
    """Watches Pine source files and fires webhook alerts on change.

    Args:
        paths:          List of files or directories to watch.
        webhook_url:    URL to POST alerts to (pine-guard format). Optional.
        extensions:     File extensions to watch. Default: {".pine"}.
        min_severity:   Minimum severity for PineGuard. Default: "warning".
        write_sidecar:  Write .sai.json next to each source file. Default: False.
        on_index:       Optional callback(filepath, index) called after each index.
        ignore_rules:   Rule IDs to suppress in PineGuard.
        verbose:        Print report to stdout on each change.
    """

    def __init__(
        self,
        paths: list[str | Path],
        webhook_url: str | None = None,
        extensions: set[str] | None = None,
        min_severity: str = "warning",
        write_sidecar: bool = False,
        on_index: Callable[[str, SAIIndex], None] | None = None,
        ignore_rules: set[str] | None = None,
        verbose: bool = True,
    ):
        if Observer is None:
            raise ImportError("watchdog is required: pip install watchdog")
        self._paths = [Path(p) for p in paths]
        self._webhook_url = webhook_url
        self._extensions = extensions or {".pine"}
        self._min_severity = min_severity
        self._write_sidecar = write_sidecar
        self._on_index = on_index
        self._ignore_rules = ignore_rules or set()
        self._verbose = verbose
        self._observer = Observer()
        self._prev_issues: dict[str, set[str]] = {}  # filepath → set of issue fingerprints
        self._lock = threading.Lock()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Schedule watchers and start observer thread (non-blocking)."""
        handler = _PineEventHandler(self, self._extensions)
        watched: set[str] = set()
        for path in self._paths:
            watch_dir = str(path if path.is_dir() else path.parent)
            if watch_dir not in watched:
                self._observer.schedule(handler, watch_dir, recursive=True)
                watched.add(watch_dir)
        self._observer.start()
        print(f"[SAIWatcher] Watching {len(self._paths)} path(s). Press Ctrl-C to stop.", flush=True)
        # Run initial index on explicit files
        for path in self._paths:
            if path.is_file() and path.suffix in self._extensions:
                self._on_file_changed(path)

    def stop(self) -> None:
        """Stop the observer thread."""
        self._observer.stop()
        self._observer.join()

    def join(self) -> None:
        """Block until Ctrl-C, then stop cleanly."""
        try:
            while self._observer.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            print("\n[SAIWatcher] Stopped.", flush=True)

    # ── Core handler ────────────────────────────────────────────────────────────

    def _on_file_changed(self, path: Path) -> None:
        filepath = str(path)
        try:
            src, index = sai.bootstrap(filepath, lang="pine6")
        except Exception as exc:
            print(f"[SAIWatcher] ERROR indexing {filepath}: {exc}", file=sys.stderr, flush=True)
            return

        guard = PineGuard(index, min_severity=self._min_severity, ignore_rules=self._ignore_rules)
        issues = guard.run()

        if self._verbose:
            print(guard.report(verbose=False), flush=True)

        if self._write_sidecar:
            sidecar = path.with_suffix(path.suffix + ".sai.json")
            sidecar.write_text(json.dumps(asdict(index), indent=2), encoding="utf-8")

        if self._on_index:
            try:
                self._on_index(filepath, index)
            except Exception as exc:
                print(f"[SAIWatcher] on_index callback error: {exc}", file=sys.stderr, flush=True)

        self._maybe_fire_webhook(filepath, index, guard)

    # ── Webhook ────────────────────────────────────────────────────────────────

    def _maybe_fire_webhook(self, filepath: str, index: SAIIndex, guard: PineGuard) -> None:
        if not self._webhook_url:
            return

        issues = guard.run()
        fingerprints = {f"{i.rule_id}:{i.line}:{i.pattern_id}" for i in issues}
        prev = self._prev_issues.get(filepath, set())

        new_issues = fingerprints - prev
        resolved = prev - fingerprints

        with self._lock:
            self._prev_issues[filepath] = fingerprints

        if not new_issues and not resolved:
            return  # no change — don’t spam

        errors = guard.errors()
        warnings_list = guard.warnings()
        severity = "critical" if errors else "warning" if warnings_list else "info"

        payload = {
            "alert_type": "sai_lint",
            "symbol": Path(filepath).stem,
            "severity": severity,
            "message": (
                f"{Path(filepath).name}: "
                f"{len(errors)} error(s), {len(warnings_list)} warning(s)"
                + (f" | +{len(new_issues)} new" if new_issues else "")
                + (f" | -{len(resolved)} resolved" if resolved else "")
            ),
            "details": guard.as_dict_list(),
            "budget": {
                "vars": index.budget.global_var_count,
                "requests": index.budget.request_call_count,
                "tuples": index.budget.tuple_element_count,
            },
            "filepath": filepath,
            "indexed_at": index.indexed_at,
        }

        threading.Thread(
            target=self._post_webhook,
            args=(payload,),
            daemon=True,
        ).start()

    def _post_webhook(self, payload: dict) -> None:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
            print(f"[SAIWatcher] Webhook fired → HTTP {status}", flush=True)
        except Exception as exc:
            print(f"[SAIWatcher] Webhook error: {exc}", file=sys.stderr, flush=True)


# ── CLI entry point ────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse
    p = argparse.ArgumentParser(description="SAI filesystem watcher for Pine Script v6")
    p.add_argument("paths", nargs="+", help="Files or directories to watch")
    p.add_argument("--webhook", default=None, help="Webhook URL to POST alerts")
    p.add_argument("--ext", default=".pine", help="File extension to watch (default: .pine)")
    p.add_argument("--min-severity", default="warning", choices=["error", "warning", "info"])
    p.add_argument("--sidecar", action="store_true", help="Write .sai.json sidecar files")
    p.add_argument("--quiet", action="store_true", help="Suppress per-save report output")
    args = p.parse_args()

    watcher = SAIWatcher(
        paths=args.paths,
        webhook_url=args.webhook,
        extensions={args.ext},
        min_severity=args.min_severity,
        write_sidecar=args.sidecar,
        verbose=not args.quiet,
    )
    watcher.start()
    watcher.join()


if __name__ == "__main__":
    _cli()

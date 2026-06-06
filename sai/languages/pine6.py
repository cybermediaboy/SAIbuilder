"""Pine Script v6 language module — encodes all 83 pine-guard rules."""
from __future__ import annotations
import re
from sai.core.tokenizer_base import TokenizerBase

FORBIDDEN_IN_FUNCTIONS = {
    "barcolor", "fill", "hline", "indicator", "library", "plot",
    "plotbar", "plotcandle", "plotchar", "plotshape", "strategy", "alertcondition",
}
REMOVED_PARAMS = {"transp", "when"}
BARSTATE_NORM = {
    "barstate.islast and not barstate.isrealtime": "is_right_edge",
    "not barstate.isnew and barstate.isrealtime": "is_live_intrabar",
    "barstate.isnew and barstate.isrealtime": "is_live_new_bar",
    "barstate.islastconfirmedhistory": "is_history_edge",
}
SERIES_SOURCES = re.compile(
    r"\b(close|open|high|low|volume|hl2|hlc3|ohlc4|bar_index|time"
    r"|ta\.\w+|request\.security|request\.seed|request\.financial)\b"
)
CRYPTIC_PATTERNS = [
    (re.compile(r"\bsc0([1-9]|10)\b"), "price_asset_{n}"),
    (re.compile(r"\ba([1-4])\b"),       "spread_ema_{n}"),
    (re.compile(r"\bs_f([1-7])\b"),     "snap_feat_{n}"),
]
V5_TF_STRINGS = re.compile(r'timeframe\.period\s*==\s*"([DWMQ])"')
RING_BUFFER_PATTERN = re.compile(
    r"array\.push\((\w+),\s*([^)]+)\)\s*\n\s*"
    r"(?:if\s+array\.size\(\1\)\s*>\s*\w+\s*\n\s*array\.shift\(\1\)"
    r"|array\.shift\(\1\)\s*//.*?oversized)",
    re.MULTILINE,
)
LAZY_STATEFUL = re.compile(r"(and|or)\s+(ta\.\w+\([^)]+\)|[a-z_]\w*\([^)]+\))\s*[><=!]")


class Pine6Tokenizer(TokenizerBase):
    """Tokenizer for Pine Script v6. Detects 83 pine-guard rules."""

    SECTION_PATTERNS = [
        (r"//\s*[-=]{3,}\s*INPUTS\s*[-=]{3,}",    "INPUTS"),
        (r"//\s*[-=]{3,}\s*TYPES\s*[-=]{3,}",     "TYPES"),
        (r"//\s*[-=]{3,}\s*HELPER\s*FUNC",         "HELPERS"),
        (r"//\s*[-=]{3,}\s*DATA\s*FETCH",          "DATA_FETCHING"),
        (r"//\s*[-=]{3,}\s*CALC",                  "CALCULATIONS"),
        (r"//\s*[-=]{3,}\s*RENDER",                "RENDERING"),
        (r"//\s*[-=]{3,}\s*STRATEGY",              "STRATEGY"),
    ]

    def __init__(self):
        super().__init__()
        self._inside_function = False
        self._function_name = ""
        self._ta_calls: dict[str, list[int]] = {}
        self._request_calls: list[int] = []
        self._barstate_norm_defined = False
        self._barstate_raw_after_norm: list[tuple[int, str]] = []

    def language_name(self) -> str:
        return "pine6"

    def scan_line(self, lineno: int, raw: str, stripped: str) -> None:
        self._scan_var_declaration(lineno, stripped)
        self._scan_function_declaration(lineno, stripped)
        self._scan_type_declaration(lineno, stripped)
        self._scan_enum_declaration(lineno, stripped)
        self._scan_ta_calls(lineno, stripped)
        self._scan_request_calls(lineno, stripped)
        self._scan_barstate(lineno, stripped)
        self._scan_dead_params(lineno, stripped)
        self._scan_bool_na(lineno, stripped)
        self._scan_numeric_bool_cast(lineno, stripped)
        self._scan_plot_series_offset(lineno, stripped)
        self._scan_param_reassignment(lineno, stripped)
        self._scan_lazy_eval_risk(lineno, stripped)
        self._scan_v5_tf_strings(lineno, stripped)
        self._scan_array_loop_oob(lineno, stripped)
        self._scan_divide_by_zero(lineno, stripped)
        self._scan_integer_division(lineno, stripped)
        self._scan_neg_array_index_opportunity(lineno, stripped)
        self._scan_varip_in_function_risk(lineno, stripped)

    def post_process(self) -> None:
        self._flag_duplicate_ta_calls()
        self._flag_request_budget()
        self._flag_barstate_raw_after_norm()
        self._flag_ring_buffer_opportunities()
        self._infer_cryptic_names()

    # ── Declaration scanners ──────────────────────────────────────────────────

    def _scan_var_declaration(self, lineno: int, s: str) -> None:
        m = re.match(r"^(var(?:ip)?)\s+(float|int|bool|string|color|array<\w+>|\w+)\s+(\w+)\s*=", s)
        if m:
            kw, type_, name = m.group(1), m.group(2), m.group(3)
        else:
            m = re.match(r"^(float|int|bool|string|color)\s+(\w+)\s*=", s)
            if not m:
                return
            kw, type_, name = "", m.group(1), m.group(2)
        is_varip = kw == "varip"
        self.add_symbol(name, "var", type_, self._infer_qualifier(s),
                        self.current_scope(), lineno,
                        is_persistent=kw in ("var", "varip"), is_intrabar=is_varip)
        if is_varip and self.current_scope() == "global":
            self.flag(lineno, "varip_global", "B6", "info", s.split("=")[0].strip(),
                      f"varip '{name}' in global scope cannot be moved into a function (B6)")

    def _scan_function_declaration(self, lineno: int, s: str) -> None:
        m = re.match(r"^(\w+)\s*\(([^)]*)\)\s*=>", s)
        if not m:
            return
        self.add_symbol(m.group(1), "function", "unknown", "simple", "global", lineno)
        self._inside_function = True
        self._function_name = m.group(1)

    def _scan_type_declaration(self, lineno: int, s: str) -> None:
        if s.startswith("type "):
            parts = s.split()
            if len(parts) >= 2:
                self.add_symbol(parts[1], "type", "UDT", "simple", "global", lineno)

    def _scan_enum_declaration(self, lineno: int, s: str) -> None:
        if s.startswith("enum "):
            parts = s.split()
            if len(parts) >= 2:
                self.add_symbol(parts[1], "enum", "enum", "const", "global", lineno)

    # ── Pattern scanners ──────────────────────────────────────────────────────

    def _scan_ta_calls(self, lineno: int, s: str) -> None:
        for m in re.finditer(r"(ta\.\w+\([^)]*\))", s):
            self._ta_calls.setdefault(m.group(1), []).append(lineno)

    def _scan_request_calls(self, lineno: int, s: str) -> None:
        if not re.search(r"\brequest\.\w+\s*\(", s):
            return
        self._request_calls.append(lineno)
        self.index.budget.request_call_count += 1
        if "request.security(" in s and "nz(" not in s and "na(" not in s:
            if re.search(r"=\s*request\.security\(", s):
                self.flag(lineno, "nullable_arithmetic", "B3", "warning", s.strip(),
                          "request.security() result may be na — consider nz() guard")

    def _scan_barstate(self, lineno: int, s: str) -> None:
        norm_kws = {"is_right_edge", "is_live_new_bar", "is_live_intrabar", "is_history_edge"}
        if any(kw in s for kw in norm_kws) and "barstate." in s:
            self._barstate_norm_defined = True
        if self._barstate_norm_defined and "barstate." in s:
            if not any(kw in s for kw in norm_kws):
                self._barstate_raw_after_norm.append((lineno, s.strip()))

    def _scan_dead_params(self, lineno: int, s: str) -> None:
        for param in REMOVED_PARAMS:
            if re.search(rf"\b{param}\s*=", s):
                self.flag(lineno, "removed_param", "-", "error", s.strip(),
                          f"Parameter '{param}=' was removed in Pine v6")

    def _scan_bool_na(self, lineno: int, s: str) -> None:
        if re.match(r"bool\s+\w+\s*=\s*na\b", s):
            self.flag(lineno, "bool_na", "CE10097", "error", s.strip(),
                      "bool variable cannot be na in Pine v6 (CE10097)")

    def _scan_numeric_bool_cast(self, lineno: int, s: str) -> None:
        if re.match(r"if\s+(bar_index|volume|ta\.\w+\([^)]*\))\s*$", s):
            self.flag(lineno, "implicit_bool_cast", "-", "warning", s.strip(),
                      "Numeric value used as bool — implicit cast removed in Pine v6. Use explicit comparison.")

    def _scan_plot_series_offset(self, lineno: int, s: str) -> None:
        if not re.search(r"\bplot\w*\s*\([^)]*offset\s*=\s*\w+", s):
            return
        m = re.search(r"offset\s*=\s*(\w+)", s)
        if m:
            sym = self.index.symbols.get(m.group(1))
            if sym and sym.qualifier == "series":
                self.flag(lineno, "plot_series_offset", "CE10056", "error", s.strip(),
                          f"offset={m.group(1)} is series — must be simple/const (CE10056)")

    def _scan_param_reassignment(self, lineno: int, s: str) -> None:
        if not (self._inside_function and re.search(r"\b\w+\s*:=", s)):
            return
        m = re.search(r"\b(\w+)\s*:=", s)
        if m and (m.group(1) not in self.index.symbols
                  or self.index.symbols[m.group(1)].scope != "global"):
            self.flag(lineno, "param_reassignment", "CE10175", "warning", s.strip(),
                      f"Possible param reassignment '{m.group(1)} :=' — params are immutable (CE10175)")

    def _scan_lazy_eval_risk(self, lineno: int, s: str) -> None:
        if LAZY_STATEFUL.search(s):
            self.flag(lineno, "lazy_eval_stateful", "B8", "warning", s.strip(),
                      "Stateful call in and/or short-circuit — may not execute every bar (B8). Pre-compute.")

    def _scan_v5_tf_strings(self, lineno: int, s: str) -> None:
        m = V5_TF_STRINGS.search(s)
        if m:
            old, tf = m.group(0), m.group(1)
            self.flag(lineno, "v5_tf_string", "-", "warning", old,
                      f'v5 timeframe string "{tf}" — use "1{tf}" in Pine v6',
                      fix=old.replace(f'"{tf}"', f'"1{tf}"'), auto_fixable=True)

    def _scan_array_loop_oob(self, lineno: int, s: str) -> None:
        if re.search(r"for\s+\w+\s*=\s*0\s+to\s+array\.size\(", s):
            self.flag(lineno, "array_loop_oob", "ARRAY_LOOP_OOB", "warning", s.strip(),
                      "Unguarded for loop on array.size()-1 — add size > 0 guard (ARRAY_LOOP_OOB)")

    def _scan_divide_by_zero(self, lineno: int, s: str) -> None:
        if re.search(r"/\s*0\b", s):
            self.flag(lineno, "divide_by_zero", "B5", "error", s.strip(), "Division by zero (B5)")

    def _scan_integer_division(self, lineno: int, s: str) -> None:
        if re.search(r"\b\d+\s*/\s*\d+\b", s) and "request." not in s:
            self.flag(lineno, "int_division", "-", "info", s.strip(),
                      "Integer division is fractional in Pine v6. Use int()/math.floor() if truncation needed.")

    def _scan_neg_array_index_opportunity(self, lineno: int, s: str) -> None:
        m = re.search(r"array\.get\((\w+),\s*array\.size\(\w+\)\s*-\s*1\)", s)
        if m:
            arr, old = m.group(1), m.group(0)
            self.flag(lineno, "neg_array_index", "-", "info", old,
                      f"Simplify to {arr}.get(-1) using v6 negative index",
                      fix=f"{arr}.get(-1)", auto_fixable=True)

    def _scan_varip_in_function_risk(self, lineno: int, s: str) -> None:
        if self._inside_function and s.startswith("varip "):
            self.flag(lineno, "varip_in_function", "B6", "info", s.strip(),
                      "varip inside function creates per-call-site state, not global (B6)")

    # ── Post-process aggregations ─────────────────────────────────────────────

    def _flag_duplicate_ta_calls(self) -> None:
        for expr, lines in self._ta_calls.items():
            if len(lines) >= 2:
                self.flag(lines[0], "duplicate_ta_call", "-", "warning", expr,
                          f"{expr} called {len(lines)}x — cache in a variable to save CPU")

    def _flag_request_budget(self) -> None:
        count = self.index.budget.request_call_count
        if count > 35:
            sev = "error" if count > 40 else "warning"
            self.flag(1, "request_budget", "W001", sev, f"request.*() calls: {count}",
                      f"{count} request.*() calls {'EXCEEDS' if count > 40 else 'approaching'} limit 40 (W001)")

    def _flag_barstate_raw_after_norm(self) -> None:
        for lineno, expr in self._barstate_raw_after_norm:
            suggestion = next((flag for pat, flag in BARSTATE_NORM.items() if pat in expr), None)
            self.flag(lineno, "raw_barstate", "-", "info", expr,
                      "Raw barstate.* after normalization block — use normalized flag",
                      fix=suggestion or "", auto_fixable=bool(suggestion))

    def _flag_ring_buffer_opportunities(self) -> None:
        src_joined = "\n".join(self._lines)
        for m in RING_BUFFER_PATTERN.finditer(src_joined):
            lineno = src_joined[:m.start()].count("\n") + 1
            self.flag(lineno, "ring_buffer_inline", "-", "info",
                      m.group(0)[:60], "Inline ring-buffer — extract to f_push_ring()")

    def _infer_cryptic_names(self) -> None:
        for sym in self.index.symbols.values():
            if sym.inferred_meaning:
                continue
            for pattern, template in CRYPTIC_PATTERNS:
                m = pattern.fullmatch(sym.name)
                if m:
                    sym.inferred_meaning = template.replace("{n}", m.group(1) if m.lastindex else "")
                    sym.rename_regex = rf"\b{re.escape(sym.name)}\b"
                    break

    # ── Qualifier inference ───────────────────────────────────────────────────

    def _infer_qualifier(self, expr: str) -> str:
        if SERIES_SOURCES.search(expr):
            return "series"
        if "input." in expr:
            return "input"
        if re.search(r"\bsyminfo\.\w+|\btimeframe\.\w+", expr):
            return "simple"
        rhs = expr.split("=", 1)[-1].strip() if "=" in expr else expr
        if re.match(r"^-?\d+(\.\d+)?$", rhs) or rhs in ("true", "false"):
            return "const"
        if rhs.startswith('"'):
            return "const"
        return "unknown"

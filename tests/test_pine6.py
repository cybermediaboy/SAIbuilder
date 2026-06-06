"""Unit tests for the Pine6 language module."""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest
import sai
from sai.core.edit_engine import apply_change
from sai.core.verifier import verify


SIMPLE = """
//@version=6
indicator("Test", overlay=true)
bool flag = na
float sc01 = request.security("BINANCE:BTCUSDT", "D", close)
float sc02 = request.security("BINANCE:ETHUSDT", "D", close)
float a1 = ta.ema(sc01 - sc02, 10)
if bar_index
    label.new(bar_index, close, "test")
float result = ta.atr(14) / math.max(ta.atr(14), syminfo.mintick)
"""

VARIP = """
//@version=6
indicator("Varip Test")
varip float globalState = 0.0
f_test(float x) =>
    varip float localState = 0.0
    localState += x
    localState
"""


def _idx(src: str):
    with tempfile.NamedTemporaryFile(suffix=".pine", mode="w", delete=False) as f:
        f.write(src)
        path = f.name
    try:
        _, idx = sai.bootstrap(path, lang="pine6")
    finally:
        os.unlink(path)
    return idx


class TestBoolNa:
    def test_flagged(self):
        assert any(p.rule_id == "CE10097" for p in _idx(SIMPLE).patterns)

class TestDuplicateTaCall:
    def test_atr_flagged(self):
        dupes = [p for p in _idx(SIMPLE).patterns if p.pattern_id == "duplicate_ta_call"]
        assert any("ta.atr(14)" in p.expr for p in dupes)

class TestImplicitBoolCast:
    def test_bar_index_flagged(self):
        assert any(p.pattern_id == "implicit_bool_cast" for p in _idx(SIMPLE).patterns)

class TestVarip:
    def test_global_flagged(self):
        assert any(p.pattern_id == "varip_global" for p in _idx(VARIP).patterns)
    def test_in_function_flagged(self):
        assert any(p.pattern_id == "varip_in_function" for p in _idx(VARIP).patterns)

class TestRemoveDeadComments:
    def test_removes(self):
        src = "float x = 1.0\n// REMOVED: old\nfloat y = 2.0\n"
        with tempfile.NamedTemporaryFile(suffix=".pine", mode="w", delete=False) as f:
            f.write(src); path = f.name
        try:
            src_l, idx = sai.bootstrap(path, lang="pine6")
            result = apply_change(src_l, idx, "remove_dead_comments")
        finally:
            os.unlink(path)
        assert "// REMOVED:" not in result
        assert "float x" in result and "float y" in result

class TestVerifier:
    def test_bool_na(self):
        src = "bool flag = na\n"
        with tempfile.NamedTemporaryFile(suffix=".pine", mode="w", delete=False) as f:
            f.write(src); path = f.name
        try:
            src_l, idx = sai.bootstrap(path, lang="pine6")
            issues = verify(src_l, idx)
        finally:
            os.unlink(path)
        assert any("bool" in i for i in issues)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

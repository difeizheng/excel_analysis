"""loader._colkey() 单测。"""
import datetime as dt
import numpy as np
import pandas as pd
from loader import _colkey


def test_colkey_datetime():
    assert _colkey(dt.datetime(2026, 1, 1)) == "2026-01"
    assert _colkey(pd.Timestamp("2025-12-01")) == "2025-12"


def test_colkey_string():
    assert _colkey("2018年") == "2018年"
    assert _colkey("  2019年  ") == "2019年"   # strip
    assert _colkey("合计完成") == "合计完成"   # 非年/月字符串保留


def test_colkey_empty():
    assert _colkey("") is None
    assert _colkey("nan") is None
    assert _colkey("NaN") is None
    assert _colkey(None) is None
    assert _colkey(np.nan) is None


def test_colkey_various():
    # 不是 datetime 但有 strftime 的对象
    class FakeDate:
        def strftime(self, fmt):
            return "2024-06"
    assert _colkey(FakeDate()) == "2024-06"
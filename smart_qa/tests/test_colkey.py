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


def test_colkey_yyyymm_numeric():
    """6 位 YYYYMM 裸数字(Excel 常存成数字)→ 'YYYY-MM'。覆盖用户场景:
    电费明细表头 202601/202602/202603 被 pandas 读成 float '202601.0',原被 V3 误判。"""
    # 文本/整型/float 三种入参形态都归一化到同一 key
    assert _colkey("202601") == "2026-01"
    assert _colkey(202601) == "2026-01"
    assert _colkey(202601.0) == "2026-01"        # float 残留 .0
    assert _colkey("202612") == "2026-12"
    assert _colkey("  202603.0  ") == "2026-03"  # strip + .0


def test_colkey_yyyymm_invalid_not_normalized():
    """年/月不合法的 6 位数字不归一化(原样保留),避免误吞邮编/ID/坏月份。"""
    assert _colkey("202613") == "202613"   # 月 13 非法
    assert _colkey("202600") == "202600"   # 月 00 非法
    assert _colkey("189912") == "189912"   # 年 < 1900 越界(下界不含)
    assert _colkey("210101") == "210101"   # 年 > 2100 越界(上界不含)
    assert _colkey("100000") == "100000"   # 邮编样数字(年 100)不动
    assert _colkey("202601.5") == "202601.5"  # 非 .0 float 残留,不像干净 YYYYMM


def test_colkey_yyyymm_boundary_years():
    """合法年窗口 [1900, 2100] 闭区间两端的合法月份仍归一化。"""
    assert _colkey("190001") == "1900-01"  # 下界
    assert _colkey("210012") == "2100-12"  # 上界


def test_colkey_year_only_unchanged():
    """4 位裸年(无 '年' / '-MM')不在本次归一化范围,保持原样(不扩大 scope)。"""
    assert _colkey("2026") == "2026"
    assert _colkey("2026年") == "2026年"
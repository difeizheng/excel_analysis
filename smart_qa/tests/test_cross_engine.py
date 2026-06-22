"""双引擎互验:memory + sqlite 端到端结果一致(backend='both')。

这是 SQLite 接入查询链的终极保证 —— 每个数值类用例,两条独立代码路径
取数 + engine 运算,产出一致才 verified。不一致即暴露 bug。
"""
from __future__ import annotations
import os
import sys
import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

import loader  # noqa: E402
import qa  # noqa: E402
import golden_cases as GC  # noqa: E402


@pytest.fixture(scope="module")
def grid():
    return loader.load_grid()


# 排除 neg 用例(只验拒绝,不验数值互验);其余都应 both 通过
NUMERIC_CASES = [c for c in GC.CASES if c["category"] != "neg"]


@pytest.mark.parametrize("case", NUMERIC_CASES, ids=lambda c: f"id{c['id']}")
def test_both_engines_agree(grid, case):
    """每个数值用例在 both 模式下双引擎一致。"""
    ans = qa.ask(grid, case["q"], backend="both")
    assert "C5 双引擎互验一致" in ans["verify_msg"], \
        f"#{case['id']} 互验失败: {ans['verify_msg']}"
    # 非失败类用例须通过全部校验(含 C1-C4 + C5)
    if ans["kind"] != "fail":
        assert ans["verified"], f"#{case['id']} 校验未过: {ans['verify_msg']}"


def test_both_smoke_wind(grid):
    """冒烟:风电发电量 both 模式 —— 已知正确值 39.9 亿千瓦时。"""
    ans = qa.ask(grid, "公司2025年风电发电量是多少", backend="both")
    assert ans["kind"] == "single"
    assert ans["verified"]
    assert "C5 双引擎互验一致" in ans["verify_msg"]
    assert abs(ans["result"].value - 39.905) < 0.05

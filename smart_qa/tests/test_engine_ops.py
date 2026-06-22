"""Phase 8.3 · 4 新 op(yoy/share/peak_year/rank)的确定性测试。

覆盖:
- engine 层:Result 形状 + 值(纯函数,鸭子类型 CellView)。
- live qa.ask:4 op 经规则路径产出正确值 + verified(含 C1-C4)。
- 双引擎 C5:4 op 在 backend='both' 下 memory==sqlite 一致。
- parser 关键词路由:同比/占比/最高/排名 → 对应 op;6 golden 问句 op 不被偷。

守项目灵魂:expected 永远从 Grid 算(QG oracle),LLM 不碰数字(use_llm=False)。
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

import engine as E  # noqa: E402
import qa  # noqa: E402
import parser as P  # noqa: E402
import question_generator as QG  # noqa: E402
from backend import CellView  # noqa: E402


def _cv(addr: str, val, numeric: bool = True) -> CellView:
    return CellView(addr=addr, value=val, numeric=numeric)


# ============================================================ engine 纯函数
class TestEngineOps:
    def test_yoy_shape_and_value(self):
        prev, curr = _cv("财务数据!M5", 9.5), _cv("财务数据!M6", 10.0)
        res = E.yoy(10.0, 9.5, curr, prev, "利润总额·2025年", "利润总额·2024年")
        assert res.operation == "yoy"
        assert res.unit == "百分比"
        assert abs(res.value - (10.0 - 9.5) / 9.5) < 1e-9
        assert len(res.operands) == 2
        assert res.operands[0].addr == "财务数据!M5"     # 上期在前
        assert res.operands[1].addr == "财务数据!M6"     # 本期在后
        assert "同比" in res.formula

    def test_share_shape_and_value(self):
        part, total = _cv("发电量!L10", 307.8), _cv("发电量!L38", 323.4)
        res = E.share(307.8, 323.4, part, total, "巴西发电量", "发电量合计")
        assert res.operation == "share"
        assert res.unit == "百分比"
        assert abs(res.value - 307.8 / 323.4) < 1e-9
        assert res.operands[0].addr == "发电量!L10"      # 部分在前
        assert res.operands[1].addr == "发电量!L38"      # 总体在后
        assert "占比" in res.formula

    def test_peak_year_shape_and_value(self):
        best = _cv("财务数据!M6", 10.0)
        res = E.peak_year(2025, best, "利润总额·2025年")
        assert res.operation == "peak_year"
        assert res.value == 2025                          # 年份(int),非数值量
        assert res.unit == ""
        assert len(res.operands) == 1
        assert res.operands[0].addr == "财务数据!M6"
        assert "2025" in res.formula and "峰值年" in res.formula

    def test_yoy_divzero_raises(self):
        """prev=0 时上层 pipeline 应已拦;engine 本身做除法会抛 ZeroDivisionError(不做静默)。"""
        prev, curr = _cv("a!1", 0.0), _cv("a!2", 5.0)
        with pytest.raises(ZeroDivisionError):
            E.yoy(5.0, 0.0, curr, prev, "x", "y")


# ============================================================ live qa.ask(rule 路径)
def test_live_yoy_golden(grid_legacy):
    """利润总额 2025 同比 = (10.0-9.5)/9.5 ≈ 5.26%。"""
    ans = qa.ask(grid_legacy, "公司2025年的利润总额同比增长了百分之几？")
    assert ans["kind"] == "single"
    assert abs(ans["result"].value - (10.0 - 9.5) / 9.5) < 0.002
    assert ans["verified"], ans["verify_msg"]


def test_live_peak_year_golden(grid_legacy):
    """利润总额全年份峰值在 2025 年(=10.0)。"""
    ans = qa.ask(grid_legacy, "公司哪一年的利润总额最高？")
    assert ans["kind"] == "single"
    assert ans["result"].value == 2025
    assert ans["verified"], ans["verify_msg"]


def test_live_share_aligns_oracle(grid_legacy):
    t = QG.enumerate_targets(grid_legacy, max_n=8, types={"share"})[0]
    ans = qa.ask(grid_legacy, QG.fallback_question(t))
    assert ans["kind"] == "single"
    exp = QG.re_derive(t, grid_legacy)
    assert abs(ans["result"].value - exp["value"]) < 0.002
    assert ans["verified"], ans["verify_msg"]


def test_live_rank_aligns_oracle(grid_legacy):
    t = QG.enumerate_targets(grid_legacy, max_n=8, types={"rank"})[0]
    ans = qa.ask(grid_legacy, QG.fallback_question(t))
    assert ans["kind"] == "multi"
    exp = QG.re_derive(t, grid_legacy)
    actual = [it["value"] for it in ans["items"]]
    assert len(actual) == len(exp["value"])
    assert all(abs(a - b) < 0.02 for a, b in zip(actual, exp["value"]))
    assert ans["verified"], ans["verify_msg"]


# ============================================================ 双引擎 C5
@pytest.mark.parametrize("op", ["yoy", "share", "peak_year", "rank"])
def test_new_ops_dual_engine(grid_legacy, op):
    """4 新 op 在 backend='both' 下 memory↔sqlite 一致(C5 对新 op 成立)。"""
    t = QG.enumerate_targets(grid_legacy, max_n=8, types={op})[0]
    ans = qa.ask(grid_legacy, QG.fallback_question(t), backend="both")
    assert "C5 双引擎互验一致" in ans["verify_msg"], (op, ans["verify_msg"])
    assert ans["verified"], (op, ans["verify_msg"])


# ============================================================ parser 关键词路由
@pytest.mark.parametrize("q,op", [
    ("公司2024年利润总额同比增加了多少", "yoy"),
    ("公司2024年利润总额环比变化", "yoy"),
    ("2025年巴西发电量占总发电量的比重", "share"),
    ("2025年巴西发电量的份额是多少", "share"),
    ("公司哪一年的利润总额最高", "peak_year"),
    ("哪年发电量最多", "peak_year"),
    ("2025年各区域发电量从高到低", "rank"),
    ("2025年各区域发电量排名", "rank"),
])
def test_parser_routes_new_ops(q, op):
    assert P.parse(q).operation == op


@pytest.mark.parametrize("q,op", [
    ("公司2018年的利润总额是多少？", "lookup"),
    ("三峡国际2022、2024、2025年每年的汇兑净损失是多少？", "multi"),
    ("24年-26年2月累计向集团分红多少？", "sum"),
    ("公司近三年的利润增长率是多少？", "cagr"),
    ("三峡国际2025年的总装机、可控装机、利润总额、发电量是多少？", "multi"),
    ("公司2025年风电发电量是多少", "sum"),
])
def test_parser_golden_ops_unchanged(q, op):
    """新关键词不得偷走 6 golden 问句的 op(run.py 6/6 的语义保证)。"""
    assert P.parse(q).operation == op

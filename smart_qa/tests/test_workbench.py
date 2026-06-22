"""工作台支撑模块单元测试:question_generator / trace_runner。

不依赖真实 LLM —— enumerate/verify/trace 均走确定性链路(use_llm=False)。
任务持久化层由 tests/test_task_store.py 覆盖。

question_generator 的核心契约:expected(标准答案)永远直接从 Grid 算出,
qa.ask 只作被测对象。故最强测试 = oracle 值与 qa.ask 输出对齐(同批单元格)。
"""
from __future__ import annotations
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))

import question_generator as QG  # noqa: E402
import trace_runner as TR  # noqa: E402


# ---------------- question_generator · 基础(向后兼容) ----------------
def test_enumerate_targets(grid_legacy):
    targets = QG.enumerate_targets(grid_legacy, max_n=20)
    assert len(targets) > 0
    metrics = {t.metric for t in targets}
    assert "利润总额" in metrics  # 已知单点 locator 指标


def test_verify_question_lookup(grid_legacy):
    """利润总额 2018 = 6.5(已知 golden),LLM 不参与,确定性匹配。"""
    ans = QG.verify_question(
        "公司2018年的利润总额是多少？", grid_legacy,
        {"kind": "single", "value": 6.5},
    )
    assert ans["kind"] == "single"
    assert ans["match"] is True
    assert ans["addr"]


def test_fallback_question_no_llm():
    t = QG.QuestionTarget(
        entity="三峡国际", metric="利润总额",
        time_tokens=[("year", 2018)], operation="lookup",
    )
    q = QG.fallback_question(t)
    assert "2018" in q and "利润总额" in q


# ---------------- oracle 真值对照 run.py 黄金用例 ----------------
def test_oracle_matches_runpy_golden(grid_legacy):
    """oracle 直接从 Grid 算出的值,须与 run.py 6 黄金用例一致。"""
    # 用例1: 利润总额 2018 = 6.50
    e = QG._o_lookup(grid_legacy, "利润总额", 2018, "三峡国际")
    assert e is not None and abs(e["value"] - 6.50) < 0.01
    # 用例6: 风电发电量 2025 = 39.905(陆上+海上分类汇总)
    e = QG._o_taxonomy(grid_legacy, "风电发电量", 2025, "三峡国际")
    assert e is not None and abs(e["value"] - 39.905) < 0.02
    # 用例4: 利润总额 近三年 CAGR ≈ 0.0557
    e = QG._o_cagr(grid_legacy, "利润总额", 3, "三峡国际")
    assert e is not None and abs(e["value"] - 0.0557) < 0.002


# ---------------- oracle ↔ 引擎对齐(6 可验证类,模板句应 ✓) ----------------
def _first(targets, op):
    ts = [t for t in targets if t.operation == op]
    assert ts, f"无 {op} 候选"
    return ts[0]


def test_verifiable_lookup_aligns_engine(grid_legacy):
    t = _first(QG.enumerate_targets(grid_legacy, max_n=8, types={"lookup"}), "lookup")
    vr = QG.verify_question(QG.fallback_question(t), grid_legacy, t.expected)
    assert vr["match"] is True, (t, vr)


def test_verifiable_cumulative_aligns_engine(grid_legacy):
    t = _first(QG.enumerate_targets(grid_legacy, max_n=8, types={"cumulative"}), "cumulative")
    vr = QG.verify_question(QG.fallback_question(t), grid_legacy, t.expected)
    assert vr["match"] is True, (t, vr)


def test_verifiable_taxonomy_aligns_engine(grid_legacy):
    t = _first(QG.enumerate_targets(grid_legacy, max_n=8, types={"taxonomy"}), "taxonomy")
    vr = QG.verify_question(QG.fallback_question(t), grid_legacy, t.expected)
    assert vr["match"] is True, (t, vr)


def test_verifiable_cagr_aligns_engine(grid_legacy):
    t = _first(QG.enumerate_targets(grid_legacy, max_n=8, types={"cagr"}), "cagr")
    vr = QG.verify_question(QG.fallback_question(t), grid_legacy, t.expected)
    assert vr["match"] is True, (t, vr)


def test_verifiable_multi_year_aligns_engine(grid_legacy):
    t = _first(QG.enumerate_targets(grid_legacy, max_n=8, types={"multi_year"}), "multi_year")
    vr = QG.verify_question(QG.fallback_question(t), grid_legacy, t.expected)
    assert vr["match"] is True, (t, vr)


def test_verifiable_multi_metric_aligns_engine(grid_legacy):
    t = _first(QG.enumerate_targets(grid_legacy, max_n=8, types={"multi_metric"}), "multi_metric")
    vr = QG.verify_question(QG.fallback_question(t), grid_legacy, t.expected)
    assert vr["match"] is True, (t, vr)


# ---------------- 原 4 盲区类(Phase 8.3 起引擎已补齐,模板句应 ✓) ----------------
def test_peak_year_aligns_engine(grid_legacy):
    t = _first(QG.enumerate_targets(grid_legacy, max_n=8, types={"peak_year"}), "peak_year")
    assert t.expected["value"] == int(t.expected["value"])    # oracle 给的是"年份"
    vr = QG.verify_question(QG.fallback_question(t), grid_legacy, t.expected)
    assert vr["match"] is True                                # 引擎 argmax 已补齐


def test_share_aligns_engine(grid_legacy):
    t = _first(QG.enumerate_targets(grid_legacy, max_n=8, types={"share"}), "share")
    assert 0 <= t.expected["value"] <= 1                      # 占比比率
    vr = QG.verify_question(QG.fallback_question(t), grid_legacy, t.expected)
    assert vr["match"] is True                                # 引擎占比 op 已补齐


def test_yoy_aligns_engine(grid_legacy):
    t = _first(QG.enumerate_targets(grid_legacy, max_n=8, types={"yoy"}), "yoy")
    vr = QG.verify_question(QG.fallback_question(t), grid_legacy, t.expected)
    assert vr["match"] is True                                # 同比 op 已补齐(不再误路由 cagr)


def test_rank_aligns_engine(grid_legacy):
    ts = QG.enumerate_targets(grid_legacy, max_n=8, types={"rank"})
    assert ts, "无 rank 候选"
    t = ts[0]
    assert t.expected["kind"] == "multi"
    vr = QG.verify_question(QG.fallback_question(t), grid_legacy, t.expected)
    assert vr["match"] is True                                # 排名 op 已补齐


# ---------------- 多样性采样 ----------------
def test_diversity_spans_types_and_metrics(grid_legacy):
    ts = QG.enumerate_targets(grid_legacy, max_n=12)
    ops = {t.operation for t in ts}
    metrics = {t.metric for t in ts}
    assert len(ops) >= 3, ops
    assert len(metrics) >= 3, metrics
    counts = Counter(t.metric for t in ts)
    assert max(counts.values()) <= 3, counts                  # 单指标不霸屏


def test_default_all_verifiable(grid_legacy):
    """Phase 8.3:原 4 盲区 op 已补齐进引擎,所有题型均为 verifiable。"""
    ts = QG.enumerate_targets(grid_legacy, max_n=15)
    cats = {t.category for t in ts}
    assert cats == {"verifiable"}


def test_blindspot_pool_now_empty(grid_legacy):
    """盲区已清空(BLINDSPOT=());按 blindspot 过滤应返回空列表。"""
    ts = QG.enumerate_targets(grid_legacy, max_n=15, categories={"blindspot"})
    assert ts == []


# ---------------- 造句 & 鲁棒性 ----------------
class _NoLLM:
    available = False

    def chat(self, *a, **k):
        raise RuntimeError("不应被调用")


def test_build_question_unavailable_returns_none():
    t = QG.QuestionTarget(entity="三峡国际", metric="利润总额",
                          time_tokens=[("year", 2018)], operation="lookup")
    assert QG.build_question(t, _NoLLM()) is None


def test_fallback_per_type_has_keywords(grid_legacy):
    """模板句须含引擎识别所需关键词(如 累计/增长率),或靠指标名触发(is_taxonomy_node)。"""
    pairs = [
        ("cumulative", "累计"),
        ("cagr", "增长率"),
        ("taxonomy", None),       # 靠指标名风电发电量触发 is_taxonomy_node,无固定词
    ]
    pools = QG._build_pools(grid_legacy)
    for op, kw in pairs:
        assert pools[op], f"无 {op} 候选"
        q = QG.fallback_question(pools[op][0])
        if kw:
            assert kw in q, (op, q)


def test_verify_catches_engine_exception(grid_legacy):
    """引擎对未配置窗口(如"近七年"cagr)会抛 KeyError;verify_question 必须捕获、转 fail,
    不让异常逃逸(yoy 已不再触发此路径——Phase 8.3 用近七年 cagr 保留异常兜底覆盖)。"""
    t = QG.QuestionTarget(
        entity="三峡国际", metric="利润总额",
        time_tokens=[("recent", 7)], operation="cagr",
        expected={"kind": "single", "value": 0.0, "op": "cagr"},
    )
    vr = QG.verify_question(QG.fallback_question(t), grid_legacy, t.expected)
    assert vr["engine_kind"] == "fail"
    assert vr["refused"] is True
    assert vr["match"] is False


def test_peak_year_handles_empty_time(grid_legacy):
    """peak_year 无年份也正常:扫描全年份取 argmax,返回 single(value=年份),且与 oracle 一致。"""
    exp = QG._o_peak_year(grid_legacy, "利润总额", "三峡国际")
    assert exp and exp["value"] == int(exp["value"])           # oracle 给的是"年份"
    t = QG.QuestionTarget(
        entity="三峡国际", metric="利润总额",
        time_tokens=[], operation="peak_year", expected=exp,
    )
    vr = QG.verify_question(QG.fallback_question(t), grid_legacy, exp)
    assert vr["engine_kind"] == "single"
    assert vr["match"] is True


# ---------------- trace_runner ----------------
def test_trace_question_structure(grid_legacy):
    tr = TR.trace_question(grid_legacy, "公司2018年的利润总额是多少？")
    for key in ("layer1_grid", "layer2_semantic", "layer3_intent", "layer4_plan",
                "layer5_exec", "layer6_answer", "layer7_verify"):
        assert key in tr
    assert tr["layer2_semantic"]["metric"] == "利润总额"
    assert tr["intent_source"] == "规则"
    assert tr["layer7_verify"]["verified"] is True

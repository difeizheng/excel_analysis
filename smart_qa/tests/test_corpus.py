"""语料库闭环的纯函数测试:re_derive / corpus_store 往返 / run_items 对齐 / diff_runs 分类。

不依赖真实 LLM——全走确定性链路。grid 由 conftest 的 grid_legacy fixture 提供。
核心契约:expected 永远现场重算(不落盘),oracle↔引擎同批单元格 → 可验证类模板句应✓。
"""
from __future__ import annotations
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "src")))

import question_generator as QG  # noqa: E402
import corpus_run as CR  # noqa: E402
from corpus_store import CorpusEntry  # noqa: E402


# ---------------- re_derive:与 enumerate 同源 oracle,值须一致 ----------------
def test_re_derive_matches_pools(grid_legacy):
    """_build_pools 每个候选的 expected,用 re_derive 重算必须完全相等。"""
    pools = QG._build_pools(grid_legacy)
    for op, cands in pools.items():
        for t in cands:
            assert QG.re_derive(t, grid_legacy) == t.expected, (op, t)


def test_re_derive_golden(grid_legacy):
    """re_derive 单点对照 run.py 黄金:利润总额2018=6.5、风电2025=39.905、近三年cagr≈0.0557。"""
    e = QG.re_derive(
        QG.QuestionTarget(entity="三峡国际", metric="利润总额",
                          time_tokens=[("year", 2018)], operation="lookup"),
        grid_legacy)
    assert e is not None and abs(e["value"] - 6.50) < 0.01

    e = QG.re_derive(
        QG.QuestionTarget(entity="三峡国际", metric="风电发电量",
                          time_tokens=[("year", 2025)], operation="taxonomy"),
        grid_legacy)
    assert e is not None and abs(e["value"] - 39.905) < 0.02

    e = QG.re_derive(
        QG.QuestionTarget(entity="三峡国际", metric="利润总额",
                          time_tokens=[("recent", 3)], operation="cagr",
                          extras={"recent_n": 3}),
        grid_legacy)
    assert e is not None and abs(e["value"] - 0.0557) < 0.002


def test_re_derive_orphan_returns_none(grid_legacy):
    """metric 不存在/单元格缺失 → re_derive 返回 None(孤儿),不抛。"""
    t = QG.QuestionTarget(entity="三峡国际", metric="压根不存在的指标XYZ",
                          time_tokens=[("year", 2018)], operation="lookup")
    assert QG.re_derive(t, grid_legacy) is None


# ---------------- corpus_store 往返 + 去重 + 不落盘数字 ----------------
def test_corpus_upsert_merge_and_dedup(tmp_path, monkeypatch):
    import task_store as TS
    import corpus_store as CS
    monkeypatch.setattr(TS, "TASKS_DIR", str(tmp_path))
    tid = "t_corpus_merge"
    t = QG.QuestionTarget(entity="三峡国际", metric="利润总额",
                          time_tokens=[("year", 2018)], operation="lookup")
    td = CR.target_to_dict(t)

    assert CS.upsert_phrasings(tid, [(td, "公司2018年的利润总额是多少？", "模板")]) == 1
    loaded = CS.load_corpus(tid)
    assert len(loaded) == 1 and loaded[0].op == "lookup"

    # 同意图不同问法 → 合并 phrasing,不建新 entry
    assert CS.upsert_phrasings(tid, [(td, "2018 咱们利润多少", "LLM")]) == 1
    loaded = CS.load_corpus(tid)
    assert len(loaded) == 1 and len(loaded[0].phrasings) == 2

    # 同 text 再来 → 去重,0 新增
    assert CS.upsert_phrasings(tid, [(td, "2018 咱们利润多少", "LLM")]) == 0
    assert len(CS.load_corpus(tid)[0].phrasings) == 2

    # 永不落盘 expected/actual 数字:yaml 里无 expected 键、无真值 6.5
    raw = open(CS.task_corpus_path(tid), encoding="utf-8").read()
    assert "expected" not in raw and "6.5" not in raw and "actual" not in raw


def test_corpus_load_missing_is_empty(tmp_path, monkeypatch):
    import task_store as TS
    import corpus_store as CS
    monkeypatch.setattr(TS, "TASKS_DIR", str(tmp_path))
    assert CS.load_corpus("never_exists") == []


def test_entry_to_target_roundtrip(grid_legacy):
    t = QG.QuestionTarget(entity="三峡国际", metric="利润总额",
                          time_tokens=[("year", 2018)], operation="lookup")
    e = CorpusEntry(id="x", op="lookup", category="verifiable",
                    target=CR.target_to_dict(t), phrasings=[])
    t2 = CR.entry_to_target(e)
    assert QG.re_derive(t2, grid_legacy) == QG.re_derive(t, grid_legacy)


# ---------------- run_items:模板模式 oracle↔引擎对齐(全 10 op) ----------------
def test_run_items_template_alignment(grid_legacy):
    """Phase 8.3:10 op 全部 verifiable 且引擎能答;模板句 run_items 应全部 ✓。"""
    pools = QG._build_pools(grid_legacy)
    entries = []
    for op in ("lookup", "cumulative", "taxonomy", "cagr", "multi_year",
               "multi_metric", "peak_year", "share", "yoy", "rank"):
        for t in pools[op][:1]:
            entries.append(CorpusEntry(
                id=op, op=op, category=t.category,
                target=CR.target_to_dict(t),
                phrasings=[{"text": QG.fallback_question(t), "src": "模板"}]))
    rows = CR.run_items(entries, grid_legacy, mode="template")
    assert rows
    for r in rows:
        assert r["category"] == "verifiable", r          # 全部 verifiable(盲区已补齐)
        assert r["match"] is True, r                     # oracle==引擎,模板句应✓


def test_run_items_template_mode_skips_llm(grid_legacy):
    pools = QG._build_pools(grid_legacy)
    t = pools["lookup"][0]
    e = CorpusEntry(id="x", op="lookup", category="verifiable",
                    target=CR.target_to_dict(t),
                    phrasings=[{"text": QG.fallback_question(t), "src": "模板"},
                               {"text": "一句 LLM 自然句", "src": "LLM"}])
    rows = CR.run_items([e], grid_legacy, mode="template")
    assert len(rows) == 1 and rows[0]["src"] == "模板"   # 闸门只跑模板句
    rows_all = CR.run_items([e], grid_legacy, mode="all")
    assert len(rows_all) == 2


# ---------------- diff_runs:5 类分类 ----------------
def _row(id_, cat, match, orphan=False, expected=1.0, ph="q"):
    return {"id": id_, "op": "lookup", "category": cat, "phrasing": ph,
            "src": "模板", "orphan": orphan, "expected": expected,
            "actual": expected, "match": match, "engine_kind": "single",
            "refused": False}


def test_diff_runs_classification():
    before = [
        _row("a", "verifiable", True, expected=6.5),     # → 将 regressed
        _row("b", "verifiable", False),                   # → 将 improved
        _row("c", "verifiable", True, expected=10.0),     # → 将 value_drift(仍✓,值变)
        _row("d", "verifiable", True),                    # → 将 orphaned
        _row("e", "verifiable", True, expected=7.0),      # → stable
    ]
    after = [
        _row("a", "verifiable", False, expected=6.5),
        _row("b", "verifiable", True),
        _row("c", "verifiable", True, expected=11.0),
        _row("d", "verifiable", False, orphan=True, expected=None),
        _row("e", "verifiable", True, expected=7.0),
    ]
    d = CR.diff_runs(before, after)
    c = d["counts"]
    assert c["regressed_verifiable"] == 1
    assert c["improved"] == 1
    assert c["value_drift"] == 1
    assert c["orphaned"] == 1
    assert c["stable"] == 1
    # changes 只含非 stable,且 regressed 排最前(红线优先)
    assert len(d["changes"]) == 4
    assert d["changes"][0]["cls"] == "regressed_verifiable"
    # verifiable ✓数: before a/c/d/e=4; after b/c/e=3(a 回退、d 孤儿)
    assert d["n_verifiable_ok_before"] == 4 and d["n_verifiable_ok_after"] == 3


def test_diff_runs_blindspot_regression_not_red():
    """盲区类 ✓→✗ 不计入红线(regressed_verifiable),归 regressed_blindspot。"""
    before = [_row("a", "blindspot", True)]
    after = [_row("a", "blindspot", False)]
    d = CR.diff_runs(before, after)
    assert d["counts"]["regressed_verifiable"] == 0
    assert d["counts"]["regressed_blindspot"] == 1

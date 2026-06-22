"""Phase 8.4 · 语义层自动提议器测试(确定性,monkeypatch LLM,不依赖真 endpoint)。

守不变量:
- build_label_inventory 只读标签,**不泄漏 Cell.value**(断言已知数值不在产物里)。
- rules 模板从 max_year 确定性生成(对齐 seed 口径)。
- 闸门:committed seed 全过(resolve_fail=0、errors=0)——证明不误杀正确语义;
  造坏 locator/taxonomy_node → 闸门抓住。
- propose 在 monkeypatch 的合规 LLM 下返回 4 文件 + 闸门过;无 LLM → None。
"""
from __future__ import annotations
import os
import sys
import json
import yaml
import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _ROOT)

import semantic_proposer as SP  # noqa: E402


def _seed_schema() -> dict:
    with open(os.path.join(_ROOT, "schemas", "三峡国际经营数据库.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_sem(name: str) -> dict:
    with open(os.path.join(_ROOT, "semantic", name), encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============================================================ 标签清单(无数值泄漏)
def test_build_label_inventory_labels(grid_legacy):
    inv = SP.build_label_inventory(grid_legacy, _seed_schema())
    fin_labels = inv["sheets"]["财务数据"]["row_labels"]
    assert "利润总额" in fin_labels
    assert "巴西" in inv["sheets"]["发电量"]["subtotal_keys"]
    yr = inv["year_range"]
    assert yr[0] is not None and yr[1] is not None and yr[0] <= yr[1]


def test_build_label_inventory_no_cell_values(grid_legacy):
    """不变量:inventory 只含标签,不含单元格数值。取几个真实 cell.value 断言其不在产物里。"""
    inv = SP.build_label_inventory(grid_legacy, _seed_schema())
    dumped = json.dumps(inv, ensure_ascii=False, default=str)
    # 利润总额 2018=6.5、发电量 2025≈384.4(均为已知单元格数值)
    samples = []
    fin_row = (grid_legacy.fin or {}).get("利润总额") or {}
    if "2018年" in fin_row:
        samples.append(fin_row["2018年"].value)
    sub = (grid_legacy.gen_subtotals or {}).get("合计") or {}
    if "2025年" in sub:
        samples.append(sub["2025年"].value)
    assert samples, "fixture 未取到样本值"
    for v in samples:
        assert str(v) not in dumped, f"Cell.value {v!r} 泄漏进 inventory(违反 LLM 不碰数字)"


# ============================================================ rules 模板(确定性)
def test_template_rules_2025_matches_seed_intent():
    r = SP._template_rules(2025)
    assert r["recent_years"]["windows"]["近一年"] == [2025]
    assert r["recent_years"]["windows"]["近三年"] == [2023, 2024, 2025]
    assert r["recent_years"]["windows"]["近五年"] == [2021, 2022, 2023, 2024, 2025]
    c3 = r["recent_years"]["cagr_initial_rule"]["近三年"]
    assert (c3["initial_year"], c3["end_year"], c3["n"]) == (2022, 2025, 3)
    c1 = r["recent_years"]["cagr_initial_rule"]["近一年"]
    assert (c1["initial_year"], c1["end_year"], c1["n"]) == (2024, 2025, 1)


def test_template_rules_shifts_with_max_year():
    r = SP._template_rules(2028)
    assert r["recent_years"]["windows"]["近三年"] == [2026, 2027, 2028]
    assert r["recent_years"]["cagr_initial_rule"]["近三年"]["initial_year"] == 2025
    assert r["recent_years"]["cagr_initial_rule"]["近五年"]["initial_year"] == 2023


def test_template_rules_lint_clean():
    """模板 rules 须过 lint_rules(无 recent_years 缺失告警)。"""
    import semantic_edit_helpers as SE
    assert [f for f in SE.lint_rules(SP._template_rules(2025)) if f[0] == "error"] == []


# ============================================================ 闸门:seed 全过 + 抓坏
def test_gate_passes_committed_seed(grid_legacy):
    """committed 三峡 metrics/taxonomy/synonyms 经闸门:0 error、0 resolve_fail。"""
    schema = _seed_schema()
    preview = SP._build_preview_grid(grid_legacy, schema)
    gate = SP._gate(_load_sem("metrics.yaml"), _load_sem("taxonomy.yaml"),
                    _load_sem("synonyms.yaml"), SP._template_rules(2025),
                    preview, schema)
    assert gate["errors"] == [], gate["errors"]
    assert gate["resolve_fail"] == [], gate["resolve_fail"]
    assert gate["n_resolve_ok"] == gate["n_metrics"]


def test_gate_catches_bad_row_and_taxonomy_node(grid_legacy):
    schema = _seed_schema()
    preview = SP._build_preview_grid(grid_legacy, schema)
    taxonomy = {"发电方式": {"风电": {"includes": ["陆上风电", "海上风电"]}}}
    metrics = {
        "好指标": {"locator": {"sheet": "财务数据", "row": "利润总额"}, "unit": "亿元"},
        "坏行": {"locator": {"sheet": "财务数据", "row": "根本不存在的行"}, "unit": "亿元"},
        "坏节点": {"taxonomy_node": "不存在的节点", "aggregation": "sum_by_方式",
                 "locator": {"sheet": "发电量"}, "unit": "亿千瓦时"},
    }
    gate = SP._gate(metrics, taxonomy, {}, SP._template_rules(2025), preview, schema)
    bad = {r["metric"] for r in gate["resolve_fail"]}
    assert "坏行" in bad
    assert "好指标" not in bad
    assert any("坏节点" in e["where"] for e in gate["errors"]), gate["errors"]


# ============================================================ propose(LLM monkeypatch)
def _canned_bundle_yaml() -> str:
    return yaml.safe_dump({
        "metrics": {
            "利润总额": {"synonyms": ["利润", "总利润"],
                       "locator": {"sheet": "财务数据", "row": "利润总额"}, "unit": "亿元"},
            "风电发电量": {"parent": "发电量", "taxonomy_node": "风电",
                         "locator": {"sheet": "发电量", "table": "年度"},
                         "unit": "亿千瓦时", "aggregation": "sum_by_方式"},
        },
        "taxonomy": {"发电方式": {"风电": {"includes": ["陆上风电", "海上风电"], "description": "风电"}}},
        "synonyms": {
            "entities": {"三峡国际": {"aliases": ["公司", "本公司"]}},
            "metric_aliases": {"利润": "利润总额"},
            "time_aliases": {"近三年": "recent_3_years"},
            "quantity_aliases": {"累计": "cumulative"},
        },
    }, allow_unicode=True, sort_keys=False)


class _FakeLLM:
    available = True

    def __init__(self, response: str):
        self._response = response

    def chat(self, system, user, json_mode=False, timeout=None):
        return self._response


def test_propose_monkeypatch_canned_llm(grid_legacy):
    schema = _seed_schema()
    res = SP.propose(grid_legacy, schema, client=_FakeLLM(_canned_bundle_yaml()))
    assert res is not None
    files, gate = res
    assert set(files) == {"metrics.yaml", "taxonomy.yaml", "synonyms.yaml", "rules.yaml"}
    assert "利润总额" in files["metrics.yaml"]
    assert "风电" in files["taxonomy.yaml"]
    # 利润总额(真实行标签)resolve ok;风电发电量 taxonomy sheet 在 schema → ok
    assert gate["resolve_fail"] == [], gate["resolve_fail"]
    assert gate["n_resolve_ok"] == 2


def test_propose_unavailable_returns_none(grid_legacy):
    class _NoLLM:
        available = False

        def chat(self, *a, **k):
            raise RuntimeError("不应调用")

    assert SP.propose(grid_legacy, _seed_schema(), client=_NoLLM()) is None


# ============================================================ prompt 不含数值
def test_user_prompt_no_cell_values(grid_legacy):
    """build_user_prompt 产物只含标签,不含单元格数值。"""
    inv = SP.build_label_inventory(grid_legacy, _seed_schema())
    prompt = SP.build_user_prompt(inv)
    fin_row = (grid_legacy.fin or {}).get("利润总额") or {}
    if "2018年" in fin_row:
        assert str(fin_row["2018年"].value) not in prompt

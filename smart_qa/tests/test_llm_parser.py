"""LLM 意图解析器单元测试。

不依赖真实 LLM endpoint —— 测试 _validate_and_build / _time_dicts_to_tokens /
_coerce_known / LLMParser 不可用时的行为。live LLM 在 tests/test_llm_live.py
里跑(需要 key,默认 skip)。
"""
from __future__ import annotations
import os
import sys
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))

from llm_parser import (  # noqa: E402
    LLMParser, ParserUnavailable, _validate_and_build,
    _time_dicts_to_tokens, _coerce_known, _build_metrics_summary,
)
from parser import Intent  # noqa: E402


# ---------------- time 转换 ----------------
class TestTimeConversion:
    def test_year_token(self):
        assert _time_dicts_to_tokens([{"type": "year", "year": 2018}]) == [("year", 2018)]

    def test_ytd_month_token(self):
        assert _time_dicts_to_tokens(
            [{"type": "ytd_month", "year": 2026, "month": 2}]
        ) == [("ytd_month", 2026, 2)]

    def test_recent_token(self):
        assert _time_dicts_to_tokens([{"type": "recent", "years": 3}]) == [("recent", 3)]

    def test_mixed_tokens(self):
        out = _time_dicts_to_tokens([
            {"type": "year", "year": 2024},
            {"type": "year", "year": 2025},
            {"type": "ytd_month", "year": 2026, "month": 2},
        ])
        assert out == [("year", 2024), ("year", 2025), ("ytd_month", 2026, 2)]

    def test_empty(self):
        assert _time_dicts_to_tokens([]) == []

    @pytest.mark.parametrize("bad", [
        {"type": "unknown"},
        {"type": "year"},                    # 缺 year
        {"type": "ytd_month", "year": 2026}, # 缺 month
        {"type": "ytd_month", "year": 2026, "month": 13},  # 越界
        {"type": "recent", "years": 99},     # 越界
        "not a dict",
    ])
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            _time_dicts_to_tokens([bad])


# ---------------- 指标白名单 ----------------
class TestCoerceKnown:
    def test_canonical_passes_through(self):
        assert _coerce_known("利润总额") == "利润总额"
        assert _coerce_known("风电发电量") == "风电发电量"

    def test_synonym_in_metric_aliases(self):
        # synonyms.yaml 里: 利润 -> 利润总额
        assert _coerce_known("利润") == "利润总额"
        assert _coerce_known("分红") == "向集团分红"

    def test_synonym_in_metrics_yaml_synonyms(self):
        # metrics.yaml 里 利润总额.synonyms 含 [利润, 总利润, 净利润近似]
        assert _coerce_known("总利润") == "利润总额"

    def test_unknown_returns_none(self):
        assert _coerce_known("世界上没有的指标") is None
        assert _coerce_known("") is None
        assert _coerce_known(None) is None


# ---------------- validate_and_build ----------------
class TestValidateAndBuild:
    def test_minimal_lookup(self):
        data = {
            "entity": "三峡国际",
            "time": [{"type": "year", "year": 2018}],
            "operation": "lookup",
            "metric": "利润总额",
            "rationale": "测试推理说明",
        }
        intent = _validate_and_build(data)
        assert intent.entity == "三峡国际"
        assert intent.metric == "利润总额"
        assert intent.metrics == ["利润总额"]
        assert intent.time_tokens == [("year", 2018)]
        assert intent.operation == "lookup"
        assert any("LLM 推理" in n for n in intent.notes)

    def test_multi_metric(self):
        data = {
            "entity": "三峡国际",
            "time": [{"type": "year", "year": 2025}],
            "operation": "multi",
            "metric": "总装机",
            "metrics": ["总装机", "可控装机", "利润总额", "发电量"],
        }
        intent = _validate_and_build(data)
        assert intent.metric == "总装机"
        assert intent.metrics == ["总装机", "可控装机", "利润总额", "发电量"]

    def test_cagr(self):
        data = {
            "entity": "三峡国际",
            "time": [{"type": "recent", "years": 3}],
            "operation": "cagr",
            "metric": "利润增长率",
        }
        intent = _validate_and_build(data)
        assert intent.operation == "cagr"
        assert intent.time_tokens == [("recent", 3)]

    def test_ytd_month(self):
        data = {
            "entity": "集团",
            "time": [
                {"type": "year", "year": 2024},
                {"type": "year", "year": 2025},
                {"type": "ytd_month", "year": 2026, "month": 2},
            ],
            "operation": "sum",
            "metric": "向集团分红",
        }
        intent = _validate_and_build(data)
        assert intent.entity == "集团"
        assert intent.operation == "sum"

    def test_unknown_metric_dropped(self):
        data = {
            "entity": "三峡国际",
            "time": [{"type": "year", "year": 2025}],
            "operation": "lookup",
            "metric": "完全没听说过的指标",
        }
        intent = _validate_and_build(data)
        assert intent.metric is None
        assert intent.metrics == []
        assert any("不在白名单" in n for n in intent.notes)

    def test_synonym_metric_normalized(self):
        data = {
            "entity": "公司",
            "time": [{"type": "year", "year": 2025}],
            "operation": "lookup",
            "metric": "利润",
        }
        intent = _validate_and_build(data)
        assert intent.metric == "利润总额"   # synonyms.yaml 映射

    def test_unmapped_entity_falls_back_to_default(self):
        data = {
            "entity": "本公司",   # 口语,应映射到 三峡国际
            "time": [{"type": "year", "year": 2025}],
            "operation": "lookup",
            "metric": "利润总额",
        }
        intent = _validate_and_build(data)
        assert intent.entity == "三峡国际"

    @pytest.mark.parametrize("bad_entity", ["", None, 123])
    def test_entity_required(self, bad_entity):
        data = {
            "entity": bad_entity,
            "time": [{"type": "year", "year": 2025}],
            "operation": "lookup",
        }
        with pytest.raises(ValueError):
            _validate_and_build(data)

    @pytest.mark.parametrize("bad_op", ["drop_table", "", None, "LOOKUP"])
    def test_invalid_operation_rejected(self, bad_op):
        data = {
            "entity": "三峡国际",
            "time": [{"type": "year", "year": 2025}],
            "operation": bad_op,
        }
        with pytest.raises(ValueError):
            _validate_and_build(data)

    def test_extra_fields_silently_dropped(self):
        """LLM 可能在 JSON 里夹带计算结果/解释,这些字段必须被丢弃。"""
        data = {
            "entity": "三峡国际",
            "time": [{"type": "year", "year": 2018}],
            "operation": "lookup",
            "metric": "利润总额",
            "value": 6.5,                  # LLM 幻觉: 算出了数
            "cell_address": "M6",          # LLM 幻觉: 编了地址
            "explanation": "这是答案",     # LLM 幻觉: 长篇解释
        }
        intent = _validate_and_build(data)
        # Intent 里没有 value / cell_address / explanation 字段
        d = intent.__dict__
        assert "value" not in d
        assert "cell_address" not in d
        assert "explanation" not in d
        assert intent.metric == "利润总额"

    def test_top_level_not_dict(self):
        with pytest.raises(ValueError):
            _validate_and_build([1, 2, 3])
        with pytest.raises(ValueError):
            _validate_and_build("string")


# ---------------- LLMParser 实例行为 ----------------
class TestLLMParserInstance:
    def test_unavailable_without_key(self, monkeypatch):
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        p = LLMParser()
        assert not p.available
        assert "未配置" in p.status()

    def test_unavailable_raises_on_parse(self, monkeypatch):
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        p = LLMParser()
        with pytest.raises(ParserUnavailable):
            p.parse("any question")

    def test_available_with_explicit_args(self, monkeypatch):
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        p = LLMParser(base_url="http://x", api_key="k", model="m")
        assert p.available
        assert "http://x" in p.status()
        assert "m" in p.status()


# ---------------- metrics 摘要 ----------------
class TestMetricsSummary:
    def test_summary_contains_known_metrics(self):
        s = _build_metrics_summary()
        assert "利润总额" in s
        assert "亿元" in s
        assert "taxonomy" in s.lower() or "分类" in s or "taxonomy:" in s

    def test_summary_not_empty(self):
        assert len(_build_metrics_summary()) > 50

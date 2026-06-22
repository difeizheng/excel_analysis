"""LLM 实时连通性测试(需要真实 endpoint + key)。

默认 skip。运行方式:
    LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=... pytest tests/test_llm_live.py -v
"""
from __future__ import annotations
import os
import sys
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))

from llm_parser import LLMParser, ParserUnavailable  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY")),
    reason="需要 LLM_BASE_URL + LLM_API_KEY 环境变量才会跑",
)


@pytest.fixture(scope="module")
def parser() -> LLMParser:
    p = LLMParser()
    if not p.available:
        pytest.skip("LLM 未配置")
    return p


CASES = [
    ("公司2018年的利润总额是多少？",
     {"metric": "利润总额", "entity": "三峡国际",
      "time_kind": "year", "operation": "lookup"}),
    ("三峡国际2022、2024、2025年每年的汇兑净损失是多少？",
     {"metric": "汇兑净损失", "entity": "三峡国际",
      "operation": "multi", "time_count": 3}),
    ("24年-26年2月累计向集团分红多少？",
     {"metric": "向集团分红", "entity": "集团",
      "operation": "sum", "time_count": 3}),
    ("公司近三年的利润增长率是多少？",
     {"metric": "利润增长率", "operation": "cagr"}),
    ("三峡国际2025年的总装机、可控装机、利润总额、发电量是多少？",
     {"metrics_count": 4, "operation": "multi"}),
    ("公司2025年风电发电量是多少",
     {"metric": "风电发电量", "operation": "sum", "taxonomy": "风电"}),
]


@pytest.mark.parametrize("question,expected", CASES, ids=[c[0][:20] for c in CASES])
def test_golden_case(parser: LLMParser, question: str, expected: dict):
    try:
        intent = parser.parse(question)
    except ParserUnavailable as e:
        pytest.fail(f"LLM 不可用: {e}")

    assert intent.operation == expected["operation"], \
        f"operation 不对: {intent.operation} vs {expected['operation']}"
    if "metric" in expected:
        assert intent.metric == expected["metric"], \
            f"metric 不对: {intent.metric} vs {expected['metric']}"
    if "entity" in expected:
        assert intent.entity == expected["entity"], \
            f"entity 不对: {intent.entity} vs {expected['entity']}"
    if "time_count" in expected:
        assert len(intent.time_tokens) == expected["time_count"], \
            f"time token 数不对: {len(intent.time_tokens)}"
    if "metrics_count" in expected:
        assert len(intent.metrics) == expected["metrics_count"], \
            f"metrics 数不对: {len(intent.metrics)}"
    # 关键:Intent 里不能有 LLM 幻想的 value/addr 字段
    assert not hasattr(intent, "value")
    assert not hasattr(intent, "cell_address")

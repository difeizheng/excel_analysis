"""LLM 意图解析器：调 OpenAI 兼容 API 把自然语言 -> Intent。

设计原则（防幻觉三道闸）:
  1. LLM 只看 metrics/entities 的白名单,系统 prompt 明示严禁输出数值/单元格
  2. 响应必须可解析为 JSON,字段全部进白名单过滤（LLM 多输出的字段直接丢弃）
  3. 校验层再做: 指标必须在白名单、operation 必须枚举、time 字段合法
     任一不通过 -> 抛 ParserUnavailable,上层回退到规则解析器

接口: parse(question) -> Intent
依赖: llm_client(OpenAI 兼容,urllib 标准库),无第三方包。
HTTP/配置统一委托给 llm_client.LLMClient,本模块只负责 prompt 构造与白名单校验。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from parser import Intent
from llm_client import LLMClient, LLMUnavailable

log = logging.getLogger(__name__)


class ParserUnavailable(RuntimeError):
    """LLM 不可用(未配置 / 网络失败 / 响应非法),上层应回退到规则解析器。"""


# ---------------- 严格 JSON schema(LLM 只能输出这些字段) ----------------
INTENT_SCHEMA_FIELDS = {
    "metric": str,
    "metrics": list,
    "entity": str,
    "time": list,
    "operation": str,
    "rationale": str,
}
ALLOWED_OPS = ("lookup", "sum", "cagr", "multi", "yoy", "share", "peak_year", "rank")
ALLOWED_ENTITY = ("三峡国际", "集团", "未指明")
ALLOWED_TIME_TYPES = ("year", "ytd_month", "recent")


def _build_metrics_summary() -> str:
    """从 metrics.yaml 提炼可发给 LLM 的清单(只含标准名+同义词+单位+分类)。"""
    import semantic_layer as S  # 延迟导入避免循环
    lines: list[str] = []
    for canon, info in S.METRICS.items():
        syns = (info or {}).get("synonyms", []) or []
        unit = (info or {}).get("unit", "")
        tax = (info or {}).get("taxonomy_node", "")
        line = f"- {canon}"
        if syns:
            line += f" (口语: {', '.join(syns)})"
        if unit:
            line += f" [{unit}]"
        if tax:
            line += f" [taxonomy:{tax}]"
        lines.append(line)
    return "\n".join(lines) or "(无指标)"


SYSTEM_PROMPT_TEMPLATE = """你是「智能问数」系统的意图解析器。任务:把用户的中文问题转成严格 JSON,不做任何计算。

# 可用指标(输出 metric 时必须用其中的标准名,不要用口语)
{metrics_list}

# 可用实体(输出 entity 时必须用以下值之一)
- "三峡国际" — 用户口语可能说:公司 / 本公司 / 三峡
- "集团"     — 用户口语可能说:集团公司 / 上级集团 / 母公司
- "未指明"   — 用户没提任何实体

# 可用分类节点(taxonomy_node, 对应 sum 运算)
- 风电   = 陆上风电 + 海上风电
- 水电   = 水电
- 光伏   = 光伏
- 新能源 = 陆上风电 + 海上风电 + 光伏

# 时间 token 三种类型
- {{"type":"year","year":2024}}              某一年
- {{"type":"ytd_month","year":2026,"month":2}} 某年某月(用于月度列,按 YTD 规则取最新月,不逐月相加)
- {{"type":"recent","years":3}}               近 N 年(用于 CAGR)

# operation 八种取值
- lookup    单点取数
- sum       求和(累计/合计/分类归并)
- cagr      复合增长率(问题含"增长率/CAGR/复合"才用)
- multi     多指标或多时间点枚举
- yoy       同比增长(问题含"同比/环比"才用;time 给当年,系统自动取上年)
- share     占比/比重(问题含"占比/比重/份额"才用;总体默认发电量,无需在 JSON 表达)
- peak_year 峰值年(问题含"最高/最多/峰值/哪一年"才用;扫描全年份,time 可空)
- rank      排名(问题含"排名/排行/从高到低"才用;各区域发电量按值降序)

# 严格规则
1. **严禁输出任何数值、单元格地址、计算结果** — 你只负责"理解+选址"
2. 找不到对应指标的指标名,就把 metric 字段省略(metrics 也空)
3. 多指标问题(如"总装机、可控装机、利润、发电量")用 metrics 数组,operation=multi
4. CAGR 期初取窗口前一年底 — 这是业务规则,你无需在 JSON 里表达,系统会处理

# 输出 JSON schema(必须严格符合)
{{
  "metric":    "<标准指标名 或 省略>",
  "metrics":   ["<标准指标名>", ...],
  "entity":    "三峡国际" | "集团" | "未指明",
  "time":      [<time token>, ...],
  "operation": "lookup" | "sum" | "cagr" | "multi",
  "rationale": "<一两句话解释你的理解>"
}}

# 示例 1
Q: 公司2018年的利润总额是多少？
A: {{"metric":"利润总额","entity":"三峡国际","time":[{{"type":"year","year":2018}}],"operation":"lookup","rationale":"问2018年公司利润总额,单点取数"}}

# 示例 2
Q: 三峡国际2022、2024、2025年每年的汇兑净损失是多少？
A: {{"metric":"汇兑净损失","entity":"三峡国际","time":[{{"type":"year","year":2022}},{{"type":"year","year":2024}},{{"type":"year","year":2025}}],"operation":"multi","rationale":"非连续多年枚举取数"}}

# 示例 3
Q: 24年-26年2月累计向集团分红多少？
A: {{"metric":"向集团分红","entity":"集团","time":[{{"type":"year","year":2024}},{{"type":"year","year":2025}},{{"type":"ytd_month","year":2026,"month":2}}],"operation":"sum","rationale":"跨年+月度YTD累计"}}

# 示例 4
Q: 公司近三年的利润增长率是多少？
A: {{"metric":"利润增长率","entity":"三峡国际","time":[{{"type":"recent","years":3}}],"operation":"cagr","rationale":"复合年增长率"}}

# 示例 5
Q: 三峡国际2025年的总装机、可控装机、利润总额、发电量是多少？
A: {{"metric":"总装机","metrics":["总装机","可控装机","利润总额","发电量"],"entity":"三峡国际","time":[{{"type":"year","year":2025}}],"operation":"multi","rationale":"四指标同年取数"}}

# 示例 6 (同比)
Q: 公司2024年利润总额同比增长了多少？
A: {{"metric":"利润总额","entity":"三峡国际","time":[{{"type":"year","year":2024}}],"operation":"yoy","rationale":"同比=与上年比,time 给当年"}}

# 示例 7 (占比;总体默认发电量,无需表达)
Q: 2025年巴西发电量占总发电量的比重是多少？
A: {{"metric":"巴西发电量","entity":"三峡国际","time":[{{"type":"year","year":2025}}],"operation":"share","rationale":"占比=部分÷总体"}}

# 示例 8 (峰值年;扫描全年份,time 空)
Q: 公司哪一年的发电量最高？
A: {{"metric":"发电量","entity":"三峡国际","time":[],"operation":"peak_year","rationale":"全年份最高值所在年"}}

# 示例 9 (排名;各区域按值降序)
Q: 2025年各区域发电量从高到低排名？
A: {{"metric":"发电量","entity":"三峡国际","time":[{{"type":"year","year":2025}}],"operation":"rank","rationale":"各区域按值降序"}}
"""


def _time_dicts_to_tokens(time_list: list[dict]) -> list[tuple]:
    """LLM 的 time dict 列表 -> pipeline 用的 tuple 列表。"""
    tokens: list[tuple] = []
    for t in time_list:
        if not isinstance(t, dict):
            raise ValueError(f"time 项不是 dict: {t!r}")
        ttype = t.get("type")
        if ttype not in ALLOWED_TIME_TYPES:
            raise ValueError(f"非法 time.type: {ttype!r}")
        try:
            if ttype == "year":
                y = int(t["year"])
                tokens.append(("year", y))
            elif ttype == "ytd_month":
                y, m = int(t["year"]), int(t["month"])
                if not 1 <= m <= 12:
                    raise ValueError(f"month 越界: {m}")
                tokens.append(("ytd_month", y, m))
            elif ttype == "recent":
                n = int(t["years"])
                if not 1 <= n <= 10:
                    raise ValueError(f"recent.years 越界: {n}")
                tokens.append(("recent", n))
        except KeyError as e:
            raise ValueError(f"time 项缺字段: {e.args[0]!r} in {t!r}")
    return tokens


def _normalize_entity(llm_entity: str) -> str:
    """LLM 直接返回规范名('三峡国际'/'集团'/'未指明');口语需回退到 resolve_entity。"""
    import semantic_layer as S
    if not isinstance(llm_entity, str):
        raise ValueError(f"entity 必须是字符串,实际={type(llm_entity).__name__}")
    if llm_entity == "未指明" or not llm_entity:
        return S.resolve_entity("")  # 默认三峡国际
    if llm_entity in ("三峡国际", "集团"):
        return llm_entity
    # 兜底:让 resolve_entity 兜一遍(LLM 可能返回 '公司' 等口语)
    return S.resolve_entity(llm_entity) or "三峡国际"


def _coerce_known(metric: str | None) -> str | None:
    """把 LLM 返回的指标名规范化为 metrics.yaml 里的标准名。"""
    import semantic_layer as S
    if not metric:
        return None
    # 1) 直接命中
    if metric in S.METRICS:
        return metric
    # 2) 在同义词表里
    aliases = S.SYNONYMS.get("metric_aliases") or {}
    if metric in aliases:
        return aliases[metric]
    # 3) 在 metrics.yaml 的 synonyms 字段里(反向查)
    for canon, info in S.METRICS.items():
        for syn in (info or {}).get("synonyms", []) or []:
            if syn == metric:
                return canon
    return None  # 不在白名单


def _validate_and_build(data: Any) -> Intent:
    """严格白名单校验 + 字段白名单过滤(LLM 多输出的字段直接丢弃)。"""
    if not isinstance(data, dict):
        raise ValueError(f"LLM 返回非 dict: {type(data).__name__}")

    # 只保留 schema 内的字段
    cleaned: dict = {k: v for k, v in data.items() if k in INTENT_SCHEMA_FIELDS}

    # 必填
    entity_raw = cleaned.get("entity", "")
    time_raw = cleaned.get("time", [])
    op_raw = cleaned.get("operation", "")
    if not entity_raw:
        raise ValueError("entity 缺失")
    if not isinstance(time_raw, list):
        raise ValueError("time 必须为数组")
    if op_raw not in ALLOWED_OPS:
        raise ValueError(f"非法 operation: {op_raw!r}")

    entity = _normalize_entity(entity_raw)
    time_tokens = _time_dicts_to_tokens(time_raw)

    # 指标白名单化(LLM 返回口语时映射回标准名;映射不到视为未指明指标)
    metric_raw = cleaned.get("metric")
    metric = _coerce_known(metric_raw) if metric_raw else None

    metrics_raw = cleaned.get("metrics") or []
    if not isinstance(metrics_raw, list):
        raise ValueError("metrics 必须为数组")
    metrics: list[str] = []
    for m in metrics_raw:
        canon = _coerce_known(m) if isinstance(m, str) else None
        if canon and canon not in metrics:
            metrics.append(canon)

    # 规则:多指标时主指标 = metrics[0]
    if metrics and not metric:
        metric = metrics[0]
    elif metric and not metrics:
        metrics = [metric]

    notes: list[str] = []
    rationale = cleaned.get("rationale")
    if isinstance(rationale, str) and rationale:
        notes.append(f"LLM 推理: {rationale[:200]}")
    notes.append(f"LLM 实体: {entity_raw} -> 规范化: {entity}")
    if metric_raw and not metric:
        notes.append(f"LLM 返回的指标「{metric_raw}」不在白名单,已丢弃")

    return Intent(
        question="",  # 由调用方填
        entity=entity,
        metric=metric,
        metrics=metrics,
        time_tokens=time_tokens,
        operation=op_raw,
        notes=notes,
    )


class LLMParser:
    """OpenAI 兼容 LLM 意图解析器。

    使用方式:
        parser = LLMParser()  # 自动从 env 读 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
        if parser.available:
            intent = parser.parse(question)
        else:
            intent = rule_parser.parse(question)  # 回退
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        system_prompt: str | None = None,
    ) -> None:
        # HTTP/配置统一委托给 llm_client.LLMClient
        self._client = LLMClient(
            base_url=base_url, api_key=api_key, model=model, timeout=timeout,
        )
        self._system: str = system_prompt or SYSTEM_PROMPT_TEMPLATE.format(
            metrics_list=_build_metrics_summary()
        )

    @property
    def available(self) -> bool:
        return self._client.available

    def status(self) -> str:
        """一行可读的诊断信息,用于日志/前端状态条。"""
        base = self._client.status()
        return base + ",回退规则解析器" if not self._client.available else base

    def parse(self, question: str) -> Intent:
        if not self._client.available:
            raise ParserUnavailable("LLM 未配置")

        log.info("LLM parse: %s", question[:50])
        try:
            content = self._client.chat(self._system, question, json_mode=True)
        except LLMUnavailable as e:
            raise ParserUnavailable(str(e)) from e
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ParserUnavailable(f"LLM 返回非 JSON: {content[:200]}") from e

        intent = _validate_and_build(data)
        intent.question = question
        return intent


# ---------------- 模块级 singleton(供 qa.py 直接 import)----------------
_default_parser: LLMParser | None = None


def get_default() -> LLMParser:
    """惰性构造:第一次调用时读 env,后续复用。"""
    global _default_parser
    if _default_parser is None:
        _default_parser = LLMParser()
    return _default_parser


if __name__ == "__main__":  # CLI 调试: python -m src.llm_parser "问题"
    import sys
    logging.basicConfig(level=logging.INFO)
    p = get_default()
    print(p.status())
    if len(sys.argv) < 2:
        print("用法: python -m src.llm_parser \"<问题>\"")
        sys.exit(1)
    if not p.available:
        print("LLM 未配置,无法解析")
        sys.exit(2)
    try:
        intent = p.parse(sys.argv[1])
    except ParserUnavailable as e:
        print(f"LLM 不可用: {e}")
        sys.exit(3)
    import dataclasses
    print(json.dumps(dataclasses.asdict(intent), ensure_ascii=False, indent=2, default=str))

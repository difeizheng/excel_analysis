"""意图解析层（规则解析器 + 可选 LLM hybrid 入口）。

职责:自然语言 -> 结构化 Intent。只做"理解",不产数字、不碰单元格。
接口固定:parse(question) -> Intent。生产环境可换 LLMParser(同接口)。
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
import semantic_layer as S

log = logging.getLogger(__name__)

_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


@dataclass
class Intent:
    question: str
    entity: str
    metric: str | None = None
    metrics: list = field(default_factory=list)
    time_tokens: list = field(default_factory=list)   # ('year',y)/('ytd_month',y,m)/('recent',n)
    operation: str = "lookup"                          # lookup|sum|cagr|multi
    notes: list = field(default_factory=list)


def _cn2int(s: str) -> int:
    return _CN.get(s, int(s) if s.isdigit() else 3)


def _ny(s: str) -> int:
    n = int(s)
    return n + 2000 if n < 100 else n


def parse_time(text: str) -> list:
    """解析时间 -> token 列表(业务规则在 planner 应用)。"""
    m = re.search(r"近([一二三四五六七八九十\d]+)年", text)
    if m:
        return [("recent", _cn2int(m.group(1)))]
    # 跨年范围带终止月:"24年-26年2月" -> [2024, 2025, YTD(2026,2)]
    m = re.search(r"(\d{2,4})\s*年\s*[-—至到~]\s*(\d{2,4})\s*年\s*(\d{1,2})\s*月", text)
    if m:
        y1, y2, mon = _ny(m.group(1)), _ny(m.group(2)), int(m.group(3))
        return [("year", y) for y in range(y1, y2)] + [("ytd_month", y2, mon)]
    # 跨年 YTD 月度:"25-26年1月" / "25-26年3月" -> [2025, YTD(2026,1/3)]
    m = re.search(r"(\d{2,4})\s*[-—至到~]\s*(\d{2,4})\s*年\s*(\d{1,2})\s*月", text)
    if m:
        y1, y2, mon = _ny(m.group(1)), _ny(m.group(2)), int(m.group(3))
        return [("year", y) for y in range(y1, y2)] + [("ytd_month", y2, mon)]
    # 单年内月份范围:"26年1-2月" -> YTD(2026, 终止月=2)
    m = re.search(r"(\d{2,4})\s*年\s*(\d{1,2})\s*[-—至到~]\s*(\d{1,2})\s*月", text)
    if m:
        return [("ytd_month", _ny(m.group(1)), int(m.group(3)))]
    # 跨年范围(无末尾月份):"22-25年" / "2022-2024年" / "2022-2024"
    m = re.search(r"(\d{2,4})\s*[-—至到~]\s*(\d{2,4})\s*年", text)
    if m:
        y1, y2 = _ny(m.group(1)), _ny(m.group(2))
        return [("year", y) for y in range(y1, y2 + 1)]
    # 单年范围："22-25年累计"（起年不带"年"但末尾带"年"）
    m = re.search(r"(\d{2,4})\s*[-—至到~]\s*(\d{2,4})(?!\s*年)", text)
    if m and re.search(r"\d{2,4}\s*年", text):
        y1, y2 = _ny(m.group(1)), _ny(m.group(2))
        return [("year", y) for y in range(y1, y2 + 1)]
    # 顿号/逗号枚举年份:"2022、2023、2024、2025年的..." -> 4 个年份
    m = re.search(r"((?:\d{2,4}\s*[、,，]\s*)+\d{2,4})\s*年?", text)
    if m:
        parts = re.split(r"\s*[、,，]\s*", m.group(1))
        ys: list[int] = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            try:
                ys.append(_ny(p))
            except (ValueError, TypeError):
                pass
        if len(ys) > 1:
            return [("year", y) for y in sorted(set(ys))]
    # 枚举年份(含省略"年"的 4 位数)
    ys = set()
    for mm in re.finditer(r"(\d{2,4})\s*年", text):
        ys.add(_ny(mm.group(1)))
    for mm in re.finditer(r"(?<!\d)(20\d{2})(?!\d)", text):
        ys.add(int(mm.group(1)))
    return [("year", y) for y in sorted(ys)]


def parse(question: str) -> Intent:
    entity = S.resolve_entity(question)
    metrics = S.resolve_metrics(question)
    metric = metrics[0] if metrics else None
    time_tokens = parse_time(question)

    # 运算推断(顺序敏感:新 op 关键词先于 cagr,但不与 6 golden 问句冲突)
    if re.search(r"同比|环比", question):
        op = "yoy"
    elif re.search(r"占比|比重|份额", question):
        op = "share"
    elif re.search(r"最高|最多|峰值|哪一年|哪年", question):
        op = "peak_year"
    elif re.search(r"排名|排行|从高到低|从低到高", question):
        op = "rank"
    elif re.search(r"增长率|增长|CAGR|复合|变化率|变动率", question):
        op = "cagr"
    elif len(metrics) > 1:
        op = "multi"
    elif len(time_tokens) > 1:
        # 避免误匹配"利润总额"里的"总" — 用更严格的关键字
        op = "sum" if re.search(r"累计|合计|总共|共计|总和|总共的|一共", question) else "multi"
    elif metric and S.is_taxonomy_node(metric):
        op = "sum"
    else:
        op = "lookup"

    notes = []
    if metric and S.metric_info(metric).get("default_entity"):
        notes.append("实体默认口径: " + entity)
    return Intent(question=question, entity=entity, metric=metric, metrics=metrics,
                  time_tokens=time_tokens, operation=op, notes=notes)


def parse_hybrid(question: str, llm: "LLMParser | None" = None,
                 fallback: bool = True) -> Intent:
    """优先用 LLM,失败/未配置时回退到规则解析器。

    参数:
        question: 用户问题
        llm:     LLMParser 实例(默认从 env 读)
        fallback: True=LLM 失败回退规则;False=抛异常
    """
    # 延迟导入避免循环依赖
    from llm_parser import LLMParser, ParserUnavailable

    if llm is None:
        llm = LLMParser()
    if llm.available:
        try:
            intent = llm.parse(question)
            return intent
        except ParserUnavailable as e:
            log.warning("LLM parse failed (%s), fallback to rules", e)
            if not fallback:
                raise
    return parse(question)

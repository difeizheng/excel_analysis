"""7 层流转结构化追踪:把一个问题拆到七层,返回结构化 dict(不 print)。

复用 qa.ask 的权威链路拿最终 ans(intent/result/operands/verified 都在其中),
再补齐语义层(消歧)与 Grid 切片的中间产物,供工作台问数台展示
"为何无需大模型也能答对"。

每层产物都是 JSON 可序列化的纯数据(供 Streamlit 直接 st.json 展示)。
"""
from __future__ import annotations
import dataclasses
from typing import Any

import semantic_layer as S
import pipeline as PL


def _asdict(o: Any) -> Any:
    """dataclass 安全转 dict(非 dataclass 原样返回)。"""
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    return o


def trace_question(grid, question: str, use_llm: bool = False, ans: dict | None = None) -> dict:
    """返回 7 层结构化追踪。

    use_llm=True 时第③层意图走 LLM(若可用);层④-⑦始终确定性(LLM 不参与算数)。
    ans 可传入已算好的 qa.ask 结果,避免重复调用。
    """
    import qa
    from llm_parser import get_default

    if ans is None:
        ans = qa.ask(grid, question, use_llm=use_llm, backend="memory")
    intent = ans.get("intent")

    # ② 语义层(展示口语→标准概念的消歧)
    entity = S.resolve_entity(question)
    metric = S.resolve_metric(question)
    info = S.metric_info(metric) if metric else {}

    # ③ 意图来源判断
    llm_on = use_llm and get_default().available
    intent_source = "LLM" if llm_on else "规则"

    # ① Grid 切片:按 metric locator 取相关行
    grid_slice = _grid_slice_for_metric(grid, metric, info)

    # ④ 规划:col_key(从 time_token 翻译)
    col_keys: list[str] = []
    if intent:
        for tk in (intent.time_tokens or []):
            try:
                col_keys.append(PL._colkey(tk))
            except Exception:
                pass

    return {
        "question": question,
        "use_llm": use_llm,
        "intent_source": intent_source,
        "layer1_grid": grid_slice,
        "layer2_semantic": {
            "entity": entity,
            "metric": metric,
            "unit": info.get("unit", ""),
            "locator": info.get("locator"),
            "is_taxonomy": bool(info.get("taxonomy_node")),
            "default_entity": info.get("default_entity"),
        },
        "layer3_intent": _asdict(intent) if intent else None,
        "layer4_plan": {
            "operation": getattr(intent, "operation", None),
            "col_keys": col_keys,
        },
        "layer5_exec": _exec_dict(ans),
        "layer6_answer": ans.get("text", ""),
        "layer7_verify": {
            "kind": ans.get("kind"),
            "verified": ans.get("verified", False),
            "verify_msg": ans.get("verify_msg", ""),
        },
    }


def _grid_slice_for_metric(grid, metric: str | None, info: dict) -> dict:
    """按 metric locator 从 grid 取相关行切片(展示用)。"""
    if not metric or not info:
        return {"note": "无指标"}
    loc = info.get("locator") or {}
    sheet = loc.get("sheet")
    row = loc.get("row")
    if not row:
        return {"note": f"taxonomy/无单点 locator: {loc}"}
    try:
        if sheet == "财务数据":
            r = grid.fin.get(row)
            return {"source": "grid.fin", "row_label": row, "cells": _cells_dict(r)} if r else {"note": f"fin 无 {row!r}"}
        if sheet == "装机":
            r = grid.cap.get(row)
            return {"source": "grid.cap", "row_label": row, "cells": _cells_dict(r)} if r else {"note": f"cap 无 {row!r}"}
        if sheet == "发电量":
            r = grid.gen_subtotals.get(row)
            return {"source": "grid.gen_subtotals", "emit_key": row, "cells": _cells_dict(r)} if r else {"note": f"gen_subtotals 无 {row!r}"}
    except Exception as e:
        return {"note": f"切片异常: {e}"}
    return {"note": f"locator 未覆盖: {loc}"}


def _cells_dict(row: dict | None) -> dict:
    """{col_key: {value, addr, numeric}}(用于展示)。"""
    out: dict = {}
    for k, c in (row or {}).items():
        out[str(k)] = {
            "value": getattr(c, "value", None),
            "addr": getattr(c, "addr", ""),
            "numeric": getattr(c, "numeric", False),
        }
    return out


def _exec_dict(ans: dict) -> dict:
    """第⑤层执行结果(single/multi/fail)。"""
    kind = ans.get("kind")
    if kind == "single":
        res = ans.get("result")
        return _asdict(res) if res else {}
    if kind == "multi":
        return {"items": ans.get("items", [])}
    return {"fail_msg": ans.get("msg", "")}

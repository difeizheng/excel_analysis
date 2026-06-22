"""语料库闭环的纯计算层:重算 expected + 跑验证 + 前后 diff。

无 streamlit 依赖(便于单测)。消费 question_generator 的 oracle 分发(re_derive)
与验证(verify_question),以及 corpus_store 的 CorpusEntry。

两类入口:
- run_items(entries, grid, mode):把语料对【当前 grid】跑一遍,每条问法一行结果。
    mode="template" 只跑模板句(闸门用:确定性、稳定、省时);
    mode="all"     跑全部问法(回归页用:含 LLM 自然句,看覆盖)。
- diff_runs(before, after):两次 run 逐问法比对,分 5 类——
    regressed(✓→✗,可验证类=红线) / improved(✗→✓) /
    value_drift(expected 变、命中不变 = "grid 口径也变了") /
    orphaned(原可算→现孤儿) / stable。
  value_drift 正是用户要的"优化后看到 grid 也发生了变化"。
"""
from __future__ import annotations
import question_generator as QG
from question_generator import QuestionTarget
from corpus_store import CorpusEntry


# ============================================================ 序列化(QG ↔ 语料 entry)
def target_to_dict(t: QuestionTarget) -> dict:
    """QuestionTarget → 可落盘 dict(去 expected/expected_addr;time_tokens 元组转 list)。
    含 category,供 upsert 落到 entry 层。"""
    return {
        "entity": t.entity, "metric": t.metric,
        "operation": t.operation, "category": t.category,
        "time_tokens": [list(tt) for tt in (t.time_tokens or [])],
        "metrics": list(t.metrics or []),
        "extras": dict(t.extras or {}),
    }


def entry_to_target(e: CorpusEntry) -> QuestionTarget:
    """语料 entry → QuestionTarget(不填 expected;expected 永远现场重算)。

    category 运行时从 op 推导(QG._CATEGORY),不读冻存的 e.category ——
    旧语料(盲区时代冻存 category:"blindspot")在 op 已补齐后自动重分类为 verifiable,
    无需迁移脚本(与"永不信任冻存、现场重算"哲学一致)。
    """
    td = e.target or {}
    op = td.get("operation", e.op)
    return QuestionTarget(
        entity=td.get("entity", ""), metric=td.get("metric", ""),
        operation=op, category=QG._CATEGORY.get(op, "verifiable"),
        time_tokens=[list(tt) for tt in (td.get("time_tokens") or [])],
        metrics=list(td.get("metrics") or []),
        extras=dict(td.get("extras") or {}),
    )


# ============================================================ 跑一遍语料
def run_items(entries, grid, *, mode: str = "all") -> list[dict]:
    """对【当前 grid】跑语料,每条( entry × 问法 )一行。

    返回行:{id, op, category, phrasing, src, orphan, expected, actual, match,
            engine_kind, refused}。re_derive 算不出 → orphan(True),不调引擎。
    """
    rows = []
    for e in entries:
        tgt = entry_to_target(e)
        cat = QG._CATEGORY.get(e.op, "verifiable")   # 运行时推导(不信任冻存 category)
        for ph in e.phrasings:
            src = ph.get("src", "")
            if mode == "template" and src != "模板":
                continue
            text = ph.get("text", "")
            exp = QG.re_derive(tgt, grid)
            if exp is None:
                rows.append({"id": e.id, "op": e.op, "category": cat,
                             "phrasing": text, "src": src, "orphan": True,
                             "expected": None, "actual": None, "match": False,
                             "engine_kind": "orphan", "refused": True})
                continue
            vr = QG.verify_question(text, grid, exp)
            rows.append({"id": e.id, "op": e.op, "category": cat,
                         "phrasing": text, "src": src, "orphan": False,
                         "expected": exp.get("value"), "actual": vr.get("actual"),
                         "match": bool(vr.get("match", False)),
                         "engine_kind": vr.get("engine_kind", "?"),
                         "refused": bool(vr.get("refused", False))})
    return rows


# ============================================================ diff
def _vals_equal(a, b) -> bool:
    if a is None or b is None:
        return a == b
    if isinstance(a, list) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_vals_equal(x, y) for x, y in zip(a, b))
    try:
        return round(float(a), 4) == round(float(b), 4)
    except (TypeError, ValueError):
        return a == b


def _classify(b: dict, a: dict) -> str:
    if a["orphan"] and not b["orphan"]:
        return "orphaned"
    if b["orphan"] and not a["orphan"]:
        return "recovered"
    if b["orphan"] and a["orphan"]:
        return "stable"
    if b["match"] and not a["match"]:
        return "regressed_verifiable" if a["category"] == "verifiable" else "regressed_blindspot"
    if not b["match"] and a["match"]:
        return "improved"
    if not _vals_equal(b["expected"], a["expected"]):
        return "value_drift"
    return "stable"


_ORDER = {"regressed_verifiable": 0, "orphaned": 1, "value_drift": 2,
          "regressed_blindspot": 3, "recovered": 4, "improved": 5, "stable": 6}


def diff_runs(before: list[dict], after: list[dict]) -> dict:
    """两次 run 逐( id × 问法 )比对。返回 {changes, counts, n_verifiable_ok_*}。

    changes 只含非 stable,按严重度排序(regressed 红 → orphaned → value_drift 琥珀 → …)。
    counts 含全部 7 类计数(含 stable)。n_verifiable_ok_* = 可验证类且✓的条数(算✓率)。
    """
    bm = {(r["id"], r["phrasing"]): r for r in before}
    am = {(r["id"], r["phrasing"]): r for r in after}
    counts = {k: 0 for k in _ORDER}
    changes = []
    for k in bm.keys() | am.keys():
        b, a = bm.get(k), am.get(k)
        if not b or not a:
            continue
        cls = _classify(b, a)
        counts[cls] += 1
        if cls != "stable":
            changes.append({
                "id": k[0], "op": a["op"], "category": a["category"],
                "phrasing": k[1], "src": a["src"], "cls": cls,
                "before": {"match": b["match"], "expected": b["expected"],
                           "orphan": b["orphan"], "engine_kind": b["engine_kind"]},
                "after": {"match": a["match"], "expected": a["expected"],
                          "orphan": a["orphan"], "engine_kind": a["engine_kind"]},
            })
    changes.sort(key=lambda c: _ORDER.get(c["cls"], 9))
    return {
        "changes": changes,
        "counts": counts,
        "n_verifiable_ok_before": sum(1 for r in before if r["category"] == "verifiable" and not r["orphan"] and r["match"]),
        "n_verifiable_ok_after": sum(1 for r in after if r["category"] == "verifiable" and not r["orphan"] and r["match"]),
        "n_verifiable_before": sum(1 for r in before if r["category"] == "verifiable"),
        "n_verifiable_after": sum(1 for r in after if r["category"] == "verifiable"),
    }

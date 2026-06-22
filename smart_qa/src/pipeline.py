"""编排核心：意图 -> 规划 -> 确定性执行 -> 溯源 -> 校验 -> 格式化。

规划层在此应用全部业务规则（YTD、近三年、CAGR 期初、分类归并）。
取数经 backend 抽象（memory / sqlite 可切换），运算在 engine.py（不改）。
"""
from __future__ import annotations
import engine as E
import semantic_layer as S
import backend as B


def _colkey(token) -> str:
    """时间 token -> 列键（与 backend._colkey 同语义，本模块仍需用）。"""
    if token[0] == "year":
        return f"{token[1]}年"
    if token[0] == "ytd_month":
        return f"{token[1]}-{token[2]:02d}"   # 已是当年累计，直接取该月列
    raise ValueError(f"无法解析时间 token: {token}")


def execute(grid, intent, backend: str = "memory", db_path: str | None = None):
    """意图 -> 取数后端 -> 确定性执行。

    backend: "memory"(默认) | "sqlite"。"both" 由 qa.ask 层分两次调用处理。
    返回 {kind: single|multi|fail, result/items, rules}。
    """
    be = B.make_backend(grid, backend, db_path)
    op = intent.operation

    if op == "cagr":
        return _exec_cagr(be, intent)
    if op == "multi":
        return _exec_multi(be, intent)
    if op == "sum":
        # 区分：分类节点(风电) 走项目聚合；否则按时间求和(累计分红)
        if intent.metric and S.is_taxonomy_node(intent.metric):
            return _exec_taxonomy_sum(be, intent)
        return _exec_cumulative_sum(be, intent)
    if op == "yoy":
        return _exec_yoy(be, intent)
    if op == "share":
        return _exec_share(be, intent)
    if op == "peak_year":
        return _exec_peak_year(be, intent)
    if op == "rank":
        return _exec_rank(be, intent)
    return _exec_lookup(be, intent)          # lookup


def _exec_lookup(be, intent):
    metric = intent.metric
    unit = S.metric_unit(metric)
    ck = _colkey(intent.time_tokens[0])
    pair = be.lookup(metric, intent.entity, ck)
    if not pair:
        return {"kind": "fail", "msg": f"未定位到 {metric}@{ck}"}
    cv, label = pair
    res = E.lookup(cv, label, unit)
    res.rules = [r for r in intent.notes] or ["单点取数"]
    return {"kind": "single", "result": res}


def _exec_cumulative_sum(be, intent):
    """跨时间累计求和（如 24-26年2月累计分红，含 YTD 规则）。"""
    metric = intent.metric
    unit = S.metric_unit(metric)
    cells = be.cumulative_cells(metric, intent.time_tokens)
    res = E.sum_cells(cells, unit)
    res.rules = ["monthly_ytd: 月度列取最新月(当年累计),不逐月相加"] + intent.notes
    return {"kind": "single", "result": res}


def _exec_taxonomy_sum(be, intent):
    """分类聚合（如风电=陆上风电+海上风电，跨项目行求和）。"""
    metric = intent.metric
    ck = _colkey(intent.time_tokens[0])
    unit = S.metric_unit(metric)
    cells = be.taxonomy_cells(metric, ck)
    subs = S.expand_taxonomy(metric)
    res = E.sum_cells(cells, unit)
    res.rules = [f"taxonomy: {metric} = {'+'.join(subs)}(按发电方式筛选明细项目,排除小计行)"]
    return {"kind": "single", "result": res}


def _exec_cagr(be, intent):
    metric = intent.metric                      # 利润增长率
    base = S.metric_info(metric).get("base_metric", "利润总额")
    n = intent.time_tokens[0][1]                # recent n
    _WN = {1: "近一年", 3: "近三年", 5: "近五年"}   # YAML 键用中文数字
    wkey = _WN.get(n, f"近{n}年")
    win = S.RULES["recent_years"]["windows"][wkey]
    n_initial = S.RULES["recent_years"]["cagr_initial_rule"][wkey]
    pair = be.cagr_cells(base, n_initial["initial_year"], n_initial["end_year"])
    if not pair:
        return {"kind": "fail", "msg": f"CAGR 取数失败: {base}"}
    init_cv, end_cv = pair
    res = E.cagr(float(init_cv.value), float(end_cv.value), n_initial["n"],
                 init_cv, end_cv,
                 f"{base}·{n_initial['initial_year']}年(期初)",
                 f"{base}·{n_initial['end_year']}年(期末)")
    res.rules.append("近三年窗口=" + str(win) + "; 期初取" + str(n_initial['initial_year']) + "年底(含首年增长)")
    res.rules.append("利润默认口径: " + base)
    return {"kind": "single", "result": res}


def _exec_multi(be, intent):
    """多指标(跨表) 或 多年枚举。"""
    items = []
    if len(intent.metrics) > 1:                 # 多指标(用例5)
        ck = _colkey(intent.time_tokens[0]) if intent.time_tokens else None
        for m in intent.metrics:
            unit = S.metric_unit(m)
            pair = be.lookup(m, intent.entity, ck) if ck else None
            if pair:
                cv, _ = pair
                items.append({"metric": m, "label": f"{m}·{ck}", "value": float(cv.value),
                              "addr": cv.addr, "unit": unit})
            else:
                items.append({"metric": m, "label": f"{m}·{ck}", "value": None,
                              "addr": "—", "unit": unit})
        return {"kind": "multi", "items": items, "rules": ["多表取数,各单位保留不混算"]}
    # 多年枚举(用例2)
    metric = intent.metric
    unit = S.metric_unit(metric)
    for tk in intent.time_tokens:
        ck = _colkey(tk)
        pair = be.lookup(metric, intent.entity, ck)
        if pair:
            cv, _ = pair
            items.append({"metric": metric, "label": f"{metric}·{ck}", "value": float(cv.value),
                          "addr": cv.addr, "unit": unit})
        else:
            items.append({"metric": metric, "label": f"{metric}·{ck}", "value": None,
                          "addr": "—", "unit": unit})
    return {"kind": "multi", "items": items, "rules": [f"非连续年份取数:{metric}"]}


def _exec_yoy(be, intent):
    """同比:(本期 - 上期) / 上期。time_tokens[0] = ('year', Y)。"""
    metric = intent.metric
    if not intent.time_tokens:
        return {"kind": "fail", "msg": "yoy 缺年份"}
    y = intent.time_tokens[0][1]
    cur = be.lookup(metric, intent.entity, f"{y}年")
    prev = be.lookup(metric, intent.entity, f"{y - 1}年")
    if not cur or not prev:
        return {"kind": "fail", "msg": f"yoy 取数失败: {metric}@{y}或{y - 1}年"}
    cur_cv, cur_lbl = cur
    prev_cv, prev_lbl = prev
    if float(prev_cv.value) == 0:
        return {"kind": "fail", "msg": f"yoy 上期为0: {metric}@{y - 1}年"}
    res = E.yoy(float(cur_cv.value), float(prev_cv.value),
                cur_cv, prev_cv, cur_lbl, prev_lbl)
    return {"kind": "single", "result": res}


# share 的默认总体(口径与 question_generator._o_share 一致)
_SHARE_DEFAULT_TOTAL = "发电量"


def _exec_share(be, intent):
    """占比:部分 ÷ 总体(默认 发电量合计)。part/total 两 lookup;total=0→fail。"""
    part_metric = intent.metric
    total_metric = _SHARE_DEFAULT_TOTAL
    if not intent.time_tokens:
        return {"kind": "fail", "msg": "share 缺年份"}
    ck = _colkey(intent.time_tokens[0])
    part = be.lookup(part_metric, intent.entity, ck)
    total = be.lookup(total_metric, intent.entity, ck)
    if not part or not total:
        return {"kind": "fail", "msg": f"share 取数失败: {part_metric}/{total_metric}@{ck}"}
    part_cv, part_lbl = part
    total_cv, total_lbl = total
    if float(total_cv.value) == 0:
        return {"kind": "fail", "msg": f"share 总体为0: {total_metric}@{ck}"}
    res = E.share(float(part_cv.value), float(total_cv.value),
                  part_cv, total_cv, part_lbl, total_lbl)
    res.rules.append(f"share: {part_metric} ÷ {total_metric}")
    return {"kind": "single", "result": res}


def _exec_peak_year(be, intent):
    """峰值年:某指标全年份 argmax(strict >,首年赢并列)。

    按年份升序遍历(与 oracle _o_peak_year 的 _years_from_row 排序一致),
    保证并列时取最早年,oracle↔引擎对齐。
    """
    metric = intent.metric
    cells = be.year_cells(metric, intent.entity)
    if not cells:
        return {"kind": "fail", "msg": f"peak_year 取数失败: {metric}"}
    best_year, best_cv = None, None
    for y, cv in sorted(cells, key=lambda yc: yc[0]):
        if best_cv is None or float(cv.value) > float(best_cv.value):
            best_year, best_cv = y, cv
    if best_year is None:
        return {"kind": "fail", "msg": f"peak_year 无数值: {metric}"}
    res = E.peak_year(best_year, best_cv, f"{metric}·{best_year}年")
    return {"kind": "single", "result": res}


def _exec_rank(be, intent):
    """排名:发电量各区域按值降序(镜像 oracle _o_rank)。

    排名对象 = S.region_metrics();跳过缺值(不入 items,与 oracle 一致);
    有效 <2 → fail;无年份→取'发电量'最新有值年(确定性默认)。
    """
    regions = S.region_metrics()
    if len(regions) < 2:
        return {"kind": "fail", "msg": "rank 可排名对象不足(<2)"}
    if intent.time_tokens:
        ck = _colkey(intent.time_tokens[0])
    else:
        ycs = be.year_cells(intent.metric or _SHARE_DEFAULT_TOTAL, intent.entity)
        if not ycs:
            return {"kind": "fail", "msg": "rank 无法确定年份"}
        ck = f"{ycs[-1][0]}年"                    # 最新有值年
    unit = S.metric_unit(intent.metric) if intent.metric else ""
    items = []
    for r in regions:
        pair = be.lookup(r, intent.entity, ck)
        if not pair:
            continue                              # 镜像 oracle:缺值不入 items
        cv, _ = pair
        items.append({"metric": r, "label": f"{r}·{ck}",
                      "value": float(cv.value), "addr": cv.addr, "unit": unit})
    if len(items) < 2:
        return {"kind": "fail", "msg": f"rank 有效值不足(<2)@{ck}"}
    items.sort(key=lambda it: it["value"], reverse=True)
    return {"kind": "multi", "items": items,
            "rules": [f"排名:{'+'.join(it['metric'] for it in items)} 按值降序@{ck}"]}

"""LLM 生成问题(接地算法):从 Grid + metrics.yaml 枚举真实查询目标,
LLM 只负责造句,答案由确定性 qa.ask 算出,再与从 Grid 直接算出的 expected 比对。

设计:LLM 造句的多样性 + Grid 答案的权威性 —— 守"接地或拒绝"防幻觉哲学。
LLM 在此环节连一个数字都不准碰。

10 个题型(全部可验证 —— Phase 8.3 起原 4 盲区 op 已补齐进生产引擎):
    lookup        单年单指标取数
    cumulative    连续年份区间累计/合计
    taxonomy      按发电方式分类汇总(风电=陆上+海上)
    cagr          复合增长率(近 N 年)
    multi_year    同指标多年分别取数
    multi_metric  同年多指标分别取数
    peak_year     某指标峰值出现在哪一年(argmax)
    share         部分/总体占比(默认 ÷ 发电量合计)
    yoy           同比增长(与上年比)
    rank          多区域按值排名(降序)

expected 永远直接从 Grid 单元格算出,**绝不调 qa.ask、不经 LLM**。
qa.ask 只作为"被测对象":它的输出能否复现 expected,就是本模块的全部价值。
"""
from __future__ import annotations
from dataclasses import dataclass, field

import semantic_layer as S


# ============================================================ 数据结构
@dataclass
class QuestionTarget:
    entity: str
    metric: str
    time_tokens: list                                  # [("year",y),...](cumulative/multi_year 多个)
    operation: str                                     # 10 题型之一
    category: str = "verifiable"                       # "verifiable" | "blindspot"
    expected: dict = field(default_factory=dict)       # {kind,value,op}
    expected_addr: str = ""
    metrics: list = field(default_factory=list)        # multi_metric/rank 用
    extras: dict = field(default_factory=dict)         # share 的 total、yoy 的 prev 等


# 题型元数据(Phase 8.3:原 4 盲区 op 已补齐进生产引擎,全部可验证)
VERIFIABLE = ("lookup", "cumulative", "taxonomy", "cagr", "multi_year", "multi_metric",
              "peak_year", "share", "yoy", "rank")
BLINDSPOT = ()   # 引擎已能答;保留符号前向兼容(未来新增未接 op 时再填)
ALL_TYPES = VERIFIABLE + BLINDSPOT
_CATEGORY = {t: "verifiable" for t in VERIFIABLE}
_CATEGORY.update({t: "blindspot" for t in BLINDSPOT})

# 采样轮转顺序:可验证/盲区交错,保证一批里两类都有
_TYPE_ORDER = ("lookup", "peak_year", "cumulative", "share",
               "taxonomy", "yoy", "cagr", "rank",
               "multi_year", "multi_metric")

# cagr 的近 N 年中文(与 rules.yaml 键一致)
_CN_YEAR = {1: "近一年", 3: "近三年", 5: "近五年"}

# 单指标在一批里最多出现几次(防止单指标霸屏)
_MAX_PER_METRIC = 3

# 造句意图提示(喂给 LLM,让其理解问法——但不强制关键词)
_OP_HINT = {
    "lookup": "单年单指标的取数问题(问'是多少')",
    "cumulative": "一个连续年份区间的累计/合计求和问题",
    "taxonomy": "按发电方式分类的汇总问题(如'风电发电量'已含陆上+海上)",
    "cagr": "一段时间跨度的复合增长率(CAGR)问题",
    "multi_year": "同一指标、多个年份分别取数的问题",
    "multi_metric": "同一年份、多个指标分别取数的问题",
    "peak_year": "求某指标最高/峰值出现在哪一年的问题",
    "share": "求某部分占总体的比重/占比问题",
    "yoy": "求同比(与上一年相比)增长的问题",
    "rank": "求多个对象按数值从高到低排名的问题",
}


# ============================================================ Grid 行定位(纯函数,与现有 enumerate 同源)
def _years_from_row(row: dict) -> list[int]:
    """从 {col_key: Cell} 提取年份列表(col_key 形如 '2018年')。"""
    ys: list[int] = []
    for k in row or {}:
        if isinstance(k, str) and k.endswith("年"):
            try:
                ys.append(int(k[:-1]))
            except ValueError:
                pass
    return sorted(ys)


def _row_for(grid, metric: str):
    """按 metric 的 locator 取行字典 {col_key: Cell}(fin/cap/gen_subtotals)。

    解析下放到 Grid.resolve_locator(loader 侧接缝,统一 _match_row_struct + subtotal 键)。
    """
    loc = (S.metric_info(metric) or {}).get("locator") or {}
    if not loc.get("row"):
        return None
    return grid.resolve_locator(loc)


def _cell_value(row, year: int):
    """行里某年的数值 Cell(非数值/缺失返回 None)。"""
    if not row:
        return None
    c = row.get(f"{year}年")
    if c and getattr(c, "numeric", False):
        return c
    return None


# ============================================================ oracle:每类从 Grid 算 expected(不调 qa/不调 LLM)
def _o_lookup(grid, metric, year, entity):
    c = _cell_value(_row_for(grid, metric), year)
    if not c:
        return None
    return {"kind": "single", "value": float(c.value), "op": "lookup",
            "addr": c.addr}


def _o_cumulative(grid, metric, years, entity):
    row = _row_for(grid, metric)
    vals = []
    for y in years:
        c = _cell_value(row, y)
        if not c:
            return None                      # 区间内任一年缺值→放弃(保 oracle==引擎同批单元格)
        vals.append(float(c.value))
    return {"kind": "single", "value": sum(vals), "op": "cumulative",
            "addr": ""}


def _o_taxonomy(grid, metric, year, entity):
    subs = S.expand_taxonomy(metric)
    if not subs:
        return None
    ck = f"{year}年"
    total = 0.0
    hit = False
    for p in grid.gen_projects:
        if p.get("方式") in subs:
            c = p.get("values", {}).get(ck)
            if c and getattr(c, "numeric", False):   # 与 engine.sum_cells 同:跳过非数值
                total += float(c.value)
                hit = True
    if not hit:
        return None
    return {"kind": "single", "value": total, "op": "taxonomy", "addr": ""}


def _o_cagr(grid, base_metric, recent_n, entity):
    """(期末/期初)^(1/n) - 1,期初/期末/n 全读 rules.cagr_initial_rule。"""
    try:
        wkey = _CN_YEAR[recent_n]
        rule = S.RULES["recent_years"]["cagr_initial_rule"][wkey]
    except (KeyError, TypeError):
        return None
    row = _row_for(grid, base_metric)
    init = _cell_value(row, rule["initial_year"])
    end = _cell_value(row, rule["end_year"])
    if not init or not end or float(init.value) == 0:
        return None
    ratio = (float(end.value) / float(init.value)) ** (1.0 / rule["n"]) - 1.0
    return {"kind": "single", "value": ratio, "op": "cagr", "addr": ""}


def _o_multi_year(grid, metric, years, entity):
    row = _row_for(grid, metric)
    vals = []
    for y in years:
        c = _cell_value(row, y)
        if not c:
            return None
        vals.append(float(c.value))
    return {"kind": "multi", "value": vals, "op": "multi_year", "addr": ""}


def _o_multi_metric(grid, metrics, year, entity):
    vals = []
    for m in metrics:
        c = _cell_value(_row_for(grid, m), year)
        if not c:
            return None
        vals.append(float(c.value))
    return {"kind": "multi", "value": vals, "op": "multi_metric", "addr": ""}


def _o_peak_year(grid, metric, entity):
    row = _row_for(grid, metric)
    best_year, best_val = None, None
    for y in _years_from_row(row):
        c = _cell_value(row, y)
        if not c:
            continue
        if best_val is None or float(c.value) > best_val:
            best_year, best_val = y, float(c.value)
    if best_year is None:
        return None
    return {"kind": "single", "value": best_year, "op": "peak_year",
            "addr": "", "peak_value": best_val}


def _o_share(grid, region_metric, total_metric, year, entity):
    r = _cell_value(_row_for(grid, region_metric), year)
    t = _cell_value(_row_for(grid, total_metric), year)
    if not r or not t or float(t.value) == 0:
        return None
    return {"kind": "single", "value": float(r.value) / float(t.value),
            "op": "share", "addr": ""}


def _o_yoy(grid, metric, year, entity):
    cur = _cell_value(_row_for(grid, metric), year)
    prev = _cell_value(_row_for(grid, metric), year - 1)
    if not cur or not prev or float(prev.value) == 0:
        return None
    return {"kind": "single", "value": (float(cur.value) - float(prev.value)) / float(prev.value),
            "op": "yoy", "addr": ""}


def _o_rank(grid, region_metrics, year, entity):
    pairs = []
    for m in region_metrics:
        c = _cell_value(_row_for(grid, m), year)
        if c:
            pairs.append((m, float(c.value)))
    if len(pairs) < 2:
        return None
    pairs.sort(key=lambda kv: kv[1], reverse=True)
    return {"kind": "multi", "value": [v for _, v in pairs], "op": "rank",
            "addr": "", "order": [m for m, _ in pairs]}


def re_derive(target: "QuestionTarget", grid) -> dict | None:
    """用【当前 grid】按 target.operation 重算 expected(永不读落盘数字)。

    语料库闭环的核心:expected 永远现场重算,绝不落盘。metric 被删/改名、
    单元格缺失 → 返回 None(上层标记孤儿/失效,不抛)。零新逻辑,纯分发到 _o_*。
    """
    op = target.operation
    ys = [t[1] for t in (target.time_tokens or [])]
    y = ys[0] if ys else None
    ent = target.entity
    if op == "lookup":
        return _o_lookup(grid, target.metric, y, ent) if y is not None else None
    if op == "cumulative":
        return _o_cumulative(grid, target.metric, ys, ent) if ys else None
    if op == "taxonomy":
        return _o_taxonomy(grid, target.metric, y, ent) if y is not None else None
    if op == "cagr":
        n = (target.extras or {}).get("recent_n")
        if n is None and ys:                       # 回退:time_tokens [("recent",n)]
            n = ys[0][1] if isinstance(ys[0], tuple) else ys[0]
        return _o_cagr(grid, target.metric, n or 3, ent)
    if op == "multi_year":
        return _o_multi_year(grid, target.metric, ys, ent) if ys else None
    if op == "multi_metric":
        return _o_multi_metric(grid, target.metrics, y, ent) if (target.metrics and y is not None) else None
    if op == "peak_year":
        return _o_peak_year(grid, target.metric, ent)
    if op == "share":
        tot = (target.extras or {}).get("total_metric", "发电量")
        return _o_share(grid, target.metric, tot, y, ent) if y is not None else None
    if op == "yoy":
        return _o_yoy(grid, target.metric, y, ent) if y is not None else None
    if op == "rank":
        return _o_rank(grid, target.metrics, y, ent) if (target.metrics and y is not None) else None
    return None


# ============================================================ 候选池(每类一份,从 Grid+S.METRICS 推)
def _row_metrics(grid, sheets=("财务数据", "装机")):
    """有单点行 locator 的 fin/cap 指标(按字典序)。"""
    out = []
    for m, info in S.METRICS.items():
        loc = (info or {}).get("locator") or {}
        if loc.get("sheet") in sheets and loc.get("row"):
            out.append(m)
    return out


def _region_metrics():
    """发电量区域小计指标(巴西/南亚/欧洲/拉美发电量)。"""
    out = []
    for m, info in S.METRICS.items():
        info = info or {}
        if info.get("is_subtotal") and (info.get("locator") or {}).get("sheet") == "发电量":
            out.append(m)
    return out


def _taxonomy_metrics():
    out = []
    for m, info in S.METRICS.items():
        if (info or {}).get("taxonomy_node"):
            out.append(m)
    return out


def _pick(years, i):
    """确定性挑一个年份(按指标序号轮转,避免全用同一年)。"""
    if not years:
        return None
    return years[i % len(years)]


def _build_pools(grid):
    """构造 {op: [QuestionTarget,...]}。oracle 算不出的候选不入池。"""
    pools: dict[str, list[QuestionTarget]] = {t: [] for t in ALL_TYPES}
    row_metrics = _row_metrics(grid)
    regions = _region_metrics()
    tax_metrics = _taxonomy_metrics()

    def _push(op, target):
        if target is not None and target.expected:
            pools[op].append(target)

    # ---- lookup:每指标 1 条,年份轮转 ----
    for i, m in enumerate(row_metrics):
        years = _years_from_row(_row_for(grid, m))
        y = _pick(years, i)
        if y is None:
            continue
        e = _o_lookup(grid, m, y, "三峡国际")
        if e:
            _push("lookup", QuestionTarget(
                entity="三峡国际", metric=m, time_tokens=[("year", y)],
                operation="lookup", category="verifiable",
                expected=e, expected_addr=e.get("addr", "")))

    # ---- cumulative:每指标 1 条,取连续 3 年窗口 ----
    for i, m in enumerate(row_metrics):
        years = _years_from_row(_row_for(grid, m))
        if len(years) < 3:
            continue
        start = _pick(years[:-2], i)                 # 窗口起点轮转,保 3 年连续
        si = years.index(start)
        window = years[si:si + 3]
        if len(window) < 3:
            continue
        e = _o_cumulative(grid, m, window, "三峡国际")
        if e:
            _push("cumulative", QuestionTarget(
                entity="三峡国际", metric=m,
                time_tokens=[("year", y) for y in window],
                operation="cumulative", category="verifiable", expected=e))

    # ---- taxonomy:每个分类指标 1 条 ----
    all_years = _years_from_row(_row_for(grid, "发电量")) if regions else []
    for i, m in enumerate(tax_metrics):
        y = _pick(all_years, i)
        if y is None:
            continue
        e = _o_taxonomy(grid, m, y, "三峡国际")
        if e:
            _push("taxonomy", QuestionTarget(
                entity="三峡国际", metric=m, time_tokens=[("year", y)],
                operation="taxonomy", category="verifiable", expected=e))

    # ---- cagr:利润总额(及任何 derived base)× 近一/三/五年 ----
    bases = []
    for m, info in S.METRICS.items():
        info = info or {}
        if info.get("derived") and info.get("base_metric"):
            bases.append(info["base_metric"])
    if not bases:
        bases = [m for m in ("利润总额",) if m in S.METRICS]
    for n in (3, 1, 5):
        for b in bases:
            e = _o_cagr(grid, b, n, "三峡国际")
            if e:
                _push("cagr", QuestionTarget(
                    entity="三峡国际", metric=b, time_tokens=[("recent", n)],
                    operation="cagr", category="verifiable", expected=e,
                    extras={"recent_n": n}))

    # ---- multi_year:每指标 1 条,取 2 个轮转年份 ----
    for i, m in enumerate(row_metrics):
        years = _years_from_row(_row_for(grid, m))
        if len(years) < 2:
            continue
        a = _pick(years, i)
        b = _pick(years, i + 1) if len(years) > 1 else years[0]
        ys = sorted({a, b})
        if len(ys) < 2:
            continue
        e = _o_multi_year(grid, m, ys, "三峡国际")
        if e:
            _push("multi_year", QuestionTarget(
                entity="三峡国际", metric=m,
                time_tokens=[("year", y) for y in ys],
                operation="multi_year", category="verifiable", expected=e))

    # ---- multi_metric:fin 指标两两组队,同年 ----
    fin = [m for m in row_metrics
           if (S.metric_info(m) or {}).get("locator", {}).get("sheet") == "财务数据"
           and _years_from_row(_row_for(grid, m))]
    for i in range(0, len(fin) - 1, 2):
        m1, m2 = fin[i], fin[i + 1]
        y1 = _years_from_row(_row_for(grid, m1))
        y2 = _years_from_row(_row_for(grid, m2))
        common = sorted(set(y1) & set(y2))
        y = _pick(common, 0)
        if y is None:
            continue
        e = _o_multi_metric(grid, [m1, m2], y, "三峡国际")
        if e:
            _push("multi_metric", QuestionTarget(
                entity="三峡国际", metric=m1, metrics=[m1, m2],
                time_tokens=[("year", y)],
                operation="multi_metric", category="verifiable", expected=e))

    # ---- peak_year:每指标 1 条 ----
    for m in row_metrics:
        e = _o_peak_year(grid, m, "三峡国际")
        if e:
            _push("peak_year", QuestionTarget(
                entity="三峡国际", metric=m, time_tokens=[],
                operation="peak_year", category="verifiable", expected=e,
                extras={"peak_value": e.get("peak_value")}))

    # ---- share:每个区域 1 条 ----
    for i, r in enumerate(regions):
        y = _pick(all_years, i)
        if y is None:
            continue
        e = _o_share(grid, r, "发电量", y, "三峡国际")
        if e:
            _push("share", QuestionTarget(
                entity="三峡国际", metric=r, time_tokens=[("year", y)],
                operation="share", category="verifiable", expected=e,
                extras={"total_metric": "发电量"}))

    # ---- yoy:每指标 1 条(取有前一年的年) ----
    for i, m in enumerate(row_metrics):
        years = _years_from_row(_row_for(grid, m))
        y = None
        for yy in reversed(years):
            if (yy - 1) in years:
                y = yy
                break
        if y is None:
            continue
        e = _o_yoy(grid, m, y, "三峡国际")
        if e:
            _push("yoy", QuestionTarget(
                entity="三峡国际", metric=m, time_tokens=[("year", y)],
                operation="yoy", category="verifiable", expected=e))

    # ---- rank:所有区域,1 条 ----
    if len(regions) >= 2:
        y = _pick(all_years, 0)
        if y is not None:
            e = _o_rank(grid, regions, y, "三峡国际")
            if e:
                _push("rank", QuestionTarget(
                    entity="三峡国际", metric="各区域发电量", metrics=list(regions),
                    time_tokens=[("year", y)],
                    operation="rank", category="verifiable", expected=e,
                    extras={"order": e.get("order", [])}))

    return pools


def enumerate_targets(grid, max_n: int = 20,
                      categories=None, types=None) -> list[QuestionTarget]:
    """从 Grid + metrics.yaml 多样性枚举真实查询目标。

    轮转跨题型、跨指标,单指标上限 _MAX_PER_METRIC,确保一批里题型/指标都多样
    (确定性顺序,测试稳定)。categories={"verifiable"|"blindspot"}、types=[op,...]
    可过滤(页面 multiselect 用);默认全 10 类。
    """
    pools = _build_pools(grid)
    type_order = [t for t in _TYPE_ORDER if t in pools and pools[t]]
    if types is not None:
        type_order = [t for t in type_order if t in set(types)]
    if categories is not None:
        wanted = set(categories)
        type_order = [t for t in type_order if _CATEGORY[t] in wanted]

    selected: list[QuestionTarget] = []
    metric_count: dict[str, int] = {}
    idx = {t: 0 for t in type_order}

    while len(selected) < max_n:
        progressed = False
        for t in type_order:
            if len(selected) >= max_n:
                break
            pool = pools[t]
            while idx[t] < len(pool):
                cand = pool[idx[t]]
                idx[t] += 1
                pm = _primary_metric(cand)
                if metric_count.get(pm, 0) >= _MAX_PER_METRIC:
                    continue
                metric_count[pm] = metric_count.get(pm, 0) + 1
                selected.append(cand)
                progressed = True
                break
        if not progressed:
            break                      # 所有池取尽
    return selected


def _primary_metric(target: QuestionTarget) -> str:
    """采样去重键:multi_metric 用首指标,rank 用固定合成键,其余用 metric。"""
    if target.operation == "rank":
        return "__rank__"
    return target.metrics[0] if target.metrics else target.metric


# ============================================================ 造句
def _entity_zh(entity: str) -> str:
    return "公司" if entity == "三峡国际" else entity


def _time_phrase(target: QuestionTarget) -> str:
    op = target.operation
    if op == "cumulative":
        ys = [t[1] for t in target.time_tokens]
        return f"{ys[0]}到{ys[-1]}年" if ys else ""
    if op == "multi_year":
        ys = [t[1] for t in target.time_tokens]
        return "、".join(f"{y}年" for y in ys)
    if op == "cagr":
        n = target.extras.get("recent_n", target.time_tokens[0][1] if target.time_tokens else 3)
        return _CN_YEAR.get(n, f"近{n}年")
    if op == "peak_year":
        return "（不限年份，问哪一年最高）"
    y = target.time_tokens[0][1] if target.time_tokens else "?"
    return f"{y}年"


def _metric_phrase(target: QuestionTarget) -> str:
    op = target.operation
    if op == "multi_metric":
        return "、".join(target.metrics)
    if op == "share":
        return f"{target.metric} 占 总发电量"
    if op == "rank":
        return "各区域发电量（巴西/南亚/欧洲/拉美）"
    return target.metric


def build_question(target: QuestionTarget, llm) -> str | None:
    """LLM 造句:把查询参数转成一句自然中文问题(纯 LLM,不限关键词)。

    只喂参数(实体/指标/时间/问法意图),不喂数值,不强制关键词——
    让 LLM 自由改写,用于压力测试 parser 对自然表达的鲁棒性。
    LLM 不可用/异常返回 None(上层用 fallback)。
    """
    if not llm.available:
        return None
    system = (
        "你是问题生成器。把给定的查询参数改写成一句像真实业务人员会问的自然中文问数问题。"
        "规则:只输出一句话问题;不要给答案;不要给任何数值;不要加多余解释;不要用引号。"
    )
    user = (
        f"实体:{_entity_zh(target.entity)}\n"
        f"指标:{_metric_phrase(target)}\n"
        f"时间:{_time_phrase(target)}\n"
        f"问法意图:{_OP_HINT.get(target.operation, target.operation)}\n"
        f"请生成一句自然的中文问数问题。"
    )
    try:
        return llm.chat(system, user, json_mode=False).strip().strip('"').strip("'").strip()
    except Exception:
        return None


def fallback_question(target: QuestionTarget) -> str:
    """LLM 不可用时的模板造句(每类固定句式,关键运算词齐全)。"""
    op = target.operation
    ent = _entity_zh(target.entity)
    y = target.time_tokens[0][1] if target.time_tokens else ""
    if op == "lookup":
        return f"{ent}{y}年的{target.metric}是多少？"
    if op == "cumulative":
        ys = [t[1] for t in target.time_tokens]
        return f"{ent}{ys[0]}到{ys[-1]}年的{target.metric}累计是多少？"
    if op == "taxonomy":
        return f"{ent}{y}年的{target.metric}是多少？"
    if op == "cagr":
        return f"{ent}{_time_phrase(target)}的{target.metric}增长率是多少？"
    if op == "multi_year":
        ys = [t[1] for t in target.time_tokens]
        return f"{ent}{'、'.join(f'{a}年' for a in ys)}的{target.metric}分别是多少？"
    if op == "multi_metric":
        return f"{ent}{y}年的{'和'.join(target.metrics)}分别是多少？"
    if op == "peak_year":
        return f"{ent}哪一年的{target.metric}最高？"
    if op == "share":
        return f"{y}年{target.metric}占总发电量的比重是多少？"
    if op == "yoy":
        return f"{ent}{y}年的{target.metric}同比增长了百分之几？"
    if op == "rank":
        return f"{y}年各区域的发电量从高到低分别是多少？"
    return f"{ent}{y}年的{target.metric}是多少？"


# ============================================================ 验证(qa.ask 作被测对象)
def verify_question(question: str, grid, expected: dict) -> dict:
    """用确定性 qa.ask 算答案,与 expected(从 Grid 直接算出)比对。

    用 try/except 包 qa.ask:盲区类/丢词问句会让 parser 误路由,可能触发
    引擎异常(KeyError/IndexError,属受保护后端的不优雅处,不改)→
    在工作台层捕获,转成干净的 ✗ 诊断,不让整页生成崩溃。
    """
    import qa
    try:
        ans = qa.ask(grid, question, use_llm=False, backend="memory")
        engine_kind = ans.get("kind", "fail")
        refused = engine_kind == "fail"
    except Exception as e:
        ans = {"kind": "fail", "msg": f"解析异常:{type(e).__name__}: {e}",
               "text": "", "verified": False}
        engine_kind = "fail"
        refused = True

    actual = None
    addr = ""
    if engine_kind == "single":
        res = ans.get("result")
        actual = res.value if res else None
        if res and res.operands:
            addr = res.operands[0].addr
    elif engine_kind == "multi":
        items = ans.get("items", [])
        actual = [it.get("value") for it in items]
        if items:
            addr = items[0].get("addr", "")

    exp_val = expected.get("value")
    op = expected.get("op", "lookup")
    tol = 0.001 if op in ("cagr", "yoy", "share") else 0.02
    if refused:
        match = False
    elif exp_val is None:
        match = engine_kind == "fail"
    else:
        match = _close(actual, exp_val, tol)
    return {
        "question": question,
        "expected": exp_val,
        "actual": actual,
        "match": match,
        "kind": engine_kind,                 # 引擎 kind(向后兼容现有测试)
        "engine_kind": engine_kind,
        "refused": refused,
        "verified": ans.get("verified", False),
        "addr": addr,
        "text": ans.get("text", ""),
        "op": op,
    }


def _close(a, b, tol: float = 0.02) -> bool:
    if a is None or b is None:
        return a == b
    if isinstance(a, list) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b

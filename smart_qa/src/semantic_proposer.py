"""LLM 语义层自动提议(离线,可选依赖)——镜像 schema_proposer 的范式。

把"人手编 4 个 semantic YAML"变成"LLM 起草 + 闸门复核 + 人定稿",拆掉 onboarding 成本墙。

边界(与 schema_proposer 同源,守"LLM 不碰数字"):
- LLM 只看 Grid 的【标签清单】(行标签、列键、项目 方式/区域),**全程不读 Cell.value**。
  标签是元数据(行列名),不是数值;发 LLM 不违反"LLM 不碰数字"。
- rules.yaml 用模板从【数据最大年份】确定性生成(引擎消费的业务约定,不交给 LLM);
  LLM 只生成 metrics/taxonomy/synonyms(单次调用,三件互相引用保一致)。
- 输出过 lint + resolve_locator 闸门(每条 metric 的 locator 须真能落到真实单元格);
  失败 1 轮自动修复(把错误喂回 LLM,镜像 schema_proposer)。
- 无 LLM 配置时不崩溃,打印引导返回 None。
- committed seed(三峡)只读作 few-shot 格式范本;提议器只在显式调用时跑,不自动覆盖。

CLI 用法:
    python -m src.semantic_proposer   # 在 smart_qa/ 下跑(读 seed grid+schema)
"""
from __future__ import annotations
import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import llm_client
import semantic_edit_helpers as SE

# committed seed 路径(只读 few-shot / 自检)
_HERE = os.path.dirname(os.path.abspath(__file__))
_SEED_SEM_DIR = os.path.join(_HERE, "..", "semantic")

PROPOSER_TIMEOUT = 240   # 离线重任务(出 3 个 YAML),比 parser 宽松;放宽以容纳大表/慢本地模型
RETRY_ON_TIMEOUT = 1     # 超时(本地 LLM 响应波动)重试次数;仅对超时重试,HTTP/格式错误不重试


# ============================================================ 标签清单(LLM 输入,零 Cell.value)
def _years_from_col_keys(col_keys) -> list[int]:
    """列键里形如 '2018年' 的年份,升序。"""
    ys = []
    for ck in (col_keys or []):
        if isinstance(ck, str) and ck.endswith("年"):
            try:
                ys.append(int(ck[:-1]))
            except ValueError:
                pass
    return sorted(set(ys))


def _schema_table_lookup(schema: dict) -> dict:
    """{table_name: (sheet_name, table_dict)} —— 供按表名查 schema 配置。"""
    out = {}
    for sh in (schema or {}).get("sheets") or []:
        for tb in sh.get("tables") or []:
            out[tb.get("name")] = (sh.get("name"), tb)
    return out


def build_label_inventory(grid, schema: dict) -> dict:
    """从 loader Grid 抽【标签清单】供 LLM 推断(只读 keys/classifier,不读 Cell.value)。

    泛型化(2026-06):遍历 grid.table_index 按 target 抽取,不假设 sheet 名/字段名。
    返回:
      tables: [{name, sheet, target, row_labels?/col_keys?/subtotal_keys?/classifiers?/projects_sample?}]
      year_range: (min, max)  # 供 rules 模板
      schema_tables: [{sheet, target, name, emit_keys?, classifier_cols?}]  # 结构线索
    """
    inv: dict = {"tables": [], "year_range": (None, None), "schema_tables": []}
    all_years: list[int] = []
    schema_tbl = _schema_table_lookup(schema)

    def _col_keys(row_dict):
        return list((row_dict or {}).keys())

    for name, meta in grid.table_index.items():
        sheet = meta.get("sheet")
        target = meta.get("target")
        entry = {"name": name, "sheet": sheet, "target": target}

        if target == "row_map":
            data = grid.row_maps.get(name, {})
            entry["row_labels"] = list(data.keys())
            cols = _col_keys(next(iter(data.values()), None))
            entry["col_keys"] = cols
            all_years.extend(_years_from_col_keys(cols))

        elif target == "gen_subtotals":
            data = grid.subtotals.get(name, {})
            _sheet, tb = schema_tbl.get(name, (None, {}))
            entry["subtotal_keys"] = [r.get("emit_key") for r in (tb.get("subtotal_rules") or [])]
            cols = _col_keys(next(iter(data.values()), None))
            entry["col_keys"] = cols
            all_years.extend(_years_from_col_keys(cols))

        elif target == "gen_detail":
            projects = grid.details.get(name, [])
            _sheet, tb = schema_tbl.get(name, (None, {}))
            clf = tb.get("detail_classifier_cols") or {}   # {维度名: col_idx},动态(三峡=方式/区域)
            dims = [d for d in clf.keys() if d != "name"]
            # 每个维度的 distinct 值(供 LLM 建 taxonomy)
            entry["classifiers"] = {
                d: sorted({str(p.get(d)) for p in projects if p.get(d)}) for d in dims
            }
            sample_vals = next(iter((p.get("values") for p in projects if p.get("values"))), None)
            entry["col_keys"] = _col_keys(sample_vals)
            all_years.extend(_years_from_col_keys(entry["col_keys"]))
            entry["projects_sample"] = [
                {**{d: p.get(d) for d in dims}, "name": p.get("name")}
                for p in projects[:30]   # 限 30 控 prompt 体量;标签无数值
            ]

        inv["tables"].append(entry)

    ys = sorted(set(all_years))
    inv["year_range"] = (ys[0], ys[-1]) if ys else (None, None)

    # schema 表结构(标签/结构线索,非数值)——保留,通用
    for sh in (schema or {}).get("sheets") or []:
        for tb in sh.get("tables") or []:
            e = {"sheet": sh.get("name"), "target": tb.get("target"), "name": tb.get("name")}
            if tb.get("target") == "gen_subtotals":
                e["emit_keys"] = [r.get("emit_key") for r in (tb.get("subtotal_rules") or [])]
            if tb.get("detail_classifier_cols"):
                e["classifier_cols"] = tb.get("detail_classifier_cols")
            inv["schema_tables"].append(e)

    return inv


# ============================================================ 预览网格(供 resolve_locator 闸门)
def _label_col_for_sheet(schema: dict, sheet: str) -> int:
    """sheet 上【首表】的 label_col_idx(0-based)。

    与 SE.resolve_locator 的读取契约一致(resolve 也读 sheet 首表 label_col),
    故 preview 必须按此列铺标签,否则闸门误报。
    """
    for sh in (schema or {}).get("sheets") or []:
        if sh.get("name") == sheet:
            tbs = sh.get("tables") or []
            if tbs:
                try:
                    return int(tbs[0].get("label_col_idx", 0))
                except (TypeError, ValueError):
                    return 0
            return 0
    return 0


def _build_preview_grid(grid, schema: dict) -> dict:
    """loader Grid → {sheet: [[str]]} 供 SE.resolve_locator(只读 label_col 文本)。

    泛型化(2026-06):遍历 grid.table_index 收标签,不假设 sheet 名。
    - row_map 表:每个行标签一行。
    - gen_subtotals 表:subtotal_rules 的 match_substring + emit_key 各铺一行
      (seed 的总指标用 row=发电量合计 即 match_substring,区域小计用 emit_key;
       engine 经 _subtotal_region_key 映射,两种写法都放进供 resolve 命中)。
    所有标签按【sheet 首表 label_col】铺(对齐 SE.resolve_locator 读取契约)。
    零 Cell.value 读取(resolve 只比 label_col 文本)。
    """
    schema_tbl = _schema_table_lookup(schema)

    def _row_with_label(label, lc):
        row = [""] * max(lc + 1, 1)
        if lc < len(row):
            row[lc] = str(label)
        return row

    preview: dict[str, list[list[str]]] = {}
    for name, meta in grid.table_index.items():
        sheet = meta.get("sheet")
        target = meta.get("target")
        lc = _label_col_for_sheet(schema, sheet)   # 对齐 resolve_locator 读取列
        bucket = preview.setdefault(sheet, [])

        if target == "row_map":
            for label in grid.row_maps.get(name, {}).keys():
                bucket.append(_row_with_label(label, lc))
        elif target == "gen_subtotals":
            _sheet, tb = schema_tbl.get(name, (None, {}))
            seen = set()
            for rule in (tb.get("subtotal_rules") or []):
                for lbl in (rule.get("match_substring"), rule.get("emit_key")):
                    if lbl and lbl not in seen:
                        seen.add(lbl)
                        bucket.append(_row_with_label(lbl, lc))
    return preview


# ============================================================ rules 模板(确定性,不问 LLM)
def _template_rules(max_year: int) -> dict:
    """从数据最大年份生成 rules.yaml(引擎消费的 CAGR/YTD 业务约定)。

    口径(对齐 seed:max_year=2025):
      近N年 window = [max-N+1 .. max];cagr initial = max-N(窗口前一年底), end = max, n = N。
    """
    def _win(n):
        return list(range(max_year - n + 1, max_year + 1))

    def _cagr(n):
        return {"window": _win(n), "initial_year": max_year - n,
                "end_year": max_year, "n": n,
                "note": f"期初取窗口前一年底({max_year - n}年底)"}

    return {
        "monthly_ytd": {
            "description": "月度列取最新月(当年累计),不逐月相加",
            "resolve_rule": {"1月至N月": "取第 N 月列的值", "N月": "取第 N 月列的值",
                             "forbid": "禁止把 1月列 + 2月列 相加(会重复计算)"},
        },
        "recent_years": {
            "anchor": "查询日期",
            "current_year": (max_year + 1) if max_year else None,
            "windows": {"近一年": _win(1), "近三年": _win(3), "近五年": _win(5)},
            "cagr_initial_rule": {
                "description": "计算近N年 CAGR 时,期初取窗口前一年底值",
                "近一年": _cagr(1), "近三年": _cagr(3), "近五年": _cagr(5),
            },
        },
        # 单位/口径由各 metric 的 unit 字段自带(不在此硬编码三峡专属单位)。
        "cross_table_warning": "跨表取数时各值保留各自单位,不可混算",
        "classification": {
            "求和去重": {"rule": "求和时只用明细项目行,不混入小计行,避免重复计入"},
        },
    }


# ============================================================ 闸门(lint + resolve_locator)
def _gate(metrics: dict, taxonomy: dict, synonyms: dict, rules: dict,
          preview: dict, schema: dict) -> dict:
    """过 lint_* + 每 metric resolve_locator。返回结构化报告。

    errors  = lint error 级 + resolve 失败(喂回 LLM 修复)
    warns   = lint warn/info(提示人,不修复)
    """
    findings = []
    findings += SE.lint_metrics(metrics, taxonomy, synonyms, schema)
    findings += SE.lint_synonyms(synonyms, metrics)
    findings += SE.lint_rules(rules)

    errors = [{"where": w, "msg": m} for sev, w, m in findings if sev == "error"]
    warns = [{"severity": sev, "where": w, "msg": m}
             for sev, w, m in findings if sev in ("warn", "info")]

    resolve_fail = []
    n_ok = 0
    for name, info in (metrics or {}).items():
        info = info if isinstance(info, dict) else {}
        ok, msg = SE.resolve_locator(info, preview, schema, metrics)
        if ok:
            n_ok += 1
        else:
            resolve_fail.append({"metric": name, "msg": msg})

    return {
        "n_metrics": len(metrics or {}),
        "n_resolve_ok": n_ok,
        "resolve_fail": resolve_fail,
        "errors": errors,
        "warns": warns,
    }


# ============================================================ Prompt
SYSTEM_PROMPT = """你是企业报表的「语义层」设计助手。任务:根据给定 Excel 的【标签清单】,
起草 metrics / taxonomy / synonyms 三个 YAML(合在一个 YAML 文档里,顶层三键)。

# 严格边界
- 你只看到【标签清单】(行标签、列键、项目分类器),**看不到也禁止编造任何数值**。
- metric 名与 locator.row 必须用清单里【真实存在的行标签】,逐字一致(否则引擎取不到数)。

# ⚠️ 关键约束:下方示例仅为【YAML 格式演示】,不是这份数据的指标
- 示例里出现的指标名(利润总额、风电发电量、巴西发电量)、sheet 名(财务数据/装机/发电量)、
  分类名(发电方式/区域)、实体名(三峡国际)、单位(亿元/亿千瓦时)**全部禁止照搬到本份数据**。
- 你必须只从用户【标签清单】里真实存在的表名/行标签/分类维度值发明指标,sheet/table/row 逐字用清单里的真名。
- 若清单里没有某类结构(如无 detail 表 → 不要造 taxonomy 聚合指标;无小计键 → 不要造 is_subtotal 指标)。

# metrics.yaml 每个指标的字段(按 locator 形态选用,以下仅示范【字段结构】)
- 行指标(row_map,清单里 row_map 表的行标签):
    <指标名>:
      synonyms: [<口语别名>]
      locator: { sheet: <清单真实 sheet>, row: <清单真实行标签> }
      unit: <从标签/列推断的单位,不确定则留空>
- 分类聚合指标(taxonomy,仅当清单有 detail 表且给出分类维度时):
    <指标名>:
      parent: <父指标>
      taxonomy_node: <taxonomy 里某维度的节点>
      locator: { sheet: <detail 表所在 sheet> }
      aggregation: sum_by_<分类维度名>   # 维度名用清单给出的(示例用 方式 仅为占位)
- 小计指标(subtotal,仅当清单给出 subtotal_keys):
    <指标名>:
      locator: { sheet: <sheet>, row: <清单 subtotal_keys 之一> }
      is_subtotal: true
- 派生指标(derived,无 locator):
    <指标名>:
      derived: true
      base_metric: <已定义指标>
      operation: cagr
      unit: 百分比

# taxonomy.yaml:分类树(仅当清单有分类维度;includes 用清单里该维度的 distinct 值)
  <维度名>:
    <节点>: { includes: [<清单里的维度值>], description: <简述> }

# synonyms.yaml:实体消歧 + 口语别名 + 时间/量词别名(实体/别名都从清单推断,勿照搬示例)
  entities:
    <主体名>: { aliases: [<口语>], means: <口径> }
  metric_aliases:
    <口语>: <metrics 里的标准名>   # 标准名必须是已定义指标
  time_aliases: { 近三年: recent_3_years }
  quantity_aliases: { 累计: cumulative, 合计: total }

# 输出格式(严格,顶层三键,只输出 YAML,不要解释,不要数值)
```yaml
metrics:
  <指标名>: { ... }
taxonomy:
  <分类>: { <节点>: { includes: [...] } }
synonyms:
  entities: { ... }
  metric_aliases: { ... }
  time_aliases: { ... }
  quantity_aliases: { ... }
```
"""


def build_user_prompt(inventory: dict) -> str:
    """把标签清单序列化成 user prompt(labels-only,无数值)。table-centric,通用。"""
    parts = ["请根据以下 Excel 各表的标签清单,起草 metrics/taxonomy/synonyms 三件 YAML。",
             "只输出一个顶层含 metrics/taxonomy/synonyms 三键的 YAML 文档。不要数值。",
             "指标名与 locator.row 必须逐字使用下面给出的真实行标签/小计键;sheet/table 用下面给出的真实名。"]
    for t in inventory.get("tables") or []:
        parts.append(f"\n## 表 {t['name']!r} (sheet={t.get('sheet')}, target={t.get('target')})")
        if "row_labels" in t:
            parts.append(f"行标签(row_labels,共 {len(t['row_labels'])},作 row_map 指标的 row): "
                         + "、".join(str(x) for x in t["row_labels"][:60]))
        if "col_keys" in t:
            parts.append(f"列键示例(col_keys): {'、'.join(str(c) for c in (t.get('col_keys') or [])[:20])}")
        if "subtotal_keys" in t:
            parts.append(f"小计键(subtotal_keys,作 is_subtotal 指标的 row): "
                         + "、".join(str(x) for x in t["subtotal_keys"]))
        if t.get("classifiers"):
            for dim, vals in t["classifiers"].items():
                if vals:
                    shown = vals[:25]   # cap:项目名等维度 distinct 值可能很多,控 prompt 体量
                    more = f" ...(共 {len(vals)} 个)" if len(vals) > 25 else ""
                    parts.append(f"分类维度[{dim}](明细项目此字段的 distinct 值,建 taxonomy/includes 用): "
                                 + "、".join(shown) + more)
        if t.get("projects_sample"):
            parts.append(f"明细项目样本(前 {len(t['projects_sample'])},仅分类字段): "
                         + "、".join(str(p) for p in t["projects_sample"][:10]))
    yr = inventory.get("year_range") or (None, None)
    parts.append(f"\n数据年份范围: {yr[0]}–{yr[1]}(rules.yaml 由系统按此模板生成,你无需输出 rules)")
    return "\n".join(parts)


# ============================================================ 解析 / 修复
def _clean_yaml(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```yaml"):
        t = t[7:]
    elif t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return t.strip()


def _parse_bundle(text: str):
    """LLM 文本 → (metrics, taxonomy, synonyms) 或 None。期望顶层 {metrics, taxonomy, synonyms}。"""
    try:
        data = yaml.safe_load(_clean_yaml(text))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    metrics = data.get("metrics")
    taxonomy = data.get("taxonomy")
    synonyms = data.get("synonyms")
    if not (isinstance(metrics, dict) and isinstance(taxonomy, dict) and isinstance(synonyms, dict)):
        return None
    return metrics, taxonomy, synonyms


def _gate_issues_for_repair(gate: dict) -> list[str]:
    issues = []
    for e in gate.get("errors", []):
        issues.append(f"[lint error] {e['where']}: {e['msg']}")
    for r in gate.get("resolve_fail", []):
        issues.append(f"[locator 落不到真实单元格] 指标「{r['metric']}」: {r['msg']}")
    return issues


def _try_chat(client, system: str, user: str) -> str | None:
    """调 LLM;超时(本地服务波动)自动重试 RETRY_ON_TIMEOUT 次,其他错误立即失败。"""
    if not client.available:
        return None
    for attempt in range(1 + RETRY_ON_TIMEOUT):
        try:
            return client.chat(system, user, json_mode=False, timeout=PROPOSER_TIMEOUT)
        except llm_client.LLMUnavailable as e:
            msg = str(e)
            is_timeout = "超时" in msg
            if is_timeout and attempt < RETRY_ON_TIMEOUT:
                print(f"[semantic_proposer] LLM 超时,重试 {attempt + 1}/{RETRY_ON_TIMEOUT} ...",
                      file=sys.stderr)
                continue
            print(f"[semantic_proposer] LLM 调用失败: {e}", file=sys.stderr)
            return None
    return None


# ============================================================ 主流程
def propose(grid, schema: dict, *, client=None):
    """从 Grid 标签 + schema 草拟 4 个 semantic YAML。

    返回 (files: dict[str,str], gate: dict) 或 None。
    files = {metrics.yaml, taxonomy.yaml, synonyms.yaml, rules.yaml}(rules 模板生成);
    gate = 闸门报告(n_metrics/n_resolve_ok/resolve_fail/errors/warns)。
    LLM 不可用 → 打印引导、返回 None。
    """
    client = client or llm_client.get_default()
    if not client.available:
        print(
            "[semantic_proposer] LLM 未配置(LLM_BASE_URL / LLM_API_KEY)。\n"
            "  (a) 在 .env 配置 LLM_BASE_URL / LLM_API_KEY 后重试,或\n"
            "  (b) 手编语义层(参考 semantic/*.yaml 模板,可用「语义层」页引导编辑),或\n"
            "  (c) 直接用 committed 语义层: semantic/*.yaml",
            file=sys.stderr,
        )
        return None

    inventory = build_label_inventory(grid, schema)
    max_year = (inventory.get("year_range") or (None, None))[1]
    if not max_year:
        print("[semantic_proposer] 无法从 Grid 推断年份范围,rules 模板无法生成", file=sys.stderr)
        return None

    system = SYSTEM_PROMPT
    user = build_user_prompt(inventory)
    preview = _build_preview_grid(grid, schema)
    rules = _template_rules(max_year)

    # 第 1 轮
    raw = _try_chat(client, system, user)
    if not raw:
        return None
    bundle = _parse_bundle(raw)
    if not bundle:
        print("[semantic_proposer] LLM 输出解析失败(非 {metrics,taxonomy,synonyms})", file=sys.stderr)
        return None
    metrics, taxonomy, synonyms = bundle
    gate = _gate(metrics, taxonomy, synonyms, rules, preview, schema)

    # 1 轮修复(lint error 或 resolve 失败)
    if gate["errors"] or gate["resolve_fail"]:
        issues = _gate_issues_for_repair(gate)
        print(f"[semantic_proposer] 闸门发现 {len(issues)} 个问题,启动修复轮...", file=sys.stderr)
        repair_user = (user + "\n\n## 上轮输出:\n```yaml\n" + _clean_yaml(raw)
                       + "\n```\n\n## 闸门问题(请逐条修正,metric 名/locator.row 用真实标签):\n- "
                       + "\n- ".join(issues)
                       + "\n\n请重新输出完整 {metrics,taxonomy,synonyms} YAML。")
        repaired = _try_chat(client, system, repair_user)
        if repaired:
            nb = _parse_bundle(repaired)
            if nb:
                metrics, taxonomy, synonyms = nb
                gate = _gate(metrics, taxonomy, synonyms, rules, preview, schema)
                if gate["errors"] or gate["resolve_fail"]:
                    print(f"[semantic_proposer] 修复后仍剩 {len(gate['errors']) + len(gate['resolve_fail'])} 个问题,交人复核",
                          file=sys.stderr)
                else:
                    print("[semantic_proposer] 修复后闸门通过", file=sys.stderr)

    files = {
        "metrics.yaml": SE.dump_metrics_yaml(metrics),
        "taxonomy.yaml": SE.dump_taxonomy_yaml(taxonomy),
        "synonyms.yaml": SE.dump_synonyms_yaml(synonyms),
        "rules.yaml": yaml.safe_dump(rules, allow_unicode=True, sort_keys=False,
                                     default_flow_style=False),
    }
    return files, gate


if __name__ == "__main__":
    # CLI 自检:对 seed grid+schema 跑(需 LLM key);无 key 则演示 inventory+gate on committed
    import preprocess as PRE
    import schema_spec as SS
    seed_xls = os.path.join(_HERE, "..", "..", "测试数据.xls")
    PRE.XLS = seed_xls
    g = PRE.load_grid()
    with open(os.path.join(_HERE, "..", "schemas", "三峡国际经营数据库.yaml"), encoding="utf-8") as f:
        schema = yaml.safe_load(f)
    inv = build_label_inventory(g, schema)
    print("year_range:", inv["year_range"])
    fin_tbl = next((t for t in inv["tables"] if t["target"] == "row_map"), {})
    print("首个 row_map 表行标签数:", len(fin_tbl.get("row_labels") or []))
    res = propose(g, schema)
    if res is None:
        print("(LLM 未配置,仅演示 inventory;配 LLM_BASE_URL/API_KEY 后可生成)")
        sys.exit(2)
    files, gate = res
    print(f"闸门: metrics={gate['n_metrics']} resolve_ok={gate['n_resolve_ok']} "
          f"resolve_fail={len(gate['resolve_fail'])} errors={len(gate['errors'])}")

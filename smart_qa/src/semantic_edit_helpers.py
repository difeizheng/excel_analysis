"""语义层(metrics.yaml)引导式编辑的纯函数:locator shape 判别、跨层选项来源、
即时 resolve、跨文件 lint、指标补丁、YAML 序列化。

纯逻辑,不依赖 streamlit/pandas/文件 IO,便于单测。
页面层负责读 metrics/schema/taxonomy/synonyms 文本 → 解析 → 调本模块函数。

为什么单独抽出(与 schema_edit_helpers 同源思路):
- metrics.yaml 的 locator 是"静默失败的字符串绑定":locator.row 必须逐字命中
  Excel 单元格,敲错不会报错、只会让 resolve 查不到 → 答案莫名变空。
- 这些函数把"按活内容点选 + 即时验证"转成可单测的选项与反馈,并把指标补丁
  回 metrics dict 再序列化。
- 后端(semantic_layer/loader/validate)零改动;本模块是 UI 专用的纯工具。

数据约定:
- metrics/taxonomy/synonyms/schema 均为 yaml.safe_load 后的 dict。
- grid: {sheet_name: list[list[str]]}(字符串化单元格,None→"")。
- locator 形态随指标种类不同 → locator_shape() 给出控件分支。
"""
from __future__ import annotations

import copy

import yaml


# ---------------------------------------------------------------- 解析(失败返回 None,不抛)
def _parse_dict(text) -> dict | None:
    """yaml.safe_load + 顶层 dict 校验;解析失败/非 dict → None。"""
    try:
        data = yaml.safe_load(text)
    except (yaml.YAMLError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def load_metrics(text) -> dict | None:
    return _parse_dict(text)


def load_schema(text) -> dict | None:
    return _parse_dict(text)


def load_taxonomy(text) -> dict | None:
    return _parse_dict(text)


# ---------------------------------------------------------------- locator 形态判别
def locator_shape(info: dict) -> str:
    """判别指标的 locator 形态 → 决定录入控件分支。

    优先级(越上越先):derived > taxonomy > subtotal > row_map > unknown。
    - derived:  derived=true(派生指标,无 locator,靠 base_metric+operation)
    - taxonomy: 有 aggregation(按分类聚合,如 sum_by_方式)
    - subtotal: is_subtotal=true(row 是 schema 的 emit_key,非单元格)
    - row_map:  locator.row 存在(扁平行标签定位)
    - unknown:  其余/缺 locator
    """
    if not isinstance(info, dict):
        return "unknown"
    if info.get("derived"):
        return "derived"
    if info.get("aggregation"):
        return "taxonomy"
    if info.get("is_subtotal"):
        return "subtotal"
    loc = info.get("locator")
    if isinstance(loc, dict) and str(loc.get("row", "")).strip():
        return "row_map"
    return "unknown"


# ---------------------------------------------------------------- 选项来源(跨层取自 grid/schema/taxonomy)
def row_label_options(grid: list[list[str]], label_col_idx: int) -> list[str]:
    """该 sheet 标签列(label_col_idx)去重非空标签,保持首次出现顺序(供 row_map 行下拉)。"""
    if not isinstance(label_col_idx, int) or label_col_idx < 0:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for row in (grid or []):
        if not isinstance(row, list) or label_col_idx >= len(row):
            continue
        val = str(row[label_col_idx]).strip()
        if val and val not in seen:
            out.append(val)
            seen.add(val)
    return out


def subtotal_key_options(schema: dict, sheet: str, table: str | None = None) -> list[str]:
    """指定 gen_subtotals 表的 subtotal_rules emit_key,去重保序(供 subtotal 下拉)。

    row 选项来自 schema(emit_key),不读 grid——subtotal 的 row 是逻辑键非单元格。
    table=None 时取该 sheet 所有 gen_subtotals 表的并集。
    """
    if not isinstance(schema, dict):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for sh in schema.get("sheets") or []:
        if sh.get("name") != sheet:
            continue
        for tb in sh.get("tables") or []:
            if tb.get("target") != "gen_subtotals":
                continue
            if table is not None and tb.get("name") != table:
                continue
            for rule in tb.get("subtotal_rules") or []:
                ek = str(rule.get("emit_key", "")).strip()
                if ek and ek not in seen:
                    out.append(ek)
                    seen.add(ek)
    return out


def taxonomy_node_options(taxonomy: dict) -> list[str]:
    """taxonomy.yaml 所有"分类树"下的节点键(供 taxonomy_node 下拉 + lint 校验)。

    分类树 = 顶层分类下,至少一个子项是 list 或含 includes 的 dict;
    据此自然排除 aggregation_rules 这类元键(其子项只有 description)。
    """
    out: list[str] = []
    seen: set[str] = set()
    for tree in (taxonomy or {}).values():
        if not isinstance(tree, dict):
            continue
        is_tree = any(
            isinstance(v, list) or (isinstance(v, dict) and "includes" in v)
            for v in tree.values()
        )
        if not is_tree:
            continue
        for node in tree:
            if node not in seen:
                out.append(node)
                seen.add(node)
    return out


# ---------------------------------------------------------------- schema 内查 helper
def _schema_has_sheet(schema: dict, sheet) -> bool:
    return any(sh.get("name") == sheet for sh in (schema or {}).get("sheets") or [])


def _label_col_for_sheet(schema: dict, sheet) -> int:
    """该 sheet 首个 table 的 label_col_idx(默认 0)。"""
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


# ---------------------------------------------------------------- 即时 resolve(验证 locator 真能落地)
def resolve_locator(
    info: dict, grid: dict, schema: dict, metrics: dict | None = None
) -> tuple[bool, str]:
    """验证 info 的 locator 真能落到一个位置。返回 (ok, 普通话细节)。

    - row_map:   row 在 sheet 标签列? ✓"命中「利润总额」(r01 · 第2行)" / ✗
    - subtotal:  row(emit_key)在 schema emit_keys? ✓/✗
    - taxonomy:  sheet 在 schema? ✓/✗
    - derived:   base_metric 非空;若给 metrics 则确认其存在 ✓/✗
    """
    shape = locator_shape(info)
    info = info if isinstance(info, dict) else {}
    loc = info.get("locator") if isinstance(info.get("locator"), dict) else {}

    if shape == "derived":
        base = str(info.get("base_metric") or "").strip()
        if not base:
            return False, "派生指标缺少 base_metric"
        if metrics is not None and base not in metrics:
            return False, f"基准指标「{base}」不在指标字典中"
        return True, f"派生指标,基准 = {base}"

    if shape == "taxonomy":
        sheet = loc.get("sheet", "")
        if not _schema_has_sheet(schema, sheet):
            return False, f"sheet「{sheet}」不在 schema 中"
        return True, f"分类聚合 @ {sheet}"

    if shape == "subtotal":
        sheet = loc.get("sheet", "")
        key = str(loc.get("row") or "").strip()
        if not key:
            return False, "subtotal 指标缺少 row(emit_key)"
        valid = subtotal_key_options(schema, sheet)
        if key not in valid:
            return False, f"小计键「{key}」不在 schema emit_keys({valid or '无'})中"
        return True, f"小计键「{key}」存在"

    if shape == "row_map":
        sheet = loc.get("sheet", "")
        row = str(loc.get("row") or "").strip()
        if not row:
            return False, "row_map 指标缺少 row"
        g = (grid or {}).get(sheet)
        if not g:
            return False, f"sheet「{sheet}」的预览网格不可用"
        label_col = _label_col_for_sheet(schema, sheet)
        for i, r in enumerate(g):
            if isinstance(r, list) and label_col < len(r) \
                    and str(r[label_col]).strip() == row:
                return True, f"命中「{row}」(r{i:02d} · 第{i + 1}行)"
        return False, f"sheet「{sheet}」标签列无「{row}」行"

    return False, "无法识别 locator 形态,请在 YAML 原文 tab 定义"


# ---------------------------------------------------------------- 跨文件 lint(非阻塞)
def _schema_sheet_names(schema: dict) -> set:
    return {sh.get("name") for sh in (schema or {}).get("sheets") or []}


def lint_metrics(
    metrics: dict, taxonomy: dict, synonyms: dict, schema: dict
) -> list[tuple[str, str, str]]:
    """跨文件一致性检查。返回 [(severity, metric_name, msg)],severity ∈ error/warn/info。

    非阻塞:页面只展示,不阻止保存(metrics 是多指标集合,不该因一个坏 locator 卡住保存别的)。
    - error: locator.sheet 不在 schema;taxonomy_node 不在 taxonomy;parent 非真指标;
             subtotal 的 row 不在 schema emit_keys(跨层!)
    - warn : 同一别名(合并 metrics.synonyms + synonyms.metric_aliases)指向 ≥2 个不同指标
             (静默最长匹配歧义);超短别名(≤2 字)是 ≥2 个指标名的子串(误命中风险)
    - info : 指标无 synonyms 且非 derived(可能问不到)
    """
    findings: list[tuple[str, str, str]] = []
    metrics = metrics if isinstance(metrics, dict) else {}
    taxonomy = taxonomy if isinstance(taxonomy, dict) else {}
    synonyms = synonyms if isinstance(synonyms, dict) else {}
    schema = schema if isinstance(schema, dict) else {}

    sheet_names = _schema_sheet_names(schema)
    valid_nodes = set(taxonomy_node_options(taxonomy))

    for name, info in metrics.items():
        info = info if isinstance(info, dict) else {}
        loc = info.get("locator") if isinstance(info.get("locator"), dict) else {}

        sheet = loc.get("sheet")
        if sheet and sheet not in sheet_names:
            findings.append(("error", name, f"locator.sheet「{sheet}」不在 schema sheets 中"))

        node = info.get("taxonomy_node")
        if node and valid_nodes and node not in valid_nodes:
            findings.append(("error", name, f"taxonomy_node「{node}」不在 taxonomy 分类树中"))

        parent = info.get("parent")
        if parent and parent not in metrics:
            findings.append(("error", name, f"parent「{parent}」不是已定义指标"))

        if info.get("is_subtotal"):
            key = str(loc.get("row") or "").strip()
            emit_keys = set(subtotal_key_options(schema, sheet or ""))
            if key and emit_keys and key not in emit_keys:
                findings.append(("error", name,
                                 f"is_subtotal 的 row「{key}」不在 schema emit_keys 中"))

        if not (info.get("synonyms") or []) and not info.get("derived"):
            findings.append(("info", name, "无 synonyms,用户可能用别名问不到(建议补口语别名)"))

    # 别名 → 指标集合(合并两处来源)
    alias_targets: dict[str, set] = {}
    for name, info in metrics.items():
        info = info if isinstance(info, dict) else {}
        alias_targets.setdefault(str(name), set()).add(name)
        for syn in info.get("synonyms") or []:
            alias_targets.setdefault(str(syn), set()).add(name)
    for k, v in (synonyms.get("metric_aliases") or {}).items():
        alias_targets.setdefault(str(k), set()).add(str(v))

    names = list(metrics.keys())
    for alias, targets in alias_targets.items():
        if len(targets) > 1:
            findings.append(("warn", "/".join(sorted(targets)),
                             f"别名「{alias}」同时指向 {sorted(targets)},最长匹配可能歧义"))
        elif len(alias) <= 2 and sum(1 for n in names if alias and alias in n) >= 2:
            findings.append(("warn", next(iter(targets)) if targets else alias,
                             f"超短别名「{alias}」是多个指标名的子串,子串匹配可能误命中"))

    return findings


# ---------------------------------------------------------------- 指标补丁(不可变)
# 可编辑字段分组:空值 → 删键(YAML 干净)
_OPTIONAL_STR = (
    "unit", "default_entity", "taxonomy_node", "parent",
    "aggregation", "operation", "base_metric", "note", "display", "sign_convention",
)
_OPTIONAL_BOOL = ("is_subtotal", "derived")


def with_metric_edited(metrics: dict, name: str, values: dict) -> dict:
    """返回 metrics 的深拷贝,其中指标 name 被 values 覆盖(不可变,不改入参)。

    - values["_new_name"](非空且 != name)→ 改名(重键,保留原顺序位);撞已有名 → ValueError。
    - 可选 str/bool/list 字段:空值(""/None/[]/falsy)→ 删键;否则写。
    - values["locator"]:dict 且非空 → 覆盖(只保留非空子键);否则删键。
    - 其余该指标的既有字段(含未知字段)原样保留;兄弟指标不动。
    - name 不存在 → 视为新增(从空 info 起应用)。
    """
    out = copy.deepcopy(metrics) if isinstance(metrics, dict) else {}
    values = values if isinstance(values, dict) else {}
    new_name = str(values.get("_new_name") or "").strip()
    if new_name and new_name != name and new_name in out:
        raise ValueError(f"指标名「{new_name}」已存在")

    info = dict(out.get(name, {}))

    for key in ("synonyms",):
        if key in values:
            v = values[key]
            if v:
                info[key] = list(v)
            else:
                info.pop(key, None)
    for key in _OPTIONAL_STR:
        if key in values:
            v = str(values[key] or "").strip()
            if v:
                info[key] = v
            else:
                info.pop(key, None)
    for key in _OPTIONAL_BOOL:
        if key in values:
            if values[key]:
                info[key] = bool(values[key])
            else:
                info.pop(key, None)

    if "locator" in values:
        loc = values["locator"]
        if isinstance(loc, dict):
            cleaned = {k: v for k, v in loc.items() if str(v).strip() != ""}
            if cleaned:
                info["locator"] = cleaned
            else:
                info.pop("locator", None)
        else:
            info.pop("locator", None)

    if new_name and new_name != name:
        rebuilt = {}
        inserted = False
        for k, v in out.items():
            if k == name:
                rebuilt[new_name] = info
                inserted = True
            else:
                rebuilt[k] = v
        if not inserted:
            rebuilt[new_name] = info
        return rebuilt
    out[name] = info
    return out


def delete_metric(metrics: dict, name: str) -> dict:
    """深拷贝并删除指标 name(不存在则原样返回拷贝)。"""
    out = copy.deepcopy(metrics) if isinstance(metrics, dict) else {}
    out.pop(name, None)
    return out


def add_metric(metrics: dict, name: str) -> dict:
    """深拷贝并新增最小指标 stub;已存在则幂等不覆盖。空名返回原拷贝。"""
    out = copy.deepcopy(metrics) if isinstance(metrics, dict) else {}
    nm = str(name or "").strip()
    if nm and nm not in out:
        out[nm] = {"locator": {}, "unit": ""}
    return out


# ---------------------------------------------------------------- YAML 序列化
def dump_metrics_yaml(metrics: dict) -> str:
    """metrics dict → YAML(保持中文、键顺序、块风格)。

    注意:SafeDump 不保留注释,且把 flow 风格([a,b]/{a:1})规范化为块风格——
    这是"可视化应用"的已知代价;若在意注释/精确格式请用 YAML 原文 tab 手编。
    """
    if not isinstance(metrics, dict):
        return ""
    return yaml.safe_dump(
        metrics, allow_unicode=True, sort_keys=False, default_flow_style=False
    )


# ================================================================ taxonomy 编辑(Phase B)
def _is_tree(tree) -> bool:
    if not isinstance(tree, dict):
        return False
    return any(isinstance(v, list) or (isinstance(v, dict) and "includes" in v)
               for v in tree.values())


def taxonomy_categories(taxonomy: dict) -> list[tuple[str, bool]]:
    """顶层分类 [(name, is_tree)]。is_tree 同 taxonomy_node_options 的判据。"""
    out: list[tuple[str, bool]] = []
    for cat, tree in (taxonomy or {}).items():
        out.append((cat, _is_tree(tree)))
    return out


def tree_nodes(taxonomy: dict, category: str) -> list[dict]:
    """某分类树下的节点 [{name, includes:list, description:str, is_list:bool}],保序。

    兼容两种节点形态:列表(区域: 巴西: [巴西])与 dict({includes, description})。
    """
    tree = (taxonomy or {}).get(category) or {}
    if not isinstance(tree, dict):
        return []
    nodes: list[dict] = []
    for name, val in tree.items():
        if isinstance(val, list):
            nodes.append({"name": name, "includes": list(val), "description": "", "is_list": True})
        elif isinstance(val, dict):
            inc = val.get("includes")
            nodes.append({"name": name,
                          "includes": list(inc) if isinstance(inc, list) else [],
                          "description": str(val.get("description", "") or ""),
                          "is_list": False})
        else:
            nodes.append({"name": name, "includes": [], "description": str(val or ""), "is_list": False})
    return nodes


def with_taxonomy_node_edited(taxonomy: dict, category: str, node: str, values: dict) -> dict:
    """深拷贝;编辑 category 树下 node(不可变)。

    values: includes(list)/description(str)/_new_name(改名)。
    保留节点形状:原为 list → 写回 list(只 includes);原为 dict → 写回 {includes, description?}。
    改名撞已有节点 → ValueError。
    """
    out = copy.deepcopy(taxonomy) if isinstance(taxonomy, dict) else {}
    tree = out.setdefault(category, {})
    new_name = str(values.get("_new_name") or "").strip()
    if new_name and new_name != node and new_name in tree:
        raise ValueError(f"节点「{new_name}」已存在")
    is_list = isinstance(tree.get(node), list)
    inc = [str(x).strip() for x in (values.get("includes") or []) if str(x).strip()]
    desc = str(values.get("description") or "").strip()
    new_val = inc if is_list else {"includes": inc, **({"description": desc} if desc else {})}
    if new_name and new_name != node:
        rebuilt = {}
        for k, v in tree.items():
            rebuilt[new_name if k == node else k] = new_val if k == node else v
        out[category] = rebuilt
    else:
        tree[new_name or node] = new_val
    return out


def delete_taxonomy_node(taxonomy: dict, category: str, node: str) -> dict:
    out = copy.deepcopy(taxonomy) if isinstance(taxonomy, dict) else {}
    (out.get(category) or {}).pop(node, None)
    return out


def add_taxonomy_node(taxonomy: dict, category: str, node: str) -> dict:
    """新增节点(dict 形 {includes: []});已存在幂等。"""
    out = copy.deepcopy(taxonomy) if isinstance(taxonomy, dict) else {}
    tree = out.setdefault(category, {})
    nm = str(node or "").strip()
    if nm and nm not in tree:
        tree[nm] = {"includes": []}
    return out


def add_taxonomy_category(taxonomy: dict, category: str) -> dict:
    out = copy.deepcopy(taxonomy) if isinstance(taxonomy, dict) else {}
    cat = str(category or "").strip()
    if cat and cat not in out:
        out[cat] = {}
    return out


def delete_taxonomy_category(taxonomy: dict, category: str) -> dict:
    out = copy.deepcopy(taxonomy) if isinstance(taxonomy, dict) else {}
    out.pop(category, None)
    return out


def dump_taxonomy_yaml(taxonomy: dict) -> str:
    if not isinstance(taxonomy, dict):
        return ""
    return yaml.safe_dump(taxonomy, allow_unicode=True, sort_keys=False,
                          default_flow_style=False)


# ================================================================ synonyms 编辑(Phase B)
def metric_alias_pairs(synonyms: dict) -> list[tuple[str, str]]:
    """metric_aliases 的 (口语, 标准) 对,保序。"""
    ma = (synonyms or {}).get("metric_aliases") or {}
    return [(str(k), str(v)) for k, v in ma.items() if str(k).strip()]


def with_metric_aliases_edited(synonyms: dict, pairs: list[tuple[str, str]]) -> dict:
    """深拷贝;用 pairs(去空、口语去重、保序)替换 metric_aliases。"""
    out = copy.deepcopy(synonyms) if isinstance(synonyms, dict) else {}
    ma: dict[str, str] = {}
    for k, v in (pairs or []):
        kk, vv = str(k or "").strip(), str(v or "").strip()
        if kk and vv and kk not in ma:
            ma[kk] = vv
    out["metric_aliases"] = ma
    return out


def entity_rows(synonyms: dict) -> list[dict]:
    """entities 的 [{name, aliases:list, means:str}],保序(means 取 means 或 description)。"""
    ents = (synonyms or {}).get("entities") or {}
    rows: list[dict] = []
    for name, info in ents.items():
        info = info if isinstance(info, dict) else {}
        rows.append({"name": name,
                     "aliases": [str(a) for a in (info.get("aliases") or [])],
                     "means": str(info.get("means") or info.get("description") or "")})
    return rows


def with_entities_edited(synonyms: dict, rows: list[dict]) -> dict:
    """深拷贝;用 rows 重建 entities(去空名、去重、保序;空别名→空列表)。"""
    out = copy.deepcopy(synonyms) if isinstance(synonyms, dict) else {}
    ents: dict[str, dict] = {}
    for r in (rows or []):
        name = str(r.get("name") or "").strip()
        if not name or name in ents:
            continue
        aliases = [str(a).strip() for a in (r.get("aliases") or []) if str(a).strip()]
        means = str(r.get("means") or "").strip()
        ents[name] = {"aliases": aliases, **({"means": means} if means else {})}
    out["entities"] = ents
    return out


def kv_pairs(synonyms: dict, section: str) -> list[tuple[str, str]]:
    """通用 口语→标准码 映射对(time_aliases / quantity_aliases 等),保序。"""
    sec = (synonyms or {}).get(section) or {}
    return [(str(k), str(v)) for k, v in sec.items() if str(k).strip()]


def with_kv_edited(synonyms: dict, section: str, pairs: list[tuple[str, str]]) -> dict:
    """深拷贝;用 pairs 替换某 section(去空、键去重、保序)。"""
    out = copy.deepcopy(synonyms) if isinstance(synonyms, dict) else {}
    d: dict[str, str] = {}
    for k, v in (pairs or []):
        kk, vv = str(k or "").strip(), str(v or "").strip()
        if kk and vv and kk not in d:
            d[kk] = vv
    out[section] = d
    return out


def lint_synonyms(synonyms: dict, metrics: dict) -> list[tuple[str, str, str]]:
    """synonyms 一致性 → [(severity, where, msg)](非阻塞)。
    error: metric_aliases 目标不是真指标;warn: 实体别名同时属多个实体。
    """
    findings: list[tuple[str, str, str]] = []
    metrics = metrics if isinstance(metrics, dict) else {}
    for k, v in ((synonyms or {}).get("metric_aliases") or {}).items():
        vv = str(v or "").strip()
        if vv and vv not in metrics:
            findings.append(("error", f"metric_aliases:{k}", f"目标「{vv}」不是已定义指标"))
    seen_alias: dict[str, str] = {}
    for name, info in ((synonyms or {}).get("entities") or {}).items():
        info = info if isinstance(info, dict) else {}
        for a in info.get("aliases") or []:
            a = str(a)
            if a in seen_alias and seen_alias[a] != name:
                findings.append(("warn", f"entities:{a}",
                                 f"实体别名「{a}」同时属于「{seen_alias[a]}」与「{name}」"))
            else:
                seen_alias.setdefault(a, name)
    return findings


def dump_synonyms_yaml(synonyms: dict) -> str:
    if not isinstance(synonyms, dict):
        return ""
    return yaml.safe_dump(synonyms, allow_unicode=True, sort_keys=False,
                          default_flow_style=False)


# ================================================================ rules 编辑(Phase C2/C3)
def rules_summary(rules: dict) -> list[tuple[str, str]]:
    """顶层规则 [(key, 一句描述)] 供只读速查(防哪类问题)。
    描述取 description / question / example.question;无则空串。
    """
    out: list[tuple[str, str]] = []
    for key, val in (rules or {}).items():
        desc = ""
        if isinstance(val, dict):
            desc = str(val.get("description") or val.get("question") or "")
            if not desc and isinstance(val.get("example"), dict):
                desc = "示例:" + str(val["example"].get("question") or "")
        out.append((key, desc))
    return out


def lint_rules(rules: dict) -> list[tuple[str, str, str]]:
    """rules 一致性 → [(severity, where, msg)](非阻塞)。
    warn: recent_years(引擎计算近 N 年/CAGR 期初时消费)缺 windows/cagr_initial_rule。
    """
    findings: list[tuple[str, str, str]] = []
    rules = rules if isinstance(rules, dict) else {}
    ry = rules.get("recent_years")
    if ry is None:
        findings.append(("warn", "recent_years",
                         "缺 recent_years(引擎消费:近 N 年/CAGR 期初)——问「近三年」类会出错"))
    elif isinstance(ry, dict):
        for sub in ("windows", "cagr_initial_rule"):
            if sub not in ry:
                findings.append(("warn", f"recent_years.{sub}",
                                 f"缺 recent_years.{sub}(引擎消费)"))
    return findings

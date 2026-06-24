"""工作台 · 语义层:全 4 文件引导式编辑 + resolve 试用。

四个顶层 tab(共享同一份磁盘 semantic,任务自包含):
- 🧠 指标编辑(引导):指标卡 + locator 按类型点选 + 即时 resolve + 跨文件 lint + ❓ 问句教学。
- 🌳 分类与别名:taxonomy 分类树编辑 + metric_aliases(口语⇄标准指标下拉)+ entities + 时间/量词。
- 📐 业务规则:规则速查(只读,"防哪类问题")+ 🔮 自然语言改动(LLM 起草,recent_years 守护)。
- 📝 YAML 原文(4 文件):裸编辑 + 应用热重载(逃生口)。
- resolve 试用:输入一句话看实体/指标消歧(跨 tab 共享的活检验)。

全确定性(仅业务规则的 🔮 起草用 LLM,且 semantic 无真实数值,天然安全)。
编辑只影响当前任务;不再写回全局 committed。后端 semantic_layer 0 改动;纯逻辑在 semantic_edit_helpers。
"""
from __future__ import annotations
import os
import yaml
import streamlit as st
import pandas as pd

import ui_common as U  # noqa: E402
import task_store as TS  # noqa: E402
import semantic_layer as S  # noqa: E402
import semantic_edit_helpers as SE  # noqa: E402
import semantic_proposer as SP  # noqa: E402
import llm_client  # noqa: E402
from llm_parser import get_default  # noqa: E402

st.set_page_config(page_title="语义层", page_icon="🧠", layout="wide")
st.title("🧠 语义层")
st.caption(
    f"{U.ICON_DET} 指标引导 / 分类与别名 / 业务规则 / YAML 原文 + resolve 试用(确定性为主,🔮 仅规则起草)"
)

# 任务上下文
U.render_task_sidebar()
task = U.current_task()
if task is None:
    st.info("暂无任务。请到「📤 数据接入」创建。")
    st.stop()

SEM_FILES = ["metrics.yaml", "taxonomy.yaml", "synonyms.yaml", "rules.yaml"]
sdir = task.semantic_dir

# ---- per-task 文本缓存(切任务各自独立;各引导面以此与磁盘/YAML tab 同步)----
sem = st.session_state.setdefault("semantic_by_task", {})
if task.id not in sem:
    texts = {}
    for fn in SEM_FILES:
        p = os.path.join(sdir, fn)
        texts[fn] = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
    sem[task.id] = {"texts": texts, "dirty": False}

st.caption(f"当前任务:**{task.name}**　生效目录:{sdir}")

# 8.2 回归闸门:若上一次写盘被守护,顶部显示前后 diff
U.render_corpus_gate(task.id)


# ============================================================ 8.4 · LLM 一键生成语义层
def _render_semantic_proposer():
    """🔮 从当前任务 Grid 标签一键草拟 4 个 semantic YAML(LLM)+ 闸门复核 + 应用。

    LLM 只看标签(行标签/列键/分类器),不碰数值;rules 由年份模板生成。
    应用走 gated_write(批量写 4 文件 + 单次热重载 + 失效 + 语料闸门 diff)。
    """
    box = st.session_state.get(f"_semprop_{task.id}")  # {"files":{fn:yaml}, "gate":{...}}
    # 生成入口(可折叠 expander)。结果展示刻意放在 expander **之外**——streamlit 禁止
    # expander 嵌套(曾在此触发 StreamlitAPIException: Expanders may not be nested),
    # 故"闸门详情/文件预览"用与生成入口同级的顶层 expander。
    # expanded 取反:没结果时展开显入口;有结果时收起,把空间让给下方结果块。
    with st.expander(f"{U.ICON_LLM} 一键生成语义层(4 文件)—— LLM 起草 + 闸门复核",
                     expanded=(box is None)):
        st.caption(
            "从当前任务 Grid 标签草拟 metrics/taxonomy/synonyms(LLM)+ rules(年份模板),"
            "过 lint + resolve_locator 闸门。覆盖当前任务语义层(任务副本可逆);committed seed 不受影响。"
        )
        llm = get_default()
        if not llm.available:
            st.info(llm.status())
        if st.button("🔮 生成语义层", disabled=not llm.available,
                     help="LLM 只看标签(不碰数值),约 1-2 分钟,含 1 轮自动修复。"):
            grid = U.get_grid()
            if grid is None:
                st.error("当前任务尚未生成 Grid(先到「数据接入/Schema 编辑」初始化)。")
            else:
                try:
                    with open(task.schema_path, encoding="utf-8") as f:
                        schema = yaml.safe_load(f)
                except Exception as e:
                    st.error(f"读取 schema 失败: {e}")
                    schema = None
                if schema is not None:
                    with st.spinner(f"{U.ICON_LLM} LLM 起草语义层中(约 1-2 分钟,含 1 轮自动修复)..."):
                        res = SP.propose(grid, schema)
                    if res is None:
                        st.error("生成失败(LLM 未配置/调用失败/解析失败,见终端)。可手编或用 committed。")
                    else:
                        files, gate = res
                        st.session_state[f"_semprop_{task.id}"] = {"files": files, "gate": gate}
                        st.rerun()

    # ---- 结果展示(box 存在才渲染;此处 expander 均为顶层,不嵌套)----
    if not box:
        return
    files, gate = box["files"], box["gate"]
    n_fail = len(gate["resolve_fail"])
    n_err = len(gate["errors"])
    if n_err + n_fail:
        st.error(f"闸门发现 {n_err + n_fail} 个问题(lint error {n_err} + 落地失败 {n_fail});"
                 "仍可应用后人复核,或重新生成。")
    else:
        st.success(f"闸门通过:{gate['n_metrics']} 个指标,{gate['n_resolve_ok']} 个落地,"
                   f"{len(gate['warns'])} 条建议。")

    with st.expander("闸门详情"):
        for r in gate["resolve_fail"]:
            st.write(f"- 🔴 落地失败「{r['metric']}」:{r['msg']}")
        for e in gate["errors"]:
            st.write(f"- 🟠 lint error「{e['where']}」:{e['msg']}")
        for w in gate["warns"][:20]:
            st.write(f"- ⚪ {w['severity']}「{w['where']}」:{w['msg']}")
        if len(gate["warns"]) > 20:
            st.caption(f"... 其余 {len(gate['warns']) - 20} 条建议")
    for fn in SEM_FILES:
        with st.expander(f"📄 {fn}"):
            st.code(files.get(fn, ""), language="yaml")

    bc, bd = st.columns([1, 3])
    if bc.button("✓ 应用全部到当前任务", type="primary", key=f"_semprop_apply_{task.id}"):
        def _apply_all():
            for fn in SEM_FILES:
                txt = files.get(fn, "")
                TS.write_task_semantic(task.id, fn, txt)
                sem[task.id]["texts"][fn] = txt
                st.session_state[f"sem_{task.id}_{fn}"] = txt
            U.reload_current_semantic()
            U.invalidate_grid()
        U.gated_write(task.id, "语义层·LLM一键生成", _apply_all)
        st.session_state.pop(f"_semprop_{task.id}", None)
        st.success("已应用 4 文件到当前任务语义层。去问数台验证。")
        st.rerun()
    if bd.button("丢弃草稿", key=f"_semprop_discard_{task.id}"):
        st.session_state.pop(f"_semprop_{task.id}", None)
        st.rerun()


_render_semantic_proposer()


# ============================================================ page-local helpers
def _read_sheet_grid(excel_path: str, sheet_name: str, nrows: int = 200):
    """读 Excel sheet → 字符串化网格(供行标签下拉);失败返回 None。"""
    try:
        engine = "openpyxl" if excel_path.lower().endswith(".xlsx") else "xlrd"
        df = pd.read_excel(excel_path, sheet_name=sheet_name, engine=engine,
                           header=None, nrows=nrows)
        df = df.fillna("")
        return [[str(v) for v in row] for row in df.values.tolist()]
    except Exception:
        return None


def _schema_sheet_names(schema: dict) -> list[str]:
    return [sh.get("name") for sh in (schema or {}).get("sheets") or [] if sh.get("name")]


def _label_col_for_sheet(schema: dict, sheet: str) -> int:
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


def _pick(label: str, options, key: str, help: str | None = None):
    """keyed 选择器:有候选→selectbox(容忍当前值不在候选,前置显示);无候选→text_input 兜底。"""
    opts = [o for o in (options or []) if o is not None]
    cur = st.session_state.get(key)
    if opts:
        if cur is not None and cur not in opts:
            opts = [cur] + opts
        st.selectbox(label, opts, key=key, help=help)
    else:
        st.text_input(f"{label}(无候选,手填)", value=(cur or ""), key=key, help=help)


def _strip_fences(t: str) -> str:
    t = (t or "").strip()
    if t.startswith("```yaml"):
        t = t[7:]
    elif t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return t.strip()


def _write_semantic(fn: str, new_text: str) -> None:
    """写单个 semantic 文件 + 同步缓存/keyed 文本 + 热重载 + 失效 grid。"""
    TS.write_task_semantic(task.id, fn, new_text)
    sem[task.id]["texts"][fn] = new_text
    st.session_state[f"sem_{task.id}_{fn}"] = new_text   # 同步 YAML tab 显示
    U.reload_current_semantic()
    U.invalidate_grid()


def _commit(fn: str, new_text: str) -> None:
    """写 semantic + 同步 + 热重载 + 失效 grid,经语料回归闸门(前后双跑模板句)。

    单一 chokepoint:所有引导编辑经此。闸门 diff 存 session,顶部 render_corpus_gate 显示。
    """
    U.gated_write(task.id, f"语义层·{fn}", lambda: _write_semantic(fn, new_text))


def _commit_metrics(new_metrics: dict) -> None:
    _commit("metrics.yaml", SE.dump_metrics_yaml(new_metrics))


# ⚠ 改 widget key(_sem_metric)必须放 on_click=callback:selectbox(key="_sem_metric")实例化后,
# 在 handler 内 st.session_state["_sem_metric"]=v 会抛 "cannot be modified after the widget
# is instantiated"(见 [[streamlit-widget-callback-trap]])。callback 在 widget 重渲染前执行,可安全
# 改 key;内部从 sem 缓存读最新(与 _write_semantic 同步,避闭包——镜像 page2 _on_add_table 模式)。
def _on_add_metric() -> None:
    """新增默认名指标并选中它。"""
    metrics = SE.load_metrics(sem[task.id]["texts"]["metrics.yaml"]) or {}
    base, i = "新指标", 2
    nm = base
    while nm in metrics:
        nm = f"{base}{i}"; i += 1
    _commit_metrics(SE.add_metric(metrics, nm))
    st.session_state["_sem_metric"] = nm             # callback 内改 widget key = 安全
    st.session_state.pop("_sem_last_metric", None)    # 强制下次 seed 新指标的字段


def _on_delete_metric() -> None:
    """两步删除:首次点击 arm _sem_del_pending;再次点击确认并真删。"""
    cur = st.session_state.get("_sem_metric", "")
    if st.session_state.get("_sem_del_pending") == cur:
        metrics = SE.load_metrics(sem[task.id]["texts"]["metrics.yaml"]) or {}
        new_metrics = SE.delete_metric(metrics, cur)
        _commit_metrics(new_metrics)
        rest = [n for n in new_metrics if n]
        st.session_state["_sem_metric"] = rest[0] if rest else ""  # clamp 到剩余首项(安全)
        st.session_state.pop("_sem_del_pending", None)
        st.session_state.pop("_sem_last_metric", None)  # 强制下次 seed
    else:
        st.session_state["_sem_del_pending"] = cur      # arm(非 widget key,安全)


def _commit_taxonomy(new_taxonomy: dict) -> None:
    _commit("taxonomy.yaml", SE.dump_taxonomy_yaml(new_taxonomy))


def _commit_synonyms(new_synonyms: dict) -> None:
    _commit("synonyms.yaml", SE.dump_synonyms_yaml(new_synonyms))


# ⚠ 同 _on_add_metric:分类树"选 X + ➕/应用改 X"的 keyed-selectbox 配按钮,改 widget key
# (_tax_cat/_tax_node)必须走 on_click=callback(见 [[streamlit-widget-callback-trap]])。
# callback 从 sem 缓存读最新 taxonomy 避闭包;ValueError(重命名撞名)经 session flag 回显。
def _on_add_category() -> None:
    """新增默认名分类并选中。"""
    taxonomy = SE.load_taxonomy(sem[task.id]["texts"]["taxonomy.yaml"]) or {}
    nm, i = "新分类", 2
    while nm in taxonomy:
        nm = f"新分类{i}"; i += 1
    _commit_taxonomy(SE.add_taxonomy_category(taxonomy, nm))
    st.session_state["_tax_cat"] = nm


def _on_add_node(cur_cat: str) -> None:
    """在 cur_cat 下新增默认名节点并选中。"""
    taxonomy = SE.load_taxonomy(sem[task.id]["texts"]["taxonomy.yaml"]) or {}
    nm, i = "新节点", 2
    while nm in (taxonomy.get(cur_cat) or {}):
        nm = f"新节点{i}"; i += 1
    _commit_taxonomy(SE.add_taxonomy_node(taxonomy, cur_cat, nm))
    st.session_state["_tax_node"] = nm


def _on_apply_node(cur_cat: str, cur_node: str) -> None:
    """应用节点编辑(includes/description/重命名);重命名成功则切到新名(callback 内安全)。
    with_taxonomy_node_edited 撞名抛 ValueError → 经 _tax_apply_err 回显,不阻断。"""
    s = st.session_state
    taxonomy = SE.load_taxonomy(sem[task.id]["texts"]["taxonomy.yaml"]) or {}
    vals = {
        "includes": [ln.strip() for ln in (s.get("_tax_includes", "") or "").splitlines() if ln.strip()],
        "description": s.get("_tax_desc", ""),
    }
    nn = (s.get("_tax_newname") or "").strip()
    if nn and nn != cur_node:
        vals["_new_name"] = nn
    try:
        new_taxonomy = SE.with_taxonomy_node_edited(taxonomy, cur_cat, cur_node, vals)
    except ValueError as e:
        s["_tax_apply_err"] = str(e)
        return
    s.pop("_tax_apply_err", None)
    _commit_taxonomy(new_taxonomy)
    if nn and nn != cur_node:
        st.session_state["_tax_node"] = nn


def _render_lint(findings) -> None:
    errs = [f for f in findings if f[0] == "error"]
    warns = [f for f in findings if f[0] == "warn"]
    infos = [f for f in findings if f[0] == "info"]
    if errs:
        st.error(f"⚠ {len(errs)} 个一致性问题(不阻断,建议修):")
        for _, n, msg in errs:
            st.markdown(f"- **{n}**:{msg}")
    if warns:
        st.warning(f"{len(warns)} 条提醒:")
        for _, n, msg in warns:
            st.markdown(f"- **{n}**:{msg}")
    if infos:
        with st.expander(f"ℹ {len(infos)} 条提示"):
            for _, n, msg in infos:
                st.markdown(f"- **{n}**:{msg}")


SHAPES = ["row_map", "subtotal", "taxonomy", "derived"]
_SHAPE_LABEL = {
    "row_map": "行标签定位(扁平指标,按行标签取数)",
    "subtotal": "小计(按小计键 emit_key 取数)",
    "taxonomy": "分类聚合(按某维度汇总明细项目)",
    "derived": "派生指标(由基准指标运算,如增长率)",
}


def _agg_options(schema: dict) -> list[str]:
    """从 schema 的 detail 表分类维度动态生成聚合选项(sum_by_<维度>);无则回退默认。

    三峡 schema 的 detail 表分类维度为 方式/区域 → 产出与历史一致的 ["sum_by_方式","sum_by_区域"]。
    """
    dims, seen = [], set()
    for sh in (schema or {}).get("sheets") or []:
        for tb in sh.get("tables") or []:
            for d in (tb.get("detail_classifier_cols") or {}).keys():
                if d != "name" and d not in seen:
                    seen.add(d)
                    dims.append(d)
    return [f"sum_by_{d}" for d in dims] or ["sum_by_方式", "sum_by_区域"]


def _seed_metric_keys(info: dict) -> None:
    """切指标时把 _sem_* session 键从该指标当前值灌入(keyed 控件据此显示)。"""
    loc = info.get("locator") if isinstance(info.get("locator"), dict) else {}
    st_ = st.session_state
    sh = SE.locator_shape(info)
    st_["_sem_shape"] = sh if sh != "unknown" else "row_map"
    st_["_sem_sheet"] = loc.get("sheet", "")
    st_["_sem_table"] = loc.get("table", "")
    st_["_sem_row"] = str(loc.get("row") or "")
    st_["_sem_synonyms"] = "\n".join(info.get("synonyms") or [])
    st_["_sem_unit"] = info.get("unit", "") or ""
    st_["_sem_taxnode"] = info.get("taxonomy_node", "") or ""
    st_["_sem_parent"] = info.get("parent", "") or ""
    st_["_sem_agg"] = info.get("aggregation", "") or "sum_by_方式"
    st_["_sem_basemetric"] = info.get("base_metric", "") or ""
    st_["_sem_operation"] = info.get("operation", "") or "cagr"
    st_["_sem_note"] = info.get("note", "") or ""
    st_["_sem_newname"] = ""


def _current_info(shape: str, st_) -> dict:
    """按当前 widget 值构造临时 info(供即时 resolve,不必 apply)。"""
    info: dict = {}
    if shape == "row_map":
        info["locator"] = {"sheet": st_.get("_sem_sheet", ""), "row": st_.get("_sem_row", "")}
    elif shape == "subtotal":
        info["is_subtotal"] = True
        info["locator"] = {"sheet": st_.get("_sem_sheet", ""), "row": st_.get("_sem_row", "")}
    elif shape == "taxonomy":
        info["aggregation"] = st_.get("_sem_agg") or "sum_by_方式"
        info["locator"] = {"sheet": st_.get("_sem_sheet", "")}
    elif shape == "derived":
        info["derived"] = True
        info["base_metric"] = st_.get("_sem_basemetric", "")
    return info


# ============================================================ 四个顶层 tab
tab_metric, tab_taxsyn, tab_rules, tab_yaml = st.tabs(
    ["🧠 指标编辑(引导)", "🌳 分类与别名", "📐 业务规则", "📝 YAML 原文(4 文件)"]
)

# ------------------------------------------------ 指标编辑(引导)tab —— Phase A + C1
with tab_metric:
    metrics = SE.load_metrics(sem[task.id]["texts"]["metrics.yaml"])
    schema = SE.load_schema(open(task.schema_path, encoding="utf-8").read()) \
        if os.path.exists(task.schema_path) else None
    taxonomy = SE.load_taxonomy(sem[task.id]["texts"]["taxonomy.yaml"])
    synonyms = SE.load_metrics(sem[task.id]["texts"]["synonyms.yaml"])

    if metrics is None:
        st.error("当前 metrics.yaml 解析失败,无法引导编辑。请到「📝 YAML 原文」tab 修正后点「应用(热重载)」。")
    else:
        s = st.session_state
        # ---- 活 grid(每 schema sheet,供行标签下拉)----
        grids: dict[str, list] = {}
        excel = task.excel_path
        if schema and os.path.exists(excel):
            for sh in _schema_sheet_names(schema):
                g = _read_sheet_grid(excel, sh)
                if g is not None:
                    grids[sh] = g

        # ---- 跨文件 lint 横幅(metrics + synonyms 跨表)----
        _render_lint(SE.lint_metrics(metrics, taxonomy or {}, synonyms or {}, schema or {}))
        syn_lint = SE.lint_synonyms(synonyms or {}, metrics)
        if syn_lint:
            with st.expander(f"🔁 synonyms 一致性({len(syn_lint)} 条)"):
                _render_lint(syn_lint)

        names = [n for n in metrics.keys() if n]
        if not names:
            st.warning("metrics.yaml 为空。点「➕ 新增指标」开始,或到「❓ 问句教学」用一句话创建。")

        # ---- 指标选择器 + 新增/删除(表单外)----
        if names:
            if s.get("_sem_metric") not in names:
                s["_sem_metric"] = names[0]
            cur = s["_sem_metric"]
            cs1, cs2, cs3 = st.columns([6, 1, 1])
            with cs1:
                def _fmt(n):
                    info = metrics.get(n, {})
                    return f"{n} · {info.get('unit', '') or '?'} · {SE.locator_shape(info)}"
                st.selectbox("选择指标", names, index=names.index(cur),
                             format_func=_fmt, key="_sem_metric")
            cur = s["_sem_metric"]
            with cs2:
                st.button("➕ 新增", use_container_width=True, on_click=_on_add_metric)
            with cs3:
                pending = s.get("_sem_del_pending") == cur
                st.button("⚠ 确认删除?" if pending else "🗑 删除",
                          use_container_width=True, disabled=not cur,
                          on_click=_on_delete_metric)

            # 切指标:灌入 _sem_* 键(无需 rerun)
            info = metrics.get(cur, {})
            changed = s.get("_sem_last_metric") != cur
            if (changed or s.get("_sem_last_metric") is None) and cur:
                _seed_metric_keys(info)
                s["_sem_last_metric"] = cur
                s.pop("_sem_del_pending", None)

            st.divider()
            st.markdown(f"##### 编辑:**{cur}**")
            st.selectbox("指标类型(决定取数方式与下方字段)", SHAPES,
                         key="_sem_shape", format_func=lambda x: _SHAPE_LABEL.get(x, x))
            shape = s.get("_sem_shape", "row_map")
            sheet_opts = _schema_sheet_names(schema) if schema else []

            if shape == "row_map":
                _pick("定位 sheet", sheet_opts, "_sem_sheet")
                row_opts = SE.row_label_options(grids.get(s.get("_sem_sheet", ""), []),
                                                _label_col_for_sheet(schema or {}, s.get("_sem_sheet", "")))
                _pick("定位行(locator.row,须与单元格逐字相等)", row_opts, "_sem_row")
            elif shape == "subtotal":
                _pick("定位 sheet", sheet_opts, "_sem_sheet")
                st.text_input("逻辑表 table(如「年度」)", key="_sem_table")
                _pick("小计键(locator.row = schema emit_key)",
                      SE.subtotal_key_options(schema or {}, s.get("_sem_sheet", "")), "_sem_row")
            elif shape == "taxonomy":
                _pick("定位 sheet", sheet_opts, "_sem_sheet")
                st.text_input("逻辑表 table(如「年度」)", key="_sem_table")
                _pick("聚合方式 aggregation", _agg_options(schema), "_sem_agg")
                _pick("分类节点 taxonomy_node", SE.taxonomy_node_options(taxonomy or {}), "_sem_taxnode")
            elif shape == "derived":
                _pick("基准指标 base_metric", [n for n in names if n != cur], "_sem_basemetric")
                _pick("运算 operation", ["cagr", "growth", "yoy"], "_sem_operation")

            ok, detail = SE.resolve_locator(_current_info(shape, s), grids, schema or {}, metrics)
            (st.success if ok else st.error)(f"{'✓' if ok else '✗'} {detail}")

            st.text_area("同义词 synonyms(每行一个)", key="_sem_synonyms", height=80,
                         placeholder="利润\n总利润")
            st.text_input("单位 unit", key="_sem_unit",
                          placeholder="亿元 / 万千瓦 / 亿千瓦时 / 百分比 / 比率")
            st.text_input("重命名(留空不改)", key="_sem_newname", placeholder=cur)
            with st.expander("高级(parent / note)"):
                st.text_input("parent(父指标)", key="_sem_parent")
                st.text_input("note(备注)", key="_sem_note")
            st.caption("⚠ 应用会用标准格式重写 metrics.yaml(注释丢失);未在此编辑的字段保持不变。")

            if st.button("✓ 应用到当前任务 metrics.yaml", type="primary"):
                new_name = (s.get("_sem_newname") or "").strip()
                values: dict = {
                    "_new_name": new_name if (new_name and new_name != cur) else "",
                    "unit": s.get("_sem_unit", ""),
                    "synonyms": [ln.strip() for ln in (s.get("_sem_synonyms", "") or "")
                                 .splitlines() if ln.strip()],
                    "parent": s.get("_sem_parent", ""),
                    "note": s.get("_sem_note", ""),
                }
                if shape == "row_map":
                    values["locator"] = {"sheet": s.get("_sem_sheet", ""), "row": s.get("_sem_row", "")}
                    values.update(is_subtotal=False, derived=False, aggregation="")
                elif shape == "subtotal":
                    values["locator"] = {"sheet": s.get("_sem_sheet", ""), "table": s.get("_sem_table", ""),
                                         "row": s.get("_sem_row", "")}
                    values.update(is_subtotal=True, derived=False, aggregation="")
                elif shape == "taxonomy":
                    values["locator"] = {"sheet": s.get("_sem_sheet", ""), "table": s.get("_sem_table", "")}
                    values.update(aggregation=s.get("_sem_agg") or "sum_by_方式",
                                  taxonomy_node=s.get("_sem_taxnode", ""), is_subtotal=False, derived=False)
                elif shape == "derived":
                    values.update(derived=True, base_metric=s.get("_sem_basemetric", ""),
                                  operation=s.get("_sem_operation", ""), is_subtotal=False,
                                  aggregation="", locator={})
                try:
                    new_metrics = SE.with_metric_edited(metrics, cur, values)
                except ValueError as e:
                    st.error(str(e))
                else:
                    _commit_metrics(new_metrics)
                    if new_name and new_name != cur:
                        s["_sem_metric"] = new_name
                        s["_sem_last_metric"] = new_name
                    st.toast("已应用并热重载", icon="✅")
                    st.rerun()

        # ---- C1:问句教学(教别名 / 新建指标)----
        st.divider()
        with st.expander("❓ 问句教学(教系统认识一个说法 / 新建指标)", expanded=False):
            q = st.text_input("输入一句话,看系统现在认不认识",
                              "", key="_teach_q",
                              placeholder="用本任务的真实指标问一句,如「2024年收入是多少」")
            if q:
                ent = S.resolve_entity(q)
                met = S.resolve_metric(q)
                c1a, c1b = st.columns(2)
                c1a.write(f"**识别实体**:`{ent}`")
                c1b.write(f"**识别指标**:`{met or '(未识别)'}`")
                if met:
                    c1b.caption(f"→ {S.metric_info(met).get('unit','')}")
                st.markdown("**教一个别名**(把某个口语说法映射到已有指标)")
                tcc1, tcc2, tcc3 = st.columns([3, 3, 1])
                with tcc1:
                    st.text_input("口语说法", key="_teach_alias", placeholder="如:利润 / 风电")
                with tcc2:
                    _pick("→ 标准指标", names, "_teach_target")
                with tcc3:
                    st.write(" ")
                    if st.button("➕ 教", use_container_width=True):
                        a = (s.get("_teach_alias") or "").strip()
                        t = s.get("_teach_target") or ""
                        if a and t:
                            pairs = SE.metric_alias_pairs(synonyms or {}) + [(a, t)]
                            _commit_synonyms(SE.with_metric_aliases_edited(synonyms or {}, pairs))
                            st.toast(f"已教:「{a}」→「{t}」", icon="✅")
                            st.rerun()
                st.markdown("**或以此问句为名新建指标**(之后到上方补 locator)")
                nc1, nc2 = st.columns([4, 1])
                with nc1:
                    st.text_input("新指标名", key="_teach_newname", placeholder="如:光伏补贴")
                with nc2:
                    st.write(" ")
                    if st.button("➕ 新建", use_container_width=True):
                        nm = (s.get("_teach_newname") or "").strip()
                        if nm and nm not in metrics:
                            _commit_metrics(SE.add_metric(metrics, nm))
                            s["_sem_metric"] = nm
                            st.toast(f"已新建「{nm}」,请在上方补全定位", icon="✅")
                            st.rerun()

# ------------------------------------------------ 分类与别名 tab —— Phase B
with tab_taxsyn:
    taxonomy = SE.load_taxonomy(sem[task.id]["texts"]["taxonomy.yaml"])
    synonyms = SE.load_metrics(sem[task.id]["texts"]["synonyms.yaml"])
    metrics = SE.load_metrics(sem[task.id]["texts"]["metrics.yaml"]) or {}
    s = st.session_state

    sub_tax, sub_ma, sub_ent, sub_kv = st.tabs(
        ["🌲 分类树", "🔁 指标别名", "🏢 实体", "⏱ 时间·量词"]
    )

    # ---- 分类树 ----
    with sub_tax:
        if taxonomy is None:
            st.error("taxonomy.yaml 解析失败,请到「YAML 原文」tab 修正。")
        else:
            cats = [c for c, is_tree in SE.taxonomy_categories(taxonomy) if is_tree]
            tc1, tc2, tc3 = st.columns([6, 1, 1])
            with tc1:
                if cats:
                    if s.get("_tax_cat") not in cats:
                        s["_tax_cat"] = cats[0]
                    st.selectbox("分类", cats, key="_tax_cat")
                else:
                    st.caption("(无分类树)")
            with tc2:
                st.button("➕ 分类", on_click=_on_add_category)
            with tc3:
                cur_cat = s.get("_tax_cat")
                if st.button("🗑 分类", disabled=not cur_cat):
                    _commit_taxonomy(SE.delete_taxonomy_category(taxonomy, cur_cat))
                    st.rerun()

            cur_cat = s.get("_tax_cat")
            if cur_cat and cur_cat in (taxonomy or {}):
                st.caption("⚠ 引擎仅消费「发电方式」树的 includes( expand_taxonomy );"
                           "其余分类作文档维护。")
                nodes = SE.tree_nodes(taxonomy, cur_cat)
                node_names = [n["name"] for n in nodes]
                nc1, nc2, nc3 = st.columns([6, 1, 1])
                _node_shape = {n["name"]: ("list" if n["is_list"] else "dict") for n in nodes}
                with nc1:
                    if node_names:
                        if s.get("_tax_node") not in node_names:
                            s["_tax_node"] = node_names[0]
                        st.selectbox("节点", node_names,
                                     format_func=lambda n: f"{n} · {_node_shape.get(n, '')}",
                                     key="_tax_node")
                    else:
                        st.caption("(无节点)")
                with nc2:
                    st.button("➕ 节点", on_click=_on_add_node, args=(cur_cat,))
                with nc3:
                    cur_node = s.get("_tax_node")
                    if st.button("🗑 节点", disabled=not cur_node):
                        _commit_taxonomy(SE.delete_taxonomy_node(taxonomy, cur_cat, cur_node))
                        st.rerun()

                cur_node = s.get("_tax_node")
                node = next((n for n in nodes if n["name"] == cur_node), None)
                if node:
                    shape_tag = "list(只 includes)" if node["is_list"] else "dict(includes+description)"
                    st.markdown(f"##### 节点:**{cur_node}**({shape_tag})")
                    st.text_input("重命名(留空不改)", key="_tax_newname", placeholder=cur_node)
                    st.text_area("includes(每行一个,聚合时展开的子项)",
                                 value="\n".join(node["includes"]), key="_tax_includes", height=90)
                    if not node["is_list"]:
                        st.text_input("description", value=node["description"], key="_tax_desc")
                    if s.get("_tax_apply_err"):
                        st.error(s["_tax_apply_err"])
                    st.button("✓ 应用此节点", type="primary",
                              on_click=_on_apply_node, args=(cur_cat, cur_node))

    # ---- 指标别名(口语⇄标准,标准=指标下拉)----
    with sub_ma:
        _render_lint(SE.lint_synonyms(synonyms or {}, metrics))
        pairs = SE.metric_alias_pairs(synonyms or {})
        st.markdown(f"**现有 {len(pairs)} 条** metric_aliases(标准侧来自指标字典)")
        for k, v in pairs:
            mc1, mc2 = st.columns([8, 1])
            mc1.markdown(f"`{k}` → **{v}**")
            if mc2.button("✕", key=f"_ma_del_{k}_{v}"):
                _commit_synonyms(SE.with_metric_aliases_edited(
                    synonyms or {}, [(kk, vv) for kk, vv in pairs if not (kk == k and vv == v)]))
                st.rerun()
        st.divider()
        st.markdown("**添加一条**")
        ac1, ac2, ac3 = st.columns([3, 3, 1])
        with ac1:
            st.text_input("口语说法", key="_ma_newk")
        with ac2:
            _pick("→ 标准指标", list(metrics.keys()), "_ma_newv")
        with ac3:
            st.write(" ")
            if st.button("➕ 添加", use_container_width=True, key="_ma_addbtn"):
                k = (s.get("_ma_newk") or "").strip()
                v = s.get("_ma_newv") or ""
                if k and v:
                    _commit_synonyms(SE.with_metric_aliases_edited(
                        synonyms or {}, pairs + [(k, v)]))
                    st.toast(f"已添加「{k}」→「{v}」", icon="✅")
                    st.rerun()

    # ---- 实体 ----
    with sub_ent:
        rows = SE.entity_rows(synonyms or {})
        st.markdown(f"**现有 {len(rows)} 个** entities(resolve_entity 用于实体消歧)")
        for r in rows:
            ec1, ec2 = st.columns([9, 1])
            ec1.markdown(f"**{r['name']}** · 别名:{' / '.join(r['aliases']) or '(无)'}"
                         + (f" · {r['means']}" if r["means"] else ""))
            if ec2.button("✕", key=f"_ent_del_{r['name']}"):
                _commit_synonyms(SE.with_entities_edited(
                    synonyms or {}, [rr for rr in rows if rr["name"] != r["name"]]))
                st.rerun()
        st.divider()
        st.markdown("**添加实体**")
        ec1, ec2 = st.columns([4, 1])
        with ec1:
            st.text_input("实体名", key="_ent_newname")
            st.text_input("别名(逗号或空格分隔)", key="_ent_newaliases")
            st.text_input("means(口径说明,可选)", key="_ent_newmeans")
        with ec2:
            st.write(" ")
            if st.button("➕ 添加", use_container_width=True, key="_ent_addbtn"):
                nm = (s.get("_ent_newname") or "").strip()
                if nm:
                    import re as _re
                    als = [a for a in _re.split(r"[,，\s]+", s.get("_ent_newaliases") or "") if a]
                    _commit_synonyms(SE.with_entities_edited(
                        synonyms or {}, rows + [{"name": nm, "aliases": als,
                                                 "means": (s.get("_ent_newmeans") or "").strip()}]))
                    st.toast(f"已添加实体「{nm}」", icon="✅")
                    st.rerun()

    # ---- 时间·量词(kv)----
    with sub_kv:
        for section, label in [("time_aliases", "时间别名"), ("quantity_aliases", "量词别名")]:
            with st.expander(f"{label} · {section}"):
                pairs = SE.kv_pairs(synonyms or {}, section)
                for k, v in pairs:
                    kc1, kc2 = st.columns([9, 1])
                    kc1.markdown(f"`{k}` → `{v}`")
                    if kc2.button("✕", key=f"_kv_del_{section}_{k}_{v}"):
                        _commit_synonyms(SE.with_kv_edited(
                            synonyms or {}, section,
                            [(kk, vv) for kk, vv in pairs if not (kk == k and vv == v)]))
                        st.rerun()
                kc1, kc2, kc3 = st.columns([3, 3, 1])
                with kc1:
                    st.text_input("口语", key=f"_kv_newk_{section}")
                with kc2:
                    st.text_input("标准码", key=f"_kv_newv_{section}",
                                  help="如 latest_year / recent_3_years / total / cumulative")
                with kc3:
                    st.write(" ")
                    if st.button("➕ 添加", key=f"_kv_add_{section}", use_container_width=True):
                        k = (s.get(f"_kv_newk_{section}") or "").strip()
                        v = (s.get(f"_kv_newv_{section}") or "").strip()
                        if k and v:
                            _commit_synonyms(SE.with_kv_edited(
                                synonyms or {}, section, pairs + [(k, v)]))
                            st.rerun()

# ------------------------------------------------ 业务规则 tab —— Phase C2/C3
with tab_rules:
    rules = SE.load_metrics(sem[task.id]["texts"]["rules.yaml"])   # 复用 dict 解析
    _render_lint(SE.lint_rules(rules or {}))
    st.caption("⚠ 引擎消费 `recent_years.windows` 与 `recent_years.cagr_initial_rule`(近 N 年/CAGR 期初);"
               "改动后请核对。手编全文请到「📝 YAML 原文」tab。")

    # C3:规则速查(只读,"防哪类问题")
    st.subheader("📋 规则速查(每条防的是哪类问题)")
    if rules:
        for key, desc in SE.rules_summary(rules):
            with st.expander(f"**{key}**" + (f" — {desc[:40]}…" if len(desc) > 40 else
                              (f" — {desc}" if desc else ""))):
                st.json(rules.get(key))
    else:
        st.caption("(rules.yaml 为空或解析失败)")

    # C2:🔮 自然语言起草(LLM)
    st.divider()
    st.subheader(f"{U.ICON_LLM} 自然语言起草规则")
    llm = get_default()
    if not llm.available:
        st.info(llm.status())
    st.caption("用一句话描述要加/改的规则,LLM 起草 rules.yaml;过 YAML 合法性闸门才写入。"
               "semantic 无真实数值,可安全发给 LLM。")
    nl = st.text_input("指令", key="_rules_nl",
                       placeholder="如:加一条规则——月度列相加会重复计算(YTD)")
    if st.button("🔮 让 LLM 起草", disabled=not (nl and llm.available)):
        with st.spinner(f"{U.ICON_LLM} LLM 起草中(约 1 分钟)..."):
            client = llm_client.get_default()
            sys_p = ("你是业务规则(rules.yaml)编辑助手。按用户指令修改 YAML,"
                     "只输出完整 rules.yaml,不要解释。必须保留 recent_years.windows 与"
                     " recent_years.cagr_initial_rule 结构(引擎消费)。不要任何真实数值。")
            usr = (f"当前 rules.yaml:\n```yaml\n{sem[task.id]['texts']['rules.yaml']}\n```\n\n"
                   f"指令:{nl}")
            try:
                out_y = _strip_fences(client.chat(sys_p, usr, json_mode=False, timeout=120))
                parsed = yaml.safe_load(out_y)
                if not isinstance(parsed, dict):
                    st.error("LLM 输出顶层非 dict,未写入。")
                else:
                    warns = SE.lint_rules(parsed)
                    _commit("rules.yaml", out_y)
                    msg = "已应用 LLM 起草的规则(已热重载)。"
                    if warns:
                        msg += " ⚠ recent_years 结构可能不完整,请核对。"
                    st.success(msg)
                    st.rerun()
            except Exception as e:
                st.error(f"起草失败:{e}")

# ------------------------------------------------ YAML 原文(4 文件)tab —— 逃生口
with tab_yaml:
    sub = st.tabs(SEM_FILES)
    for fn, t in zip(SEM_FILES, sub):
        with t:
            st.text_area(fn, sem[task.id]["texts"][fn], height=420,
                         label_visibility="collapsed", key=f"sem_{task.id}_{fn}")

    c1, _ = st.columns([1, 3])
    with c1:
        if st.button(f"{U.ICON_DET} 应用(热重载)"):
            def _write_all():
                for fn in SEM_FILES:
                    new_text = st.session_state[f"sem_{task.id}_{fn}"]
                    TS.write_task_semantic(task.id, fn, new_text)
                    sem[task.id]["texts"][fn] = new_text
                sem[task.id]["dirty"] = False
                U.reload_current_semantic()
                U.invalidate_grid()
            U.gated_write(task.id, "语义层·YAML 原文", _write_all)
            st.success("已热重载当前任务的 semantic")
            st.rerun()

    st.caption(
        "状态: " + ("脏(编辑未应用)" if any(
            st.session_state.get(f"sem_{task.id}_{fn}") != sem[task.id]["texts"][fn]
            for fn in SEM_FILES) else "已应用")
    )

# ------------------------------------------------ resolve 试用(跨 tab 共享)
st.divider()
st.subheader(f"{U.ICON_DET} resolve 试用")
trial = st.text_input("输入一段文本(看实体/指标消歧)", "公司2018年的利润总额")
if trial:
    c1, c2, c3 = st.columns(3)
    c1.write(f"**resolve_entity**\n`{S.resolve_entity(trial)}`")
    c2.write(f"**resolve_metric**\n`{S.resolve_metric(trial)}`")
    c3.write(f"**resolve_metrics**\n`{S.resolve_metrics(trial)}`")
    m = S.resolve_metric(trial)
    if m:
        info = S.metric_info(m)
        st.json({"metric": m, "unit": info.get("unit"), "locator": info.get("locator"),
                 "taxonomy_node": info.get("taxonomy_node")})

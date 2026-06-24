"""工作台 · Schema 编辑:可视化编辑(按内容点选 + target 门控 + 预检高亮 + 高级 widget + 智能填充)+ YAML 原文(LLM 修复 + 自然语言改动)。

两种编辑面共享同一份磁盘 schema(任务自包含):
- 🖼 可视化编辑:
    · 实时 Excel 预览(带行列标尺);预检失败时 Styler 高亮对应行/列。
    · target 选择器在表单外(切换即按 target 门控显示相关字段)。
    · 核心字段"按内容点选"下拉;高级字段(subtotal_rules/classifier/skip_labels)文本区 widget。
    · ✨ 智能填充(基于预览的启发式建议,需确认);应用前预检(普通话报错)。
- 📝 YAML 原文:原编辑器 + 即时校验 + LLM 修复 + 🔮 自然语言改动 + 导出(进阶/LLM 逃生口)。

后端(schema_spec/validate/loader)零改动;纯逻辑在 schema_edit_helpers。
任务自包含:编辑只影响当前任务;保留「导出下载」;不再写回全局 committed。
"""
from __future__ import annotations
import contextlib
import io
import os
import yaml
import pandas as pd
import streamlit as st

import ui_common as U  # noqa: E402
import task_store as TS  # noqa: E402
import schema_spec as SS  # noqa: E402
import validate as V  # noqa: E402
import schema_edit_helpers as SE  # noqa: E402
import loader  # noqa: E402  (构造预览:实时跑 load_table)
import llm_client  # noqa: E402
from llm_parser import get_default  # noqa: E402

st.set_page_config(page_title="Schema 编辑", page_icon="📝", layout="wide")
st.title("📝 Schema 编辑")
st.caption(f"{U.ICON_DET} 可视化按内容点选 / YAML 原文　实时校验　{U.ICON_LLM} LLM 修复 + 自然语言　写入当前任务")

# 任务上下文
U.render_task_sidebar()
task = U.current_task()
if task is None:
    st.info("暂无任务。请到「📤 数据接入」创建。")
    st.stop()

spec_path = task.schema_path
excel = task.excel_path

# ---- 加载当前任务 schema 文本(磁盘为唯一真相源,两个 tab 都从这里读)----
default_text = ""
if os.path.exists(spec_path):
    with open(spec_path, encoding="utf-8") as f:
        default_text = f.read()

st.caption(f"当前任务:**{task.name}**　编辑 `{os.path.basename(spec_path)}`")

# 8.2 回归闸门:若上一次写盘被守护,顶部显示前后 diff
U.render_corpus_gate(task.id)

TARGETS = ["row_map", "gen_detail", "gen_subtotals"]

# ============================================================ page-local IO / 渲染 helper
def _read_grid(excel_path: str, sheet_name: str, nrows: int):
    """读 Excel 指定 sheet 前 nrows 行 → (字符串化 Grid, 行数, 列数, 原始 df)。

    返回原始 df 供构造预览跑 loader.load_table(保留真实值:datetime 表头可归一化为
    'YYYY-MM'、带千分位数字可 float);字符串化 grid 供显示预览定位行列。失败抛异常由调用方兜底。
    """
    engine = "openpyxl" if excel_path.lower().endswith(".xlsx") else "xlrd"
    raw_df = pd.read_excel(excel_path, sheet_name=sheet_name, engine=engine,
                           header=None, nrows=nrows)
    disp_df = raw_df.fillna("")
    grid = [[str(v) for v in row] for row in disp_df.values.tolist()]
    return grid, len(grid), int(raw_df.shape[1]), raw_df


def _preview_df(grid: list[list[str]], n_cols: int) -> pd.DataFrame:
    """带行列标尺的预览 DataFrame:r00 第1行 / c01 B列。"""
    cols = [f"c{j:02d} {SE.col_letter(j)}" for j in range(n_cols)]
    idx = [f"r{i:02d} 第{i+1}行" for i in range(len(grid))]
    rows = [(r + [""] * (n_cols - len(r)))[:n_cols] for r in grid]
    return pd.DataFrame(rows, columns=cols, index=idx)


def _styled_preview(grid, n_cols, highlights) -> pd.DataFrame | pd.io.formats.style.Styler:
    """高亮指定行/列的预览(预检失败时指给用户看问题位置)。"""
    df = _preview_df(grid, n_cols)
    if not highlights:
        return df
    rows = {i for kind, i in highlights if kind == "row" and 0 <= i < len(df)}
    cols = {i for kind, i in highlights if kind == "col" and 0 <= i < len(df.columns)}

    def _css(d: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame("", index=d.index, columns=d.columns)
        for r in range(len(d)):
            for c in range(len(d.columns)):
                if r in rows or c in cols:
                    out.iloc[r, c] = "background-color: #ffe08a"
        return out

    return df.style.apply(_css, axis=None)


def _clamp(v, lo: int, hi: int) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def _valid_target(t):
    return t if t in TARGETS else "row_map"


def _apply_schema(text: str, label: str) -> None:
    """写 schema 到当前任务 + 标 manual + 失效 grid,经语料回归闸门(前后双跑模板句)。

    语料空则零开销直写。闸门 diff 存 session,顶部 render_corpus_gate 显示。
    """
    def _write():
        TS.write_task_schema(task.id, text)
        TS.set_schema_source(task.id, "manual")
        U.invalidate_grid()
    U.gated_write(task.id, label, _write)


# ---------------------------------------------------------------- table 增删 callback
# 这些 callback 在 widget 重新实例化**之前**被 streamlit 调用,可在内部安全修改
# _viz_table / _confirm_del 等 widget key(直接 st.session_state[k]=v 会抛 "cannot be
# modified after the widget is instantiated" 锁定错)。callback 完成后 streamlit 自动 rerun,
# 不需要手动 st.rerun()。
#
# ⚠ callback 内部不依赖页面局部变量(闭包陷阱),改为从磁盘读最新 yaml(已 _apply_schema 写过的)。
# 这样即使 callback 跨轮,拿到的也是最新状态。
def _on_add_table(spec_path: str, si: int, current_tables_count: int) -> None:
    """在 si sheet 末尾追加一张默认禁用的占位表,切到新表。"""
    with open(spec_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    new_table = {
        "name": f"新表 {current_tables_count + 1}",
        "target": "row_map",
        "header_row": 0,
        "first_data_row": 1,
        "label_col_idx": 0,
        "enabled": False,  # 默认占位,避免立即进 Grid 派发
    }
    new_raw = SE.with_table_added(raw, si, new_table)
    _apply_schema(SE.dump_workbook_yaml(new_raw), "Schema·新增表")
    st.session_state["_viz_table"] = current_tables_count  # 切到新表(callback 内安全)
    st.session_state.pop("_viz_last_table", None)  # 强制下次 seed 新表


def _on_confirm_delete(spec_path: str, si: int, ti: int) -> None:
    """删除 si sheet 的 ti 表,clamp _viz_table 到合法范围。"""
    with open(spec_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    new_raw = SE.with_table_removed(raw, si, ti)
    _apply_schema(SE.dump_workbook_yaml(new_raw), "Schema·删除表")
    # clamp _viz_table 到合法范围(删后新长度可能 < ti)
    remaining = new_raw["sheets"][si].get("tables") or []
    st.session_state["_viz_table"] = max(0, min(ti, len(remaining) - 1))
    st.session_state.pop("_confirm_del", None)
    st.session_state.pop("_viz_last_table", None)  # 强制下次 seed


def _seed_core_keys(table: dict, n_rows: int, n_cols: int) -> None:
    """切表时把核心/高级字段的 viz_* session 键从该表磁盘值灌入(keyed 表单据此显示)。"""
    s = st.session_state
    s["_viz_name"] = table.get("name", "")
    s["_viz_hdr"] = _clamp(table.get("header_row", 0), 0, max(n_rows - 1, 0))
    s["_viz_fdr"] = _clamp(table.get("first_data_row", s["_viz_hdr"] + 1), 0, max(n_rows - 1, 0))
    ldr = table.get("last_data_row")
    s["_viz_ldr"] = ldr if (isinstance(ldr, int) and 0 <= ldr < n_rows) else SE.UNSET
    s["_viz_lci"] = _clamp(table.get("label_col_idx", 0), 0, max(n_cols - 1, 0))
    dcs = table.get("data_col_start")
    s["_viz_dcs"] = dcs if (isinstance(dcs, int) and 0 <= dcs < n_cols) else SE.UNSET
    # dce 合法显式值与 col_opts (range(n_cols) + [UNSET]) 一致:
    # 0..n_cols-1 是显式,UNSET(-1) 是"到表底"。UI INCLUSIVE 显示:
    # YAML EXCLUSIVE → INCLUSIVE(-1),边界 0 / n_cols → UNSET(空范围 / 到末尾)。
    dce = table.get("data_col_end")
    s["_viz_dce"] = SE._yaml_to_viz_dce(dce, n_cols)
    dmc = table.get("detail_marker_col_idx")
    s["_viz_dmc"] = dmc if (isinstance(dmc, int) and 0 <= dmc < n_cols) else SE.UNSET
    s["_viz_subrules"] = SE.format_subtotal_rules(table.get("subtotal_rules", []))
    s["_viz_classifiers"] = SE.format_classifiers(table.get("detail_classifier_cols", {}))
    s["_viz_skiplabels"] = SE.format_skip_labels(table.get("skip_labels", []))
    s["_viz_skipregex"] = table.get("skip_label_regex", "") or ""
    s["_viz_enabled"] = table.get("enabled", True)


def _clamp_viz_keys(n_rows: int, n_cols: int) -> None:
    """wrapper:把当前 session_state 的 viz 键 clamp 到合法域(委派给 SE.clamp_viz_keys)。"""
    s = st.session_state
    if not hasattr(s, "_viz_target"):
        return  # 还没进入 viz 表单,无 viz 键
    # 逐项写回 streamlit session_state(它不是普通 dict,需显式赋值)
    d = {k: s[k] for k in (
        "_viz_hdr", "_viz_fdr", "_viz_ldr",
        "_viz_lci", "_viz_dcs", "_viz_dmc", "_viz_dce",
    ) if k in s}
    SE.clamp_viz_keys(d, n_rows, n_cols)
    for k, v in d.items():
        if s.get(k) != v:
            s[k] = v


def _collect_form_vals(target: str, n_cols: int) -> dict:
    """从当前 viz_* session_state 收集成 with_table_edited 的 values 字典。

    构造预览(每次 rerun)与"应用"(点按钮)共用此函数 → 两者看到完全一致的表单值,
    预览即"应用后 loader 结果"的镜像。依赖 viz 键已被 _seed_core_keys 灌入。
    """
    s = st.session_state
    vals = {
        "target": target,
        "name": s["_viz_name"],
        "header_row": s["_viz_hdr"], "first_data_row": s["_viz_fdr"],
        "last_data_row": s["_viz_ldr"],
        "data_col_start": s["_viz_dcs"], "data_col_end": s["_viz_dce"],
        "enabled": s["_viz_enabled"],
    }
    if target in ("row_map", "gen_subtotals"):
        vals["label_col_idx"] = s["_viz_lci"]
    if target in ("gen_detail", "gen_subtotals"):
        vals["detail_marker_col_idx"] = s["_viz_dmc"]
    if target == "gen_subtotals":
        vals["subtotal_rules"] = SE.parse_subtotal_rules(s["_viz_subrules"])
    if target == "gen_detail":
        vals["detail_classifier_cols"] = SE.parse_classifiers(s["_viz_classifiers"], n_cols)
    if target == "row_map":
        vals["skip_labels"] = SE.parse_skip_labels(s["_viz_skiplabels"])
        vals["skip_label_regex"] = s["_viz_skipregex"]
    return vals


# ============================================================ 双 tab
tab_viz, tab_yaml = st.tabs(["🖼 可视化编辑", "📝 YAML 原文"])

# ------------------------------------------------ YAML 原文 tab(逃生口 + LLM 修复 + 自然语言改动)
with tab_yaml:
    col_edit, col_check = st.columns([3, 2])
    with col_edit:
        st.markdown(f"#### {U.ICON_DET} YAML 编辑器")
        text = st.text_area("schema_yaml", default_text, height=520, label_visibility="collapsed")
    with col_check:
        st.markdown(f"#### {U.ICON_DET} 实时校验")
        errs: list[str] = []
        try:
            data = yaml.safe_load(text)
            spec = SS.workbook_from_dict(data)
            errs = V.validate(spec, excel, spec_path=spec_path)
            if errs:
                st.error(f"{len(errs)} 个问题:")
                for e in errs:
                    st.write(f"- {e}")
            else:
                st.success("校验全通过(V1-V10)")
            st.caption(f"对照 Excel: {excel}")
        except Exception as e:
            st.error(f"YAML 解析失败: {e}")

        # ---- LLM 修复单条 ----
        st.markdown(f"#### {U.ICON_LLM} LLM 修复")
        llm = get_default()
        if not llm.available:
            st.info(llm.status())
        fix_options = errs if errs else ["(无错误)"]
        st.selectbox("选择一条错误让 LLM 修复", fix_options, disabled=not errs, key="_fix_sel")
        if errs and llm.available and st.button("LLM 修复此条", disabled=not errs):
            fix_idx = st.session_state["_fix_sel"]
            with st.spinner(f"{U.ICON_LLM} LLM 修复中..."):
                client = llm_client.get_default()
                sys_p = ("你是 schema 修复助手。根据错误信息修正 YAML,"
                         "只输出修正后的完整 YAML,不要任何解释。")
                usr = (f"当前 YAML:\n```yaml\n{text}\n```\n\n"
                       f"要修复的错误:\n{fix_idx}\n\n保持其余结构不变,只修问题处。")
                try:
                    fixed = client.chat(sys_p, usr, json_mode=False)
                    t = fixed.strip()
                    if t.startswith("```yaml"):
                        t = t[7:]
                    elif t.startswith("```"):
                        t = t[3:]
                    if t.endswith("```"):
                        t = t[:-3]
                    t = t.strip()
                    _apply_schema(t, "Schema·LLM 修复")
                    st.success("已应用 LLM 修复到当前任务。")
                    st.rerun()
                except Exception as e:
                    st.error(f"修复失败: {e}")

        # ---- 自然语言改动(C3)----
        st.markdown(f"#### {U.ICON_LLM} 自然语言改动")
        st.caption("用一句话描述改动,如「把财务主表的表头改到第 3 行」「年度小计数据列终止改到 12」。"
                   "LLM 改完会过 validate 闸门,通过才写入。")
        nl = st.text_input("自然语言指令", key="_nl_edit", placeholder="把 … 的 … 改到 …")
        if st.button("让 LLM 改 YAML", disabled=not (nl and llm.available), key="_nl_btn"):
            with st.spinner(f"{U.ICON_LLM} LLM 改写中(约 1 分钟)..."):
                client = llm_client.get_default()
                sys_p = ("你是 schema 编辑助手。按用户的自然语言指令修改 YAML,"
                         "只输出修改后的完整 YAML,不要任何解释,不要真实数值。")
                usr = f"当前 YAML:\n```yaml\n{default_text}\n```\n\n指令:{nl}"
                try:
                    out_y = client.chat(sys_p, usr, json_mode=False, timeout=120)
                    c = out_y.strip()
                    if c.startswith("```yaml"):
                        c = c[7:]
                    elif c.startswith("```"):
                        c = c[3:]
                    if c.endswith("```"):
                        c = c[:-3]
                    c = c.strip()
                    spec = SS.workbook_from_dict(yaml.safe_load(c))
                    verrs = V.validate(spec, excel, spec_path=spec_path)
                    if verrs:
                        st.error(f"LLM 改完后校验不通过({len(verrs)} 个),未写入:")
                        for e in verrs:
                            st.write(f"- {e}")
                    else:
                        _apply_schema(c, "Schema·自然语言改动")
                        st.success("已应用 LLM 自然语言改动,校验通过。")
                        st.rerun()
                except Exception as e:
                    st.error(f"自然语言改动失败: {e}")

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if st.button(f"{U.ICON_DET} 应用到当前任务 Grid"):
            _apply_schema(text, "Schema·YAML 原文")
            st.success("已写入当前任务,去「Grid 检视」查看。")
    with c2:
        st.download_button("⬇ 导出 YAML", text, file_name="schema.yaml", mime="text/yaml")

# ------------------------------------------------ 可视化编辑 tab
with tab_viz:
    # ---- 顶部反馈:应用成功 / 预检失败 ----
    if st.session_state.pop("_just_viz_applied", False):
        try:
            spec = SS.workbook_from_dict(yaml.safe_load(default_text))
            verrs = V.validate(spec, excel, spec_path=spec_path)
            if verrs:
                st.warning(f"✓ 已应用可视化改动,但校验发现 {len(verrs)} 个问题:")
                for e in verrs:
                    st.write(f"- {e}")
            else:
                st.success(f"✓ 已应用可视化改动,校验全通过(V1-V10)。")
        except Exception as e:
            st.error(f"✓ 已应用,但校验失败: {e}")
    for m in (st.session_state.get("_viz_preflight") or []):
        st.error(m)

    # ---- 解析当前 schema ----
    try:
        raw = yaml.safe_load(default_text)
    except Exception as e:
        st.error(f"当前 YAML 解析失败,无法可视化:{e}。请先在「YAML 原文」tab 修正。")
        st.stop()
    if not isinstance(raw, dict) or not raw.get("sheets"):
        st.warning("当前 schema 没有 sheets 结构,无法可视化。请在「YAML 原文」tab 编辑或用 LLM 生成。")
        st.stop()

    sheet_names = [s.get("name", f"<未命名#{i}>") for i, s in enumerate(raw["sheets"])]
    si = st.selectbox("选择 Sheet", range(len(sheet_names)),
                      format_func=lambda i: sheet_names[i], key="_viz_sheet")
    tables = raw["sheets"][si].get("tables") or []
    if not tables:
        st.info(f"Sheet「{sheet_names[si]}」没有 table 定义。请在「YAML 原文」tab 添加。")
        st.stop()
    if st.session_state.get("_viz_table", 0) >= len(tables):
        st.session_state["_viz_table"] = 0
    ti = st.selectbox("选择 表", range(len(tables)),
                      format_func=lambda i: tables[i].get("name", f"table#{i}"),
                      key="_viz_table")
    table = tables[ti]
    table_name = table.get("name", f"table#{ti}")
    st.caption(f"正在编辑:**{sheet_names[si]} / {table_name}**")

    # ---- 增/删 table 入口(独立于表单,即时生效)----
    # 二次确认状态机:get("_confirm_del") 决定渲染哪套按钮;
    # 增/删都走整包 schema 重写,与可视化编辑同一条 _apply_schema 路径,经 gated_write 回归闸门。
    #
    # ⚠ 关键:所有改 widget key(_viz_table / _confirm_del)的操作必须放在 on_click=callback 里。
    # streamlit 规定:widget 实例化后,直接 st.session_state[k] = v 会抛 "cannot be modified after
    # the widget is instantiated" 锁定错。callback 在 widget 重新渲染**前**执行,可安全改 key,
    # 且脚本重跑后 selectbox 用新值实例化。
    if st.session_state.get("_confirm_del"):
        # 确认态:显示警告 + 确认/取消,不渲染下面的 viz 表单
        st.warning(
            f"⚠ 确认删除「{sheet_names[si]} / {table_name}」?此操作不可撤销。"
        )
        cdl1, cdl2, _ = st.columns([1, 1, 3])
        with cdl1:
            st.button("✓ 确认删除", type="primary",
                      on_click=_on_confirm_delete, args=(spec_path, si, ti))
        with cdl2:
            if st.button("取消"):
                st.session_state.pop("_confirm_del", None)
                st.rerun()
        st.stop()
    else:
        # 正常态:显示增/删按钮 + 当前表状态摘要
        cadd, cdel, cstatus = st.columns([1, 1, 2])
        with cadd:
            st.button("➕ 新增表",
                      help="在当前 sheet 末尾追加一张新表(默认禁用,占位不污染 Grid)",
                      on_click=_on_add_table, args=(spec_path, si, len(tables)))
        with cdel:
            if st.button("🗑 删除此表", help="删除当前选中的表(走二次确认)"):
                st.session_state["_confirm_del"] = True
                st.rerun()
        with cstatus:
            target = table.get("target", "?")
            enabled = table.get("enabled", True)
            status_icon = "✓ 启用" if enabled else "○ 禁用(占位)"
            st.caption(f"状态: **{status_icon}** · target: `{target}` · 共 {len(tables)} 张表")

    # 切表检测:灌入 viz_* 键 + 清预检/高亮
    changed = st.session_state.get("_viz_last_table") != (si, ti)
    if changed:
        st.session_state["_viz_target"] = _valid_target(table.get("target", "row_map"))
        st.session_state.pop("_viz_preflight", None)
        st.session_state.pop("_viz_highlight", None)

    # ---- 实时预览(真实数值,仅供定位;不发 LLM)----
    st.markdown(f"#### 实时预览:{sheet_names[si]}")
    st.caption("⚠ 本预览含真实数值,仅供你**定位行列**;不会发给 LLM(LLM 只收无数值的 schema)。"
               "黄色高亮 = 预检发现的问题行/列。")
    grid = None
    n_rows = n_cols = 0
    if not os.path.exists(excel):
        st.error(f"任务 Excel 缺失:{excel}")
    else:
        try:
            # 读全表(封顶 200 行)→ 选项范围覆盖真实坐标;slider 只控制下方"显示"窗口
            grid, n_rows, n_cols, raw_df = _read_grid(excel, sheet_names[si], 200)
        except Exception as e:
            st.error(f"读取 Excel sheet「{sheet_names[si]}」失败:{e}。"
                     "可能 schema 的 sheet 名与 Excel 实际不符——请先核对。")
        if grid is not None and (n_rows == 0 or n_cols == 0):
            st.warning("该 sheet 预览为空,无法点选。")
            grid = None

    # 切表时灌入核心键(需 grid 范围);标记已处理
    if grid is not None and changed:
        _seed_core_keys(table, n_rows, n_cols)
        st.session_state["_viz_last_table"] = (si, ti)
    elif grid is not None and st.session_state.get("_viz_last_table") is None:
        _seed_core_keys(table, n_rows, n_cols)
        st.session_state["_viz_last_table"] = (si, ti)

    # ---- 预览渲染(带高亮;slider 只控制显示窗口)----
    if grid is not None:
        highlights = st.session_state.get("_viz_highlight") or []
        disp_max = min(60, n_rows)
        disp_n = st.slider("预览显示行数", 5, disp_max, min(25, disp_max), key="_viz_nrows")
        st.dataframe(_styled_preview(grid[:disp_n], n_cols, highlights),
                     use_container_width=True, height=320)

        # ---- target 选择器(表单外,切换即门控)+ 智能填充(C2)----
        tc1, tc2 = st.columns([3, 2])
        with tc1:
            st.selectbox(
                "target(表类型)——切换后下方只显示该类型相关字段",
                TARGETS, key="_viz_target", format_func=lambda t: t,
                help="row_map=扁平键值表;gen_detail=明细行;gen_subtotals=小计行",
            )
        with tc2:
            if st.button("✨ 智能填充(基于预览的建议,需确认)"):
                tgt = st.session_state.get("_viz_target", "row_map")
                sug = SE.suggest_fields(grid, n_rows, n_cols, target=tgt)
                # 智能填充建议可能越界(n_rows/n_cols 是当前 sheet 的边界),
                # 在灌入 session_state 前 clamp,避免后续 selectbox 抛 "X is not in iterable"
                row_max = max(n_rows - 1, 0)
                col_max = max(n_cols - 1, 0)
                # (sug_key, session_key, 中文标签, kind):
                #   kind="row" → clamp 上界用 row_max、显示走 rfmt;kind="col" → col_max、cfmt。
                #   data_col_end 特殊:sug 是 EXCLUSIVE,UI 显示 INCLUSIVE,需 -1 再 clamp。
                _SF = [
                    ("header_row", "_viz_hdr", "表头行", "row"),
                    ("first_data_row", "_viz_fdr", "首行数据", "row"),
                    ("label_col_idx", "_viz_lci", "标签列", "col"),
                    ("data_col_start", "_viz_dcs", "数据起始列", "col"),
                    ("data_col_end", "_viz_dce", "数据终止列", "col"),
                    ("detail_marker_col_idx", "_viz_dmc", "明细判别列", "col"),
                ]
                s = st.session_state
                # changed: (label, old_raw, new_raw, kind) —— rerun 后用 rfmt/cfmt 还原成
                # 与 selectbox 一致的文案,让用户一眼看到"改了什么"。
                changed = []
                unchanged = 0
                for sug_k, sk, label, kind in _SF:
                    if sug_k not in sug:
                        continue  # 该 target 下 suggest_fields 没给这个字段
                    hi = row_max if kind == "row" else col_max
                    raw = int(sug[sug_k]) - 1 if sug_k == "data_col_end" else int(sug[sug_k])
                    new_v = _clamp(raw, 0, hi)
                    old_v = s.get(sk)
                    s[sk] = new_v
                    if old_v != new_v:
                        changed.append((label, old_v, new_v, kind))
                    else:
                        unchanged += 1
                st.session_state.pop("_viz_preflight", None)
                st.session_state.pop("_viz_highlight", None)
                # 记录反馈,rerun 后展示一次(见下方 _viz_smartfill 渲染块)。
                # 解决"点了没反应"的观感:即便建议=现状,也会明确提示"无变更"。
                st.session_state["_viz_smartfill"] = {"changed": changed, "unchanged": unchanged}
                st.rerun()

        target = st.session_state.get("_viz_target", "row_map")
        row_opts = list(range(n_rows))
        col_opts = list(range(n_cols)) + [SE.UNSET]
        # 无条件 clamp viz 键 → 防 stale session_state 越界(selectbox 抛 "X is not in iterable")
        _clamp_viz_keys(n_rows, n_cols)
        hdr = st.session_state.get("_viz_hdr", 0)
        rfmt = lambda i: "末尾(默认到表底)" if i == SE.UNSET else SE.row_option_label(i, grid)
        cfmt = lambda i: ("未指定" if i == SE.UNSET
                          else SE.col_option_label(i, grid, label_row=hdr))

        # ---- 智能填充反馈(点击后展示一次)----
        # 按钮把 diff 存进 _viz_smartfill 后 rerun;这里 pop 出来渲染一次即焚,
        # 下一轮 rerun(任意 widget 改动)即消失。值文案复用 rfmt/cfmt,与 selectbox 完全一致。
        sf = st.session_state.pop("_viz_smartfill", None)
        if sf is not None:
            def _fv(val, kind):
                """raw 值 → 与 selectbox 一致的显示文案(行/列分别走 rfmt/cfmt 逻辑)。"""
                if val is None:
                    return "未设置"
                if val == SE.UNSET:
                    return "末尾(默认到表底)" if kind == "row" else "未指定"
                return (SE.row_option_label(val, grid) if kind == "row"
                        else SE.col_option_label(val, grid, label_row=hdr))
            if sf["changed"]:
                parts = [f"**{label}**:{_fv(o, k)} → {_fv(n, k)}"
                         for label, o, n, k in sf["changed"]]
                body = "\n".join(
                    [f"✨ 智能填充完成 · 变更 {len(sf['changed'])} 项"
                     "(已填入表单,未落盘——确认无误后点下方「应用」):"]
                    + [f"- {p}" for p in parts]
                )
                st.success(body)
                if sf["unchanged"]:
                    st.caption(f"另有 {sf['unchanged']} 项建议与现状一致,保持不变。")
            else:
                st.info(
                    f"✨ 智能填充:启发式建议与当前取值一致,无变更(共 {sf['unchanged']} 项)。"
                    "若实际配置有误,可手动改字段或换个 target 重试。"
                )

        # ---- 通用字段 + 高级字段(裸 widget,不在 st.form 内)----
        # 为什么不用 form:实时构造预览需要每次 rerun 都拿到当前 widget 值,而 form 内 widget
        # 值必须点提交才进 session_state。裸 widget 每次改动即 rerun、即刷新预览。
        # (target 选择器、智能填充早已是裸 widget,此处与之一致。)
        st.markdown("##### 通用字段")
        st.checkbox("启用此表", key="_viz_enabled",
                    help="禁用后该表不会被 loader 处理(占位用)。新增表默认禁用,配置完成后勾选启用。")
        st.text_input("表名 name", key="_viz_name",
                      help="逻辑表名,用于日志/调试与表选择下拉框显示。改名需点下方「应用」落盘。")
        st.selectbox("表头行 header_row", row_opts, key="_viz_hdr", format_func=rfmt)
        st.selectbox("首行数据 first_data_row", row_opts, key="_viz_fdr", format_func=rfmt)
        st.selectbox("末行数据 last_data_row", row_opts + [SE.UNSET],
                     key="_viz_ldr", format_func=rfmt)

        if target in ("row_map", "gen_subtotals"):
            st.selectbox("标签列 label_col_idx", col_opts, key="_viz_lci", format_func=cfmt)
        if target in ("gen_detail", "gen_subtotals"):
            st.selectbox("明细判别列 detail_marker_col_idx", col_opts,
                         key="_viz_dmc", format_func=cfmt)
        st.selectbox("数据起始列 data_col_start", col_opts, key="_viz_dcs", format_func=cfmt)
        st.selectbox("数据终止列 data_col_end (INCLUSIVE,包含本列)",
                     col_opts, key="_viz_dce", format_func=cfmt,
                     help="选中的列本身也算数据列;不指定 = 到表底。"
                          "写入 YAML 时内部 +1 转 EXCLUSIVE(后端零改动)。")

        # ---- 高级字段 widget(C1,按 target 显示)----
        if target == "gen_subtotals":
            st.markdown("##### 小计规则(每行一条;**顺序即优先级,first-wins**)")
            st.caption("格式:`匹配子串 => 输出键`(空行/`#` 注释跳过)")
            st.text_area("subtotal_rules", key="_viz_subrules", height=120,
                         label_visibility="collapsed",
                         placeholder="合计 => 合计\n（一）巴西 => 巴西")
        if target == "gen_detail":
            st.markdown("##### 明细分类列(每行一条)")
            st.caption("格式:`维度名 => 列(字母如 E,或序号如 4)`")
            st.text_area("detail_classifier_cols", key="_viz_classifiers", height=100,
                         label_visibility="collapsed", placeholder="name => E\n方式 => C")
        if target == "row_map":
            st.markdown("##### 跳过标签(row_map 用)")
            st.caption("`skip_labels`:每行一个,精确匹配;`skip_label_regex`:可选正则")
            st.text_area("skip_labels", key="_viz_skiplabels", height=70,
                         label_visibility="collapsed", placeholder="备注")
            st.text_input("skip_label_regex(可选)", key="_viz_skipregex")

        # ---- 🔍 构造预览(实时:每次 rerun 用当前通用字段 + target 跑一遍 loader)----
        # 预览 = "应用后 loader 结果"的镜像:走同一条 with_table_edited→workbook_from_dict→
        # load_table 链,故与写盘后逐字节一致。本地显示真实数值,不发 LLM(沿用页面警告口径)。
        vals = _collect_form_vals(target, n_cols)
        with st.expander(f"🔍 构造预览 · target={target}(按当前设置实时解析;本地显示,不发 LLM)",
                         expanded=True):
            try:
                preview_raw = SE.with_table_edited(raw, si, ti, vals)
                with contextlib.redirect_stderr(io.StringIO()):  # 静默 load_table 的 duplicate-warn
                    preview_spec = SS.workbook_from_dict(preview_raw)
                    preview_table = preview_spec.sheets[si].tables[ti]
                    contribs = loader.load_table(raw_df, sheet_names[si], preview_table)
                    # 用 spec 推"数据范围全部 colkey"(按 idx 升序),让全空列也在 preview
                    # 里展示列头(单元格为空)— 否则 loader 只返非空 cell 的 colkey,用户看不到
                    # 自己设的最后一列。
                    expected_columns = loader.resolve_data_colkeys(preview_table, raw_df)
            except Exception as e:
                st.warning(f"当前设置无法构造预览:{e}")
                contribs = []
                expected_columns = []
            rows_pv, cols_pv, json_pv = SE.contributions_to_preview(
                contribs, target, expected_columns=expected_columns)
            if not contribs:
                st.info(
                    "当前设置下没有解析出任何行。检查:表头行 / 首行数据 / 数据列范围,以及 target "
                    "匹配规则(row_map 的 skip_labels、gen_subtotals 的 subtotal_rules 是否命中行标签、"
                    "gen_detail 的明细判别列是否非空)。"
                )
            else:
                st.caption(
                    f"已解析 {len(contribs)} 行(基于前 {n_rows} 行预览;"
                    "“末行数据=到表底”时按预览上限计)。"
                )
                st.dataframe(pd.DataFrame(rows_pv, columns=cols_pv).fillna(""),
                             use_container_width=True, height=260)
                st.caption("构造的 JSON(loader 产物结构):")
                st.json(json_pv)

        st.caption("⚠ 可视化应用会用标准格式重写 YAML(注释丢失);未在此编辑的字段保持不变。")
        if st.button("✓ 应用可视化改动到当前任务", type="primary"):
            s = st.session_state
            msgs, hl = SE.preflight_table(vals, n_cols)
            if msgs:
                s["_viz_preflight"] = msgs
                s["_viz_highlight"] = hl
                st.rerun()

            new_raw = SE.with_table_edited(raw, si, ti, vals)
            _apply_schema(SE.dump_workbook_yaml(new_raw), "Schema·可视化编辑")
            s.pop("_viz_preflight", None)
            s.pop("_viz_highlight", None)
            s["_just_viz_applied"] = True
            st.rerun()

        # ---- 仍未在可视化编辑的字段(只读摘要)----
        _EDITED = (set(SE.EDITABLE_KEYS) | {"name", "target", "subtotal_rules",
                   "detail_classifier_cols", "skip_labels", "skip_label_regex"})
        with st.expander("其他字段(只读;如需修改请用「YAML 原文」tab)"):
            rest = {k: v for k, v in table.items() if k not in _EDITED}
            if rest:
                st.code(yaml.safe_dump(rest, allow_unicode=True, sort_keys=False), language="yaml")
            else:
                st.caption("(无)")

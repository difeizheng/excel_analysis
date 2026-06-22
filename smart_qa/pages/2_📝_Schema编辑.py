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
import os
import yaml
import pandas as pd
import streamlit as st

import ui_common as U  # noqa: E402
import task_store as TS  # noqa: E402
import schema_spec as SS  # noqa: E402
import validate as V  # noqa: E402
import schema_edit_helpers as SE  # noqa: E402
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
    """读 Excel 指定 sheet 前 nrows 行 → 字符串化 Grid + 行列数。失败抛异常由调用方兜底。"""
    engine = "openpyxl" if excel_path.lower().endswith(".xlsx") else "xlrd"
    df = pd.read_excel(excel_path, sheet_name=sheet_name, engine=engine,
                       header=None, nrows=nrows)
    df = df.fillna("")
    grid = [[str(v) for v in row] for row in df.values.tolist()]
    return grid, len(grid), int(df.shape[1])


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


def _seed_core_keys(table: dict, n_rows: int, n_cols: int) -> None:
    """切表时把核心/高级字段的 viz_* session 键从该表磁盘值灌入(keyed 表单据此显示)。"""
    s = st.session_state
    s["_viz_hdr"] = _clamp(table.get("header_row", 0), 0, max(n_rows - 1, 0))
    s["_viz_fdr"] = _clamp(table.get("first_data_row", s["_viz_hdr"] + 1), 0, max(n_rows - 1, 0))
    ldr = table.get("last_data_row")
    s["_viz_ldr"] = ldr if (isinstance(ldr, int) and 0 <= ldr < n_rows) else SE.UNSET
    s["_viz_lci"] = _clamp(table.get("label_col_idx", 0), 0, max(n_cols - 1, 0))
    dcs = table.get("data_col_start")
    s["_viz_dcs"] = dcs if (isinstance(dcs, int) and 0 <= dcs < n_cols) else SE.UNSET
    dce = table.get("data_col_end")
    s["_viz_dce"] = dce if (isinstance(dce, int) and 0 <= dce <= n_cols) else SE.UNSET
    dmc = table.get("detail_marker_col_idx")
    s["_viz_dmc"] = dmc if (isinstance(dmc, int) and 0 <= dmc < n_cols) else SE.UNSET
    s["_viz_subrules"] = SE.format_subtotal_rules(table.get("subtotal_rules", []))
    s["_viz_classifiers"] = SE.format_classifiers(table.get("detail_classifier_cols", {}))
    s["_viz_skiplabels"] = SE.format_skip_labels(table.get("skip_labels", []))
    s["_viz_skipregex"] = table.get("skip_label_regex", "") or ""


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
            grid, n_rows, n_cols = _read_grid(excel, sheet_names[si], 200)
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
                s = st.session_state
                s["_viz_hdr"] = sug.get("header_row", s["_viz_hdr"])
                s["_viz_fdr"] = sug.get("first_data_row", s["_viz_fdr"])
                if "label_col_idx" in sug:
                    s["_viz_lci"] = sug["label_col_idx"]
                if "data_col_start" in sug:
                    s["_viz_dcs"] = sug["data_col_start"]
                if "data_col_end" in sug:
                    s["_viz_dce"] = sug["data_col_end"]
                if "detail_marker_col_idx" in sug:
                    s["_viz_dmc"] = sug["detail_marker_col_idx"]
                st.session_state.pop("_viz_preflight", None)
                st.session_state.pop("_viz_highlight", None)
                st.rerun()

        target = st.session_state.get("_viz_target", "row_map")
        row_opts = list(range(n_rows))
        col_opts = list(range(n_cols)) + [SE.UNSET]
        hdr = st.session_state.get("_viz_hdr", 0)
        rfmt = lambda i: "末尾(默认到表底)" if i == SE.UNSET else SE.row_option_label(i, grid)
        cfmt = lambda i: ("未指定" if i == SE.UNSET
                          else SE.col_option_label(i, grid, label_row=hdr))

        with st.form("viz_form"):
            st.markdown("##### 通用字段")
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
            st.selectbox("数据终止列 data_col_end (EXCLUSIVE,不含本列)",
                         col_opts, key="_viz_dce", format_func=cfmt)

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

            st.caption("⚠ 可视化应用会用标准格式重写 YAML(注释丢失);未在此编辑的字段保持不变。")
            submitted = st.form_submit_button("✓ 应用可视化改动到当前任务", type="primary")

        if submitted:
            s = st.session_state
            vals = {
                "target": target,
                "header_row": s["_viz_hdr"], "first_data_row": s["_viz_fdr"],
                "last_data_row": s["_viz_ldr"],
                "data_col_start": s["_viz_dcs"], "data_col_end": s["_viz_dce"],
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

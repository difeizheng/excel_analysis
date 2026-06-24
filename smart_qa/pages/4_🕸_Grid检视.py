"""工作台 · Grid 检视:load_grid 后按表在线查看 + 按地址查 Cell。

泛型化(2026-06):不再写死 fin/cap/gen_* 4 字段,改为遍历 grid.table_index
按 target(row_map/subtotal/detail)动态生 tab。任意陌生 Excel 的所有表都可见。
"""
from __future__ import annotations
import pandas as pd
import streamlit as st

import ui_common as U  # noqa: E402

st.set_page_config(page_title="Grid 检视", page_icon="🕸", layout="wide")
st.title("🕸 Grid 检视")
st.caption(f"{U.ICON_DET} 当前任务 schema+excel 解析出的 Grid(dumb indexer,确定性)")

U.render_task_sidebar()
task = U.current_task()
if task is None:
    st.info("暂无任务。请到「📤 数据接入」创建。")
    st.stop()

try:
    grid = U.get_grid()
except Exception as e:
    st.error(f"加载 Grid 失败: {e}")
    st.stop()
if grid is None:
    st.warning("当前任务尚未生成 Grid(可能 schema/excel 缺失)。")
    st.stop()

st.caption(f"当前任务: **{task.name}**")

# ---- 通用统计:按 target 分组统计表数 ----
def _count(target: str) -> int:
    return sum(1 for m in grid.table_index.values() if m.get("target") == target)

n_rowmap = _count("row_map")
n_detail = _count("gen_detail")
n_subtotal = _count("gen_subtotals")
c1, c2, c3, c4 = st.columns(4)
c1.metric("表总数", len(grid.table_index))
c2.metric("row_map 表", n_rowmap)
c3.metric("detail 表", n_detail)
c4.metric("subtotal 表", n_subtotal)


def _row_map_to_df(rmap: dict) -> pd.DataFrame:
    """{label: {colkey: Cell}} -> DataFrame(index=label)。"""
    if not rmap:
        return pd.DataFrame()
    colkeys = sorted({ck for inner in rmap.values() for ck in inner})
    rows = {}
    for label, inner in rmap.items():
        rows[label] = {ck: getattr(inner.get(ck), "value", None) for ck in colkeys}
    return pd.DataFrame.from_dict(rows, orient="index")


def _details_to_df(projects: list) -> pd.DataFrame:
    """[{分类字段..., values:{colkey:Cell}}] -> DataFrame(动态列,不写死字段名)。"""
    if not projects:
        return pd.DataFrame()
    rows = []
    for p in projects:
        base = {k: v for k, v in p.items() if k != "values"}  # 分类维度字段动态保留
        for ck, c in (p.get("values") or {}).items():
            base[str(ck)] = getattr(c, "value", None)
        rows.append(base)
    return pd.DataFrame(rows)


def _tab_icon(target: str | None) -> str:
    return {"row_map": "📋", "gen_detail": "📝", "gen_subtotals": "∑"}.get(target, "·")


# ---- 按 table_index 顺序动态生 tab(每表一个) ----
# 顺序:row_map → detail → subtotal,与 table_index 插入(schema 遍历)顺序一致。
def _tables_in_order():
    seen = set()
    for target in ("row_map", "gen_detail", "gen_subtotals"):
        for name, meta in grid.table_index.items():
            if meta.get("target") == target and name not in seen:
                seen.add(name)
                yield name, meta

ordered = list(_tables_in_order())
if not ordered:
    st.info("当前 Grid 无任何启用的表。请到「Schema 编辑」检查 tables 的 enabled。")
    st.stop()

tabs = st.tabs([f"{_tab_icon(m.get('target'))} {name}" for name, m in ordered])
for tab, (name, meta) in zip(tabs, ordered):
    target = meta.get("target")
    with tab:
        st.caption(f"sheet: **{meta.get('sheet')}** · target: `{target}`")
        if target == "row_map":
            st.dataframe(_row_map_to_df(grid.row_maps.get(name, {})), use_container_width=True)
        elif target == "gen_subtotals":
            st.dataframe(_row_map_to_df(grid.subtotals.get(name, {})), use_container_width=True)
        elif target == "gen_detail":
            df = _details_to_df(grid.details.get(name, []))
            if df.empty:
                st.info(f"无 {name} 明细行")
            else:
                st.dataframe(df, use_container_width=True)


# ---- 按地址查 Cell(遍历全部容器,通用) ----
st.divider()
st.subheader(f"{U.ICON_DET} 按地址查 Cell")
addr = st.text_input("输入单元格地址(如 财务数据!J6)")
if addr:
    found = None
    for src, label, cells in grid.iter_row_maps():
        for ck, c in cells.items():
            if getattr(c, "addr", "") == addr:
                found = (f"row_map[{src}].{label}[{ck}]", c)
                break
        if found:
            break
    if not found:
        for src, emit_key, cells in grid.iter_subtotals():
            for ck, c in cells.items():
                if getattr(c, "addr", "") == addr:
                    found = (f"subtotal[{src}].{emit_key}[{ck}]", c)
                    break
            if found:
                break
    if not found:
        for src, p in grid.iter_details():
            for ck, c in (p.get("values") or {}).items():
                if getattr(c, "addr", "") == addr:
                    found = (f"detail[{src}][{p.get('name')}][{ck}]", c)
                    break
            if found:
                break
    if found:
        loc, c = found
        st.success(loc)
        st.json({
            "value": c.value, "addr": c.addr, "numeric": c.numeric,
            "row_idx": c.row_idx, "col_idx": c.col_idx,
        })
    else:
        st.warning(f"未找到地址 {addr}")

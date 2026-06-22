"""工作台 · Grid 检视:load_grid 后 4 字段在线查看 + 按地址查 Cell。"""
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

c1, c2, c3, c4 = st.columns(4)
c1.metric("fin 行", len(grid.fin))
c2.metric("cap 行", len(grid.cap))
c3.metric("gen_projects", len(grid.gen_projects))
c4.metric("gen_subtotals", len(grid.gen_subtotals))


def _row_map_to_df(rmap: dict) -> pd.DataFrame:
    """{label: {colkey: Cell}} -> DataFrame(index=label)。"""
    if not rmap:
        return pd.DataFrame()
    colkeys = sorted({ck for inner in rmap.values() for ck in inner})
    rows = {}
    for label, inner in rmap.items():
        rows[label] = {ck: getattr(inner.get(ck), "value", None) for ck in colkeys}
    return pd.DataFrame.from_dict(rows, orient="index")


tabs = st.tabs(["fin 财务", "cap 装机", "gen_projects 明细", "gen_subtotals 小计"])
with tabs[0]:
    st.dataframe(_row_map_to_df(grid.fin), use_container_width=True)
with tabs[1]:
    st.dataframe(_row_map_to_df(grid.cap), use_container_width=True)
with tabs[2]:
    if grid.gen_projects:
        rows = []
        for p in grid.gen_projects:
            base = {"name": p.get("name"), "方式": p.get("方式"), "区域": p.get("区域")}
            for ck, c in (p.get("values") or {}).items():
                base[str(ck)] = getattr(c, "value", None)
            rows.append(base)
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("无 gen_projects")
with tabs[3]:
    st.dataframe(_row_map_to_df(grid.gen_subtotals), use_container_width=True)

# ---- 按地址查 Cell ----
st.divider()
st.subheader(f"{U.ICON_DET} 按地址查 Cell")
addr = st.text_input("输入单元格地址(如 财务数据!J6)")
if addr:
    found = None
    for field in ("fin", "cap", "gen_subtotals"):
        for label, inner in getattr(grid, field).items():
            for ck, c in inner.items():
                if getattr(c, "addr", "") == addr:
                    found = (f"{field}.{label}[{ck}]", c)
                    break
            if found:
                break
        if found:
            break
    if not found:
        for p in grid.gen_projects:
            for ck, c in (p.get("values") or {}).items():
                if getattr(c, "addr", "") == addr:
                    found = (f"gen_projects[{p.get('name')}][{ck}]", c)
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

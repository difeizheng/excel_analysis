"""工作台 · 数据接入:上传 Excel → 创建任务 → 骨架网格预览 → schema 初始化。

上传即创建一个自包含任务(自带 excel/schema/semantic 副本),并自动选为当前任务。
任意 Excel 可上传;Grid 入库仍按现有派发(财务数据/装机/发电量)。
"""
from __future__ import annotations
import os
import yaml
import pandas as pd
import streamlit as st

import ui_common as U  # noqa: E402
import task_store as TS  # noqa: E402
import schema_proposer as SP  # noqa: E402
import validate as V  # noqa: E402
import schema_spec as SS  # noqa: E402
from llm_parser import get_default  # noqa: E402

st.set_page_config(page_title="数据接入", page_icon="📤", layout="wide")
st.title("📤 数据接入")
st.caption(
    f"上传 Excel → 创建任务 → 看骨架网格 {U.ICON_DET} → 初始化 schema"
    f"({U.ICON_DET} 克隆模板 或 {U.ICON_LLM} LLM 生成)"
)

# 任务上下文(侧栏)
U.render_task_sidebar()

# ---- 上传 = 创建任务 ----
# file_uploader 的文件状态由【浏览器前端】持有。单纯 pop key + rerun 后,前端会
# 把文件状态重传 → 重复 create_task(实测一次上传可建出 3+ 个同名任务)。
# 【动态 key】是最可靠的清空方式:每次成功创建后 counter+1,下一轮用全新 key
# 的 uploader(前端对该新 key 无文件记忆)→ 必为空 → 不再重复创建。
_upload_counter = st.session_state.get("_upload_counter", 0)
up = st.file_uploader("上传 Excel 创建任务(.xls / .xlsx)", type=["xls", "xlsx"],
                      key=f"excel_uploader_{_upload_counter}")
if up is not None:
    stem = os.path.splitext(up.name)[0]
    t = TS.create_task(
        name=stem, excel_bytes=up.getbuffer(), filename=up.name,
        schema_source="template",  # 默认克隆 committed 模板,可下方再换
    )
    U.set_current_task_id(t.id)
    st.session_state["_just_created_task"] = t.id            # 一次性反馈标记
    st.session_state["_upload_counter"] = _upload_counter + 1  # 换 key,废弃带文件的旧 uploader
    st.rerun()

# 一次性「创建成功」反馈(rerun 后渲染一次,下次 rerun 因 pop 消失)
_just = st.session_state.pop("_just_created_task", None)
if _just:
    _jt = TS.get_task(_just)
    if _jt:
        st.success(f"✓ 已创建任务「{_jt.name}」并选为当前任务。下方为其 Sheet/骨架预览。")

# ---- 当前任务 ----
task = U.current_task()
if task is None:
    st.info("暂无任务。上传 Excel 创建,或侧栏「＋ 新建任务」。")
    st.stop()

path = task.excel_path
if not os.path.exists(path):
    st.error(f"任务 Excel 缺失: {path}")
    st.stop()

st.caption(f"当前任务: **{task.name}** · 数据源 `{task.excel_filename}` · 创建于 {task.created_at[:16]}")

engine = "openpyxl" if path.lower().endswith(".xlsx") else "xlrd"
xls = pd.ExcelFile(path, engine=engine)
sheets = list(xls.sheet_names)

# ---- Sheet 清单 + 派发诚实表达(动态 · 来自 load_grid.sheet_dispatch)----
# sheet 清单来自 ExcelFile(展示全部 sheet,含 selectbox 选项);
# "入 Grid / 进了哪个字段" 取自当前任务 Grid 的 sheet_dispatch,
# 与 loader.py 派发点是同一份真相,不再靠平行硬编码对账。
st.subheader("Sheet 清单与 Grid 派发")
try:
    _grid = U.get_grid()
    _dispatch = _grid.sheet_dispatch if _grid is not None else {}
except Exception:
    # schema 被 LLM/手改坏时,load_grid 会抛;降级让页面其余部分(清单+骨架)照常
    _dispatch = {}
    st.caption("⚠ schema 解析失败,派发信息暂不可用(下方骨架预览仍正常)")

for s in sheets:
    if s in _dispatch:
        fields = " · ".join(_dispatch[s])
        st.markdown(f"- `{s}` — {U.ICON_DET} → {fields}")
    else:
        st.markdown(f"- `{s}` — ⚠ 未入 Grid")
if any(s not in _dispatch for s in sheets):
    st.warning(
        "未入 Grid 的 Sheet 数据不会进入查询 Grid。"
        "派发结果由当前任务的 schema 决定(见 loader.sheet_dispatch),"
        "如需扩字段请走「Schema 编辑」调 table target,不要在 loader 旁路加白名单。"
    )

# ---- 骨架网格预览(类型脱敏)----
st.subheader(f"骨架网格预览 {U.ICON_DET}(类型脱敏,LLM 看不到真实数值)")
sel = st.selectbox("选择 Sheet", sheets)
df = pd.read_excel(xls, sheet_name=sel, engine=engine, header=None, nrows=15)
st.code(SP.render_skeleton(df), language="text")

st.divider()

# ---- schema 初始化(对当前任务)----
st.subheader("初始化当前任务的 schema")
col1, col2 = st.columns(2)
with col1:
    st.markdown(f"#### {U.ICON_DET} 克隆 committed 模板(默认,瞬时)")
    st.caption("committed 模板是三峡经营数据库的结构。⚠ 若本任务是非三峡格式的 Excel,"
               "建议改用右侧「LLM 生成 schema」,不要用此模板。")
    if st.button("重置为 committed 模板"):
        TS.update_status(task.id, "构建中")
        with open(U.COMMITTED_SCHEMA, encoding="utf-8") as f:
            txt = f.read()
        TS.write_task_schema(task.id, txt)
        TS.set_schema_source(task.id, "template")
        U.invalidate_grid()
        st.success("已重置为模板 schema。去「Schema 编辑」细调。")
        st.rerun()

with col2:
    st.markdown(f"#### {U.ICON_LLM} LLM 基于本 Excel 生成 schema")
    llm = get_default()
    if not llm.available:
        st.info(llm.status())
    if st.button("生成 schema", disabled=not llm.available):
        with st.spinner(f"{U.ICON_LLM} LLM 推断 schema 中(离线重任务,约 1-2 分钟,含 1 轮自动修复)..."):
            yaml_text = SP.propose(path)
        if yaml_text:
            TS.write_task_schema(task.id, yaml_text)
            TS.set_schema_source(task.id, "llm")
            U.invalidate_grid()
            st.success("schema 已写入当前任务。去「Schema 编辑」细调。")
            with st.expander("生成的 YAML + 校验闸门"):
                st.code(yaml_text, language="yaml")
                try:
                    spec = SS.workbook_from_dict(yaml.safe_load(yaml_text))
                    errs = V.validate(spec, path)
                    if errs:
                        st.error(f"校验 {len(errs)} 个问题:")
                        for e in errs:
                            st.write(f"- {e}")
                    else:
                        st.success(f"{U.ICON_DET} 校验全通过(V1-V10)")
                except Exception as e:
                    st.error(f"schema 解析失败: {e}")
        else:
            st.error("LLM 生成失败(见终端日志),可手写或用模板。")

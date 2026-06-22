"""Streamlit 工作台 · 首页问数台。

启动: streamlit run app.py

本页:多轮 chat + 答案卡 + C1-C4 校验 + 7 层流转可视化。
任务中心:针对当前选中任务(默认最新)问答;对话按任务隔离。
系统灵魂:查询链路层④-⑦全程确定性,LLM 不参与算数(界面用 🔮/⚙ 标注)。
"""
from __future__ import annotations
import os
import pandas as pd
import streamlit as st

import ui_common as U  # noqa: E402  (设置 sys.path + 任务上下文 helper)
import qa  # noqa: E402
import trace_runner as TR  # noqa: E402
from llm_parser import get_default  # noqa: E402


@st.cache_resource
def get_db_path() -> str:
    from to_sqlite import build_db
    db = os.path.join(U._ROOT, "data", "grid.db")
    if not os.path.exists(db):
        build_db(os.path.join(U._REPO, "测试数据.xls"), db)
    return db


# ---------- 渲染辅助 ----------
def _fmt_value(v, op, unit) -> str:
    if v is None:
        return "—"
    if op == "cagr":
        return f"{v * 100:.2f}%"
    if unit == "":
        return f"{v:.2f}"
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v))} {unit}".strip()
    return f"{v:.2f} {unit}".strip()


def _render_answer(ans: dict) -> None:
    if ans["kind"] == "fail":
        st.warning(f"⚠ {ans['msg']}")
        st.caption("系统拒绝编造,请确认指标/时间是否正确")
        return

    if ans["kind"] == "multi":
        rows = [{
            "指标": it["metric"],
            "值": it["value"] if it["value"] is not None else "—",
            "单位": it["unit"],
            "单元格": it["addr"],
        } for it in ans["items"]]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        if ans.get("rules"):
            with st.expander("📋 规则依据"):
                for r in ans["rules"]:
                    st.write(f"- {r}")
    else:
        res = ans["result"]
        unit = "百分比" if res.operation == "cagr" else res.unit
        st.success(f"答: **{_fmt_value(res.value, res.operation, unit)}**")
        st.caption(f"计算链: {res.formula}")
        if res.operands:
            with st.expander(f"📍 溯源 ({len(res.operands)} 项) — 可点击展开看单元格", expanded=True):
                for i, o in enumerate(res.operands[:20], 1):
                    st.markdown(
                        f"**{i}. `{o.addr}`** — {o.label} = **{_fmt_value(o.value, res.operation, o.unit)}**"
                    )
                if len(res.operands) > 20:
                    st.caption(f"... 其余 {len(res.operands) - 20} 项")
        if res.rules:
            with st.expander("📋 规则依据"):
                for r in res.rules:
                    st.write(f"- {r}")

    if ans.get("verified"):
        st.success(f"{U.ICON_DET} 校验通过 — {ans.get('verify_msg', '')[:200]}")
    else:
        st.error(f"✗ 校验未通过 — {ans.get('verify_msg', '')}")


def _render_trace(grid, question: str, ans: dict, use_llm: bool) -> None:
    """7 层流转可视化(复用 trace_runner,传入已算好的 ans 避免重复调用)。"""
    with st.expander(
        f"{U.ICON_DET} 展开全链路(7 层流转 · 层④-⑦ LLM 不参与算数)", expanded=False
    ):
        tr = TR.trace_question(grid, question, use_llm=use_llm, ans=ans)
        src = tr["intent_source"]
        st.caption(
            f"第③层意图来源: {U.ICON_LLM if src == 'LLM' else U.ICON_DET} {src}　"
            f"(其余各层均为 {U.ICON_DET} 确定性)"
        )
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**① 预处理层 · {U.ICON_DET}**  Grid(离线一次性产物)")
            gs = tr["layer1_grid"]
            cells = gs.get("cells")
            if cells:
                st.caption(f"{gs.get('source')} · {gs.get('row_label') or gs.get('emit_key')}")
                st.json(dict(list(cells.items())[:6]))
            else:
                st.caption(gs.get("note", ""))
        with c2:
            st.markdown(f"**② 语义层 · {U.ICON_DET}**  口语→标准概念")
            st.json(tr["layer2_semantic"])
        st.markdown(f"**③ 意图解析 · {U.ICON_LLM if src == 'LLM' else U.ICON_DET}**")
        st.json(tr["layer3_intent"])
        st.markdown(f"**④ 规划取数 · {U.ICON_DET}**  Intent → CellView")
        st.json(tr["layer4_plan"])
        st.markdown(f"**⑤ 执行层 · {U.ICON_DET}**  确定性 engine")
        st.json(tr["layer5_exec"])
        st.markdown(f"**⑥ 答案合成 · {U.ICON_DET}**")
        st.text(tr["layer6_answer"])
        st.markdown(f"**⑦ 校验层 · {U.ICON_DET}**  独立复核")
        v = tr["layer7_verify"]
        st.write(f"verified={v['verified']}　{v['verify_msg'][:200]}")


# ---------- 页面 ----------
st.set_page_config(page_title="智能问数", page_icon="📊", layout="wide")
st.title("📊 三峡国际 · 智能问数")
st.caption(f"{U.ICON_DET} 确定性取数 · {U.ICON_LLM} 仅意图解析(可选)· 零幻觉")

# ----- 任务上下文(侧栏顶部选择器)-----
task = U.render_task_sidebar()
grid = U.get_grid()
messages = U.get_messages()

# ----- Sidebar:数据状态 -----
with st.sidebar:
    st.divider()
    st.header("当前任务数据")
    if grid is None:
        st.warning("当前任务尚未生成 Grid(可能 schema/excel 缺失)。请到「数据接入」或「Schema 编辑」。")
    else:
        c1, c2 = st.columns(2)
        c1.metric("财务指标", len(grid.fin))
        c2.metric("装机项目", len(grid.cap))
        c3, c4 = st.columns(2)
        c3.metric("发电量明细", len(grid.gen_projects))
        c4.metric("小计区", len(grid.gen_subtotals))
    if task is not None:
        st.caption(f"📄 {task.excel_filename}　{U.ICON_DET} schema={task.schema_source}")

    st.divider()
    st.subheader("意图解析器")
    llm = get_default()
    if llm.available:
        st.success(f"{U.ICON_LLM} {llm.status()}")
    else:
        st.info(f"{U.ICON_DET} {llm.status()}")
    use_llm = st.toggle(
        "使用 LLM 解析意图", value=llm.available, disabled=not llm.available,
        help="仅第③层意图解析用 LLM;算数始终确定性",
    )

    st.divider()
    st.subheader("SQLite 镜像")
    try:
        st.success(f"已就绪(示例种子): {os.path.relpath(get_db_path(), U._ROOT)}")
        st.caption("答案取自内存中的任务 Grid,本镜像仅状态展示。")
    except Exception as e:
        st.error(f"未就绪: {e}")

    st.divider()
    if st.button("🗑 清空当前任务对话", use_container_width=True):
        messages.clear()
        st.rerun()

# ----- 主体:无任务/无 Grid 则引导 -----
if task is None:
    st.info("暂无任务。请到「📤 数据接入」上传 Excel 创建一个任务。")
    st.stop()
if grid is None:
    st.stop()

# ----- 历史 -----
st.subheader(f"对话 · `{task.name}`")
for msg in messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])
        if msg.get("ans"):
            _render_answer(msg["ans"])
        if msg.get("trace"):
            _render_trace(grid, msg["content"], msg["ans"], msg.get("use_llm", False))

# ----- 示例 -----
EXAMPLES = [
    "公司2018年的利润总额是多少？",
    "三峡国际2022、2024、2025年每年的汇兑净损失是多少？",
    "24年-26年2月累计向集团分红多少？",
    "公司近三年的利润增长率是多少？",
    "三峡国际2025年的总装机、可控装机、利润总额、发电量是多少？",
    "公司2025年风电发电量是多少",
]

if not messages:
    st.caption("💡 试试这些问题:")
    cols = st.columns(2)
    question = None
    for i, ex in enumerate(EXAMPLES):
        if cols[i % 2].button(ex, key=f"ex_{i}"):
            question = ex
            break
else:
    question = None

user_input = st.chat_input("问个问题,例如:公司2018年的利润总额?")
if user_input:
    question = user_input

if question and not any(
    m["role"] == "user" and m["content"] == question for m in messages
):
    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        with st.spinner("解析中..."):
            try:
                ans = qa.ask(grid, question, use_llm=use_llm)
            except Exception as e:
                st.error(f"出错了: {e}")
                ans = None
        if ans:
            _render_answer(ans)
            _render_trace(grid, question, ans, use_llm)
            messages.append({
                "role": "assistant", "content": ans["text"],
                "ans": ans, "trace": True, "use_llm": use_llm,
            })

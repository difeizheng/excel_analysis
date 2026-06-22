"""工作台 · 语料回归:把存入的接地语料对【当前 grid】全量重跑,看 ✓率与缺口。

闭环主视图。expected 永远现场从 grid 重算(不读落盘数字);LLM 不参与。
- 可验证类 ✓率 = parser/语义口径对自然+模板问法的复现能力。
- 盲区类 ✗ = 引擎已知能力缺口(peak/share/yoy/rank)。
- 孤儿 = target 引用的指标已不在当前 grid(改名/删除后失效)。

Schema/语义层编辑时的"前后 diff"在对应编辑器顶部(闸门),本页看"当前快照"。
"""
from __future__ import annotations
import pandas as pd
import streamlit as st

import ui_common as U  # noqa: E402
import corpus_store as CS  # noqa: E402
import corpus_run as CR  # noqa: E402

OP_CN = {
    "lookup": "查值", "cumulative": "累计合计", "taxonomy": "分类汇总",
    "cagr": "复合增长率", "multi_year": "多年序列", "multi_metric": "多指标",
    "peak_year": "峰值年", "share": "占比", "yoy": "同比", "rank": "排名",
}

st.set_page_config(page_title="语料回归", page_icon="🔁", layout="wide")
st.title("🔁 语料回归")
st.caption(
    f"{U.ICON_DET} 把存入的语料对当前任务 Grid 全量重跑(答案现场算,LLM 不碰数字)。"
    "10 类全部可验证;✓率 = 口径/parser 鲁棒性;✗ = 问法未命中;孤儿 = 指标已失效。"
)

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

entries = CS.load_corpus(task.id)
st.caption(f"当前任务: **{task.name}**　·　语料 {len(entries)} 条意图")

if not entries:
    st.info(
        "语料库为空。先到「❓ 问题生成」造题并点「💾 存入语料库」,"
        "再回这里看回归,或在 Schema/语义层编辑时自动触发闸门。"
    )
    st.stop()

# 统计 phrasing 数
n_phrasings = sum(len(e.phrasings) for e in entries)

ck = f"_regress_{task.id}"
ccol, _ = st.columns([1, 3])
if ccol.button("▶ 跑全量回归", type="primary",
               help=f"对 {n_phrasings} 条问法逐条跑确定性引擎(无 LLM)。"):
    with st.spinner(f"跑 {n_phrasings} 条问法…"):
        st.session_state[ck] = CR.run_items(entries, grid, mode="all")

rows = st.session_state.get(ck)
if not rows:
    st.caption("点「▶ 跑全量回归」开始。")
    st.stop()

# ---- 表 ----
df = pd.DataFrame([{
    "题型": OP_CN.get(r["op"], r["op"]),
    "类别": "可验证" if r["category"] == "verifiable" else "盲区",
    "问法": r["phrasing"],
    "来源": r["src"],
    "期望真值": U.fmt_val(r["expected"]),
    "引擎答案": U.fmt_val(r["actual"]),
    "命中": "✓" if r["match"] else "✗",
    "引擎状态": U.engine_status(r),
} for r in rows])
st.dataframe(df, use_container_width=True, hide_index=True)

# ---- 汇总 ----
total = len(rows)
n_match = sum(r["match"] for r in rows)
n_orphan = sum(r["orphan"] for r in rows)
n_miss = total - n_match

m1, m2, m3 = st.columns(3)
m1.metric("总命中", f"{n_match}/{total}", help="引擎复现真值的条数")
m2.metric("未命中(✗)", n_miss,
          help="偏离口径或问法未命中(Phase 8.3 起 10 类引擎均可答,非引擎缺口)")
m3.metric("孤儿(指标失效)", n_orphan,
          help="target 引用的指标已不在当前 grid,建议清理或重建")

st.caption("✓ = 引擎复现真值;✗ = 问法未命中或口径偏离。孤儿 = target 引用的指标已失效。")

# ---- 未命中分组 ----
fails = [r for r in rows if not r["match"]]
if fails:
    with st.expander(f"未命中({len(fails)})— 定位问法/口径缺口"):
        for r in fails:
            st.write(
                f"- `{OP_CN.get(r['op'], r['op'])}` {r['phrasing']}  "
                f"期望={U.fmt_val(r['expected'])} 引擎={U.fmt_val(r['actual'])} "
                f"[{U.engine_status(r)}]"
            )

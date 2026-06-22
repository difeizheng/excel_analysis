"""工作台 · 问题生成:多题型接地生成 + parser 鲁棒性测试台。

10 题型(6 可验证 + 4 盲区探测),每条的「标准答案」直接从当前任务 Grid 算出
(LLM 不碰数字);LLM 只负责把参数改写成自然问句。再用确定性 qa.ask 去答,
能否复现标准答案 = 这套口径/parser 的真实能力。

✓ 率分两类看:可验证类 ✓率 = parser 对自然表达的鲁棒性;盲区类 ✗ = 引擎已知能力缺口。
"""
from __future__ import annotations
import pandas as pd
import streamlit as st

import ui_common as U  # noqa: E402
import question_generator as QG  # noqa: E402
import llm_client  # noqa: E402
import corpus_run as CR  # noqa: E402
import corpus_store as CS  # noqa: E402
from llm_parser import get_default  # noqa: E402

# ---- 题型标签(10 类全部可验证 —— Phase 8.3 起原 4 盲区已补齐进引擎) ----
TYPE_LABEL = {
    "lookup": "查值 lookup", "cumulative": "累计合计 cumulative",
    "taxonomy": "分类汇总 taxonomy", "cagr": "复合增长率 cagr",
    "multi_year": "多年序列 multi_year", "multi_metric": "多指标 multi_metric",
    "peak_year": "峰值年 peak_year", "share": "占比 share",
    "yoy": "同比 yoy", "rank": "排名 rank",
}
TYPE_ORDER = list(TYPE_LABEL)

PHRASE_LLM = f"{U.ICON_LLM} LLM 自然造句"
PHRASE_TPL = f"{U.ICON_DET} 模板造句"


def _status(vr: dict) -> str:
    """引擎状态:拒答/异常 → 诚实拒答;否则给 kind(single/multi)。"""
    if vr.get("refused"):
        return "拒答/异常"
    return vr.get("engine_kind", "?")


def _fmt(v) -> str:
    """把期望/答案值格式化为可读字符串(scalar 或 list 统一为文本,避 Arrow 混型告警)。"""
    if v is None:
        return "—"
    if isinstance(v, (list, tuple)):
        return " / ".join(_fmt(x) for x in v)
    try:
        f = float(v)
        return f"{f:.4f}".rstrip("0").rstrip(".") or "0"
    except (TypeError, ValueError):
        return str(v)


st.set_page_config(page_title="问题生成", page_icon="❓", layout="wide")
st.title("❓ 问题生成")
st.caption(
    f"{U.ICON_LLM} LLM 只造句,{U.ICON_DET} 标准答案由当前任务 Grid 确定性算出(接地,LLM 不碰数字)。"
    "再让引擎去答——能不能复现标准答案,就是这一页的全部意义。"
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

st.caption(f"当前任务: **{task.name}**")

llm = get_default()
c_mode, c_types, c_n = st.columns([1, 2, 1])
with c_mode:
    mode = st.selectbox(
        "造句方式",
        [PHRASE_LLM, PHRASE_TPL],
        index=0 if llm.available else 1,
        help="LLM 自然造句:LLM 自由改写为自然业务问法,可能丢掉引擎识别所需的关键词"
             "(增长率/累计/风电…)——用于压力测试 parser 对自然表达的鲁棒性。\n"
             "模板造句:固定句式、运算词齐全,最大化命中引擎意图(确定性基线)。",
    )
    if not llm.available:
        st.caption("⚠ 未配置 LLM,实际将走模板造句。")
with c_types:
    chosen = st.multiselect(
        "题型范围", TYPE_ORDER, default=TYPE_ORDER,
        format_func=lambda k: TYPE_LABEL[k],
        help="10 类全部可验证(引擎能答);标准答案由 Grid 确定性算出,"
             "能否复现 = parser 对自然问法的鲁棒性。")
with c_n:
    n = st.slider("生成数量", 3, 30, 10)

use_llm = (mode == PHRASE_LLM)

if st.button("生成并验证", type="primary"):
    if not chosen:
        st.warning("请至少选一个题型。")
        st.stop()
    targets = QG.enumerate_targets(grid, max_n=n, types=set(chosen))
    if not targets:
        st.warning("未枚举到真实目标(Grid 可能未加载或所选题型无可用指标)。")
        st.stop()
    client = llm_client.get_default()
    rows = []
    corpus_items = []                                  # [(target_dict, 问法, src_tag)] 存语料库用
    progress = st.progress(0, text="生成中...")
    for i, t in enumerate(targets):
        use_llm_now = use_llm and client.available
        q = (QG.build_question(t, client) if use_llm_now else None) or QG.fallback_question(t)
        src = f"{U.ICON_LLM} LLM" if use_llm_now else f"{U.ICON_DET} 模板"
        src_tag = "LLM" if use_llm_now else "模板"
        vr = QG.verify_question(q, grid, t.expected)
        rows.append({
            "题型": TYPE_LABEL.get(t.operation, t.operation),
            "类别": "可验证" if t.category == "verifiable" else "盲区",
            "问题": q, "来源": src,
            "期望真值": _fmt(t.expected.get("value")),
            "引擎答案": _fmt(vr["actual"]),
            "命中": "✓" if vr["match"] else "✗",
            "引擎状态": _status(vr),
        })
        td = CR.target_to_dict(t)
        corpus_items.append((td, q, src_tag))
        fb = QG.fallback_question(t)
        if fb != q:
            corpus_items.append((td, fb, "模板"))     # 每条都带确定性模板句(闸门基线)
        progress.progress((i + 1) / len(targets))
    U.set_generated_questions(rows)
    U.set_generated_pairs(corpus_items)

rows = U.get_generated_questions()
if rows:
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    bc, bs = st.columns([1, 3])
    if bc.button("💾 存入语料库", type="primary",
                 help="把这批接地问题(target+问法)存入当前任务语料。答案永不落盘,"
                      "后续到「🔁 语料回归」跑全量,或在 Schema/语义层编辑应用时自动跑闸门。"):
        items = U.get_generated_pairs()
        if items:
            added = CS.upsert_phrasings(task.id, items)
            bs.success(
                f"已存入语料库:{added} 条问法(去重后)。"
                "到「🔁 语料回归」查看全量 ✓率;编辑 Schema/语义层时闸门会自动比对前后。"
            )
        else:
            bs.warning("没有可存的批次,先点「生成并验证」。")

    total = len(rows)
    n_match = sum(r["命中"] == "✓" for r in rows)
    fails = [r for r in rows if r["命中"] != "✓"]
    n_ops = len({r["题型"] for r in rows})

    m1, m2, m3 = st.columns(3)
    m1.metric("总匹配率", f"{n_match}/{total}",
              help="引擎能否复现 Grid 算出的标准答案")
    m2.metric("未匹配(✗)", len(fails),
              help="偏离口径的条数;多为 LLM 改写丢了运算关键词或语义未命中(可改进)")
    m3.metric("题型覆盖", n_ops,
              help="本批涉及的题型数(满分 10)")

    st.caption(
        "✓ = 引擎复现了标准答案;✗ = 偏离口径。"
        "Phase 8.3 起 10 题型引擎均可答,✗ 多为 LLM 改写丢了运算关键词或问法未命中。"
    )

    if fails:
        with st.expander(f"未匹配({len(fails)})— 定位 parser/口径缺口"):
            for r in fails:
                st.write(
                    f"- `{r['题型']}` {r['问题']}  "
                    f"期望={r['期望真值']} 引擎={r['引擎答案']} [{r['引擎状态']}]"
                )

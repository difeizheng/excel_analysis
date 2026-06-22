"""工作台 UI 公共 helper:sys.path、任务上下文、grid 缓存、committed 常量、图标。

任务中心模型:所有页面/首页问数都针对「当前选中任务」(默认最新)操作。
任务持久化在 task_store;当前指针存 st.session_state。
"""
from __future__ import annotations
import os
import sys

# smart_qa/ 目录(本文件在 smart_qa/ui_common.py)
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
_ROOT = _HERE                  # smart_qa/
_REPO = os.path.dirname(_HERE)  # excel_analysis/
for _p in (_SRC, _ROOT, _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import streamlit as st  # noqa: E402

import task_store as TS  # noqa: E402

ICON_LLM = "🔮"   # 概率性 LLM 步骤
ICON_DET = "⚙"    # 确定性步骤

# committed 文件:种入示例任务的种子源 + 测试夹具(UI 不再写回)
COMMITTED_SCHEMA = os.path.join(_ROOT, "schemas", "三峡国际经营数据库.yaml")
COMMITTED_SEMANTIC_DIR = os.path.join(_ROOT, "semantic")
SHEET_WHITELIST = ("财务数据", "装机", "发电量")  # 能进 Grid 的 Sheet 名(派发硬编码)

_DATA_ACCESS_PAGE = "pages/1_📤_数据接入.py"


# ---------------- 当前任务指针(session_state,默认最新)----------------
def current_task_id() -> str | None:
    """当前选中任务 id;首次访问或指针失效时回落最新任务。"""
    tasks = TS.list_tasks()
    if not tasks:
        return None
    ids = [t.id for t in tasks]
    cur = st.session_state.get("current_task_id")
    if cur not in ids:
        cur = tasks[0].id  # 最新(倒序首位)
        st.session_state["current_task_id"] = cur
    return cur


def set_current_task_id(tid: str) -> None:
    st.session_state["current_task_id"] = tid
    invalidate_grid()


def current_task() -> TS.Task | None:
    """当前任务对象;无任务(且无法种入)时返回 None。"""
    tid = current_task_id()
    if tid is None:
        return None
    return TS.get_task(tid)


def current_excel_path() -> str | None:
    t = current_task()
    return t.excel_path if t else None


def current_schema_path() -> str | None:
    t = current_task()
    return t.schema_path if t else None


def current_semantic_dir() -> str | None:
    t = current_task()
    return t.semantic_dir if t else None


# ---------------- per-task 会话数据 ----------------
def get_messages() -> list:
    """当前任务的对话历史(切任务看各自对话)。"""
    t = current_task()
    if t is None:
        return []
    store = st.session_state.setdefault("messages_by_task", {})
    return store.setdefault(t.id, [])


def get_generated_questions() -> list:
    """当前任务最近一次生成的问题批次。"""
    t = current_task()
    if t is None:
        return []
    store = st.session_state.setdefault("questions_by_task", {})
    return store.setdefault(t.id, [])


def set_generated_questions(rows: list) -> None:
    t = current_task()
    if t is None:
        return
    st.session_state.setdefault("questions_by_task", {})[t.id] = rows


def get_generated_pairs() -> list:
    """当前任务最近一批 (target_dict, question, src)——存入语料库用(page5→corpus)。"""
    t = current_task()
    if t is None:
        return []
    return st.session_state.setdefault("gen_pairs_by_task", {}).setdefault(t.id, [])


def set_generated_pairs(pairs: list) -> None:
    t = current_task()
    if t is None:
        return
    st.session_state.setdefault("gen_pairs_by_task", {})[t.id] = pairs


# ---------------- grid 缓存(任务/schema/excel 变更自动失效)----------------
def get_grid():
    """加载并缓存当前任务的 Grid。task/schema mtime/excel mtime 变更即重载。"""
    t = current_task()
    if t is None:
        return None
    try:
        sm = os.path.getmtime(t.schema_path)
        em = os.path.getmtime(t.excel_path)
    except OSError:
        return None
    cache = st.session_state.setdefault("_grid_cache", {})
    key = (t.id, sm, em)
    if cache.get("key") != key:
        import loader
        cache["grid"] = loader.load_grid(t.schema_path, t.excel_path)
        cache["key"] = key
    return cache["grid"]


def invalidate_grid() -> None:
    st.session_state.pop("_grid_cache", None)


# ---------------- 语义层对齐当前任务 ----------------
def ensure_semantic_loaded() -> None:
    """若 semantic_layer 当前加载的目录 != 当前任务目录,则重载(幂等)。

    语义层是全局单例;切任务后须对齐,否则问数/resolve 会用错任务的口径。
    """
    t = current_task()
    if t is None:
        return
    if st.session_state.get("_sem_loaded_dir") == t.semantic_dir:
        return
    reload_current_semantic()


def reload_current_semantic() -> None:
    """强制把 semantic_layer 重载为当前任务目录,并刷新缓存标记。"""
    t = current_task()
    if t is None:
        return
    try:
        import semantic_layer as S
        S.reload(t.semantic_dir)
        st.session_state["_sem_loaded_dir"] = t.semantic_dir
    except Exception:
        pass


# ---------------- 值/状态格式化(page5/6 共享)----------------
def fmt_val(v) -> str:
    """scalar 或 list 统一为可读字符串(避 Arrow 混型告警)。None→—。"""
    if v is None:
        return "—"
    if isinstance(v, (list, tuple)):
        return " / ".join(fmt_val(x) for x in v)
    try:
        f = float(v)
        return f"{f:.4f}".rstrip("0").rstrip(".") or "0"
    except (TypeError, ValueError):
        return str(v)


def engine_status(d: dict) -> str:
    """引擎状态:孤儿/拒答/异常/kind。d 带 orphan/refused/engine_kind 键。"""
    if d.get("orphan"):
        return "孤儿(指标失效)"
    if d.get("refused"):
        return "拒答/异常"
    return d.get("engine_kind", "?")


# ---------------- 8.2 编辑器回归闸门 ----------------
def gated_write(task_id: str, label: str, write_fn) -> None:
    """写盘前后各跑一遍【模板语料】比对,diff 存 session;corpus 空则直写零开销。

    write_fn 闭包负责:实际写盘 + (语义编辑时) reload_current_semantic + invalidate_grid。
    get_grid 凭 schema/excel mtime 自动在 write_fn 后重建 → after 取到新 grid。
    预期永不落盘:diff 里的 expected 全是现场重算值,仅本次会话展示。
    """
    import corpus_store as CS
    import corpus_run as CR
    entries = CS.load_corpus(task_id)
    before = None
    if entries:
        with st.spinner("🔁 跑语料回归闸门(应用前)…"):
            before = CR.run_items(entries, get_grid(), mode="template")
    write_fn()
    if before is not None:
        with st.spinner("🔁 跑语料回归闸门(应用后)…"):
            after = CR.run_items(entries, get_grid(), mode="template")
        st.session_state[f"_gate_{task_id}"] = {
            "label": label, "diff": CR.diff_runs(before, after),
        }


_GATE_EMOJI = {
    "regressed_verifiable": "🔴", "regressed_blindspot": "🟠",
    "orphaned": "⚪", "value_drift": "🟡",
    "recovered": "🟢", "improved": "🟢", "stable": "⚪",
}
_GATE_CN = {
    "regressed_verifiable": "可验证类回退(✓→✗)", "regressed_blindspot": "盲区类回退",
    "orphaned": "指标失效(孤儿)", "value_drift": "grid 口径变化",
    "recovered": "失效恢复", "improved": "改善(✗→✓)", "stable": "未变",
}


def render_corpus_gate(task_id: str) -> None:
    """若有最近一次被闸门守护的写盘,画 before/after diff 面板;两页 body 顶部调。"""
    data = st.session_state.get(f"_gate_{task_id}")
    if not data:
        return
    diff = data["diff"]
    c = diff["counts"]
    reg = c["regressed_verifiable"]
    vb, va = diff["n_verifiable_ok_before"], diff["n_verifiable_ok_after"]
    with st.container(border=True):
        head, _, close = st.columns([6, 2, 1])
        head.markdown(f"#### 🔁 回归闸门 · {data['label']}")
        if close.button("✕", key=f"_gate_close_{task_id}", help="关闭本次闸门报告"):
            st.session_state.pop(f"_gate_{task_id}", None)
            st.rerun()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("可验证类 ✓", f"{vb} → {va}",
                  delta=f"{va - vb:+d}", help="应用前后能复现真值的可验证类条数")
        m2.metric("回退(红线)", reg, delta=None,
                  help="✓→✗ 的可验证类,= 本次编辑引入的回归")
        m3.metric("grid 口径变化", c["value_drift"],
                  help="🟡 expected 值变了但命中不变 = grid 随编辑变化(你要的信号)")
        m4.metric("改善 / 失效", f"{c['improved']} / {c['orphaned']}")
        if reg > 0:
            st.error(f"⚠ {reg} 条可验证类回退(✓→✗)——检查是否改坏了取数口径。")
        if not diff["changes"]:
            st.success("语料全部稳定,本次编辑对回归集无影响。")
        else:
            st.markdown("**非稳定变化(按严重度):**")
            for ch in diff["changes"]:
                cls = ch["cls"]
                b, a = ch["before"], ch["after"]
                arrow = "✓→✗" if (b["match"] and not a["match"]) else \
                        "✗→✓" if (not b["match"] and a["match"]) else "·"
                valstr = ""
                if cls in ("value_drift",) or b.get("expected") != a.get("expected"):
                    valstr = f" · 期望 {fmt_val(b['expected'])} → {fmt_val(a['expected'])}"
                st.markdown(
                    f"{_GATE_EMOJI.get(cls,'·')} **{_GATE_CN.get(cls,cls)}** {arrow}{valstr}"
                    f"\n　　`{ch['op']}` {ch['phrasing']}"
                )


# ---------------- sidebar 任务选择器(每页顶部调用)----------------
def _page_link(label: str, icon: str) -> None:
    """page_link 容错:路径相对主入口解析,失败时降级为纯文本(不阻断 sidebar)。"""
    try:
        st.sidebar.page_link(_DATA_ACCESS_PAGE, label=label, icon=icon)
    except Exception:
        st.sidebar.markdown(f"{icon} {label}　_(数据接入页)_")


def render_task_sidebar():
    """渲染侧栏任务选择器 + 管理动作。返回当前 Task 或 None(无任务)。"""
    sb = st.sidebar
    sb.subheader("🗂 任务上下文")

    # 空则种入示例
    if not TS.list_tasks():
        TS.ensure_seed_task()
    tasks = TS.list_tasks()
    if not tasks:
        sb.warning("暂无任务。请到「数据接入」上传 Excel 创建。")
        _page_link("＋ 新建任务", "📤")
        return None

    ids = [t.id for t in tasks]
    cur = st.session_state.get("current_task_id")
    if cur not in ids:
        cur = ids[0]
        st.session_state["current_task_id"] = cur
    label_map = {t.id: f"{t.name} · {t.status}" for t in tasks}
    sel = sb.selectbox(
        "当前任务", ids, index=ids.index(cur),
        format_func=lambda i: label_map.get(i, i), key="_task_select",
    )
    if sel != cur:
        set_current_task_id(sel)
        st.rerun()

    t = TS.get_task(sel)
    sb.caption(
        f"📅 {t.created_at[:16].replace('T', ' ')}　📄 {t.excel_filename}　"
        f"{ICON_DET if t.schema_source in ('seed','template','manual') else ICON_LLM} {t.schema_source}"
    )
    _page_link("＋ 新建任务(上传 Excel)", "📤")

    with sb.expander("管理此任务"):
        nn = st.text_input("重命名", value=t.name, key="_task_rename_in")
        if st.button("保存名称", key="_task_rename_btn", use_container_width=True):
            if nn and nn != t.name:
                TS.rename_task(t.id, nn)
                st.rerun()
        st.divider()
        if st.checkbox("我确认删除(连同 schema/semantic)", key="_task_del_conf"):
            if st.button("🗑 删除此任务", key="_task_del_btn", type="primary",
                         use_container_width=True):
                TS.delete_task(t.id)
                st.session_state.pop("current_task_id", None)
                invalidate_grid()
                st.rerun()
    ensure_semantic_loaded()
    return t

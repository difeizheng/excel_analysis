"""AppTest 回归:语义层页「生成语义层」结果渲染路径不抛 StreamlitAPIException。

锁住的 bug(2026-06-24):`pages/3_🧠_语义层.py` 的 `_render_semantic_proposer` 曾把
"闸门详情" / 文件预览两个 `st.expander` 嵌套在外层"一键生成语义层"expander 内 →
Streamlit 禁止 expander 嵌套 → 点生成、拿到带 resolve_fail 的结果时抛
`StreamlitAPIException: Expanders may not be nested inside other expanders.`
(Phase 8.4 的 AppTest smoke 当场手跑、未落库,故漏网。)

修法:结果块移到外层 expander 之外作同级。本测试直接预置一个带 resolve_fail 的
生成结果(session_state `_semprop_{tid}`),触发"结果渲染"这条曾经崩溃的路径,
断言:(1) 无 exception;(2) 错误 banner 出现;(3) "闸门详情"作为顶层 expander 渲染。
不点按钮、不调 LLM —— 测的是渲染契约,不是 propose 逻辑(propose 有 test_semantic_proposer 覆盖)。
"""
from __future__ import annotations
import os
import sys
import glob

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))

import task_store as TS  # noqa: E402

try:
    from streamlit.testing.v1 import AppTest
    _HAS_APPTEST = True
except Exception:  # 老版本 streamlit 无 testing.v1
    _HAS_APPTEST = False


def _page3_path() -> str:
    """glob 取语义层页(文件名含中文/emoji,不硬编码)。"""
    matches = glob.glob(os.path.join(_ROOT, "pages", "3_*.py"))
    assert matches, "找不到 pages/3_*.py"
    return matches[0]


@pytest.fixture
def seeded_task(tmp_path, monkeypatch):
    """隔离 task_store 到 tmp,并从 committed seed 种一个真实任务(含 schema/semantic/excel)。"""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    monkeypatch.setattr(TS, "TASKS_DIR", str(tasks_dir))
    monkeypatch.setattr(TS, "REGISTRY_PATH", str(tasks_dir / "registry.json"))
    task = TS.ensure_seed_task()
    assert task is not None, "ensure_seed_task 未种入(committed schema / 测试数据.xls 缺失?)"
    return task


def _canned_box_with_resolve_fail() -> dict:
    """模拟 SP.propose 的返回:1 个指标,locator 落不到真实单元格(resolve_fail 非空)。

    取 resolve_fail 非空,正是为了命中"闸门详情"expander + 错误 banner 这条崩溃路径。
    """
    files = {fn: f"# {fn}\n" for fn in TS.SEM_FILES}
    gate = {
        "n_metrics": 1,
        "n_resolve_ok": 0,
        "resolve_fail": [
            {"metric": "利润总额", "msg": "行标签「不存在的行」在 财务数据 找不到"}
        ],
        "errors": [],
        "warns": [],
    }
    return {"files": files, "gate": gate}


@pytest.mark.skipif(not _HAS_APPTEST, reason="streamlit.testing.v1.AppTest 不可用")
def test_proposer_result_renders_without_nested_expander_crash(seeded_task):
    """预置生成结果 → 渲染语义层页 → 不抛嵌套 expander 异常,且结果块正常渲染。"""
    tid = seeded_task.id
    at = AppTest.from_file(_page3_path(), default_timeout=30)
    # 注入当前任务 + 一个带 resolve_fail 的生成结果(等价于"点完生成、propose 已返回")
    at.session_state["current_task_id"] = tid
    at.session_state[f"_semprop_{tid}"] = _canned_box_with_resolve_fail()
    at.run()

    # (1) 核心:不抛 StreamlitAPIException(嵌套 expander 回归)
    assert len(at.exception) == 0, (
        "页面抛异常(可能是 expander 嵌套复发): "
        + "; ".join(repr(getattr(e, "value", e)) for e in at.exception)
    )
    # (2) 错误 banner 渲染 → 证明结果块确实跑了(不是因 box 没生效而提前 return)
    assert any(
        "闸门发现 1 个问题" in (getattr(e, "value", "") or "")
        for e in at.error
    ), "未看到闸门错误 banner,结果块可能未渲染"
    # (3) "闸门详情"作为顶层 expander 渲染(修后为同级,非嵌套)
    assert any(
        getattr(e, "label", None) == "闸门详情" for e in at.expander
    ), '"闸门详情" expander 未出现'


def test_add_metric_button_no_widget_key_crash(seeded_task):
    """点「➕ 新增」指标 → 不抛 'cannot be modified after widget instantiated',
    且「新指标」落盘到 metrics.yaml。

    锁 widget-key 陷阱回归:`selectbox(key="_sem_metric")` 实例化后,旧代码在按钮
    handler 内直接 `st.session_state["_sem_metric"]=nm` → 抛 StreamlitAPIException。
    修法:改 widget key 必须走 `on_click=callback`(callback 在 widget 重渲染前执行)。
    """
    tid = seeded_task.id
    at = AppTest.from_file(_page3_path(), default_timeout=30)
    at.session_state["current_task_id"] = tid
    at.run()

    # 找到并点击「➕ 新增」(at.button 是 ElementList,按 label 过滤再 .click())
    add_btns = [b for b in at.button if getattr(b, "label", "") == "➕ 新增"]
    assert add_btns, "没找到「➕ 新增」按钮(metrics.yaml 可能为空?seed 应有指标)"
    add_btns[0].click()
    at.run()

    # (1) 核心:不抛 widget-key 异常
    assert len(at.exception) == 0, (
        "点「➕ 新增」抛异常(可能是 widget-key 陷阱复发): "
        + "; ".join(repr(getattr(e, "value", e)) for e in at.exception)
    )
    # (2) 新指标「新指标」落盘到当前任务 metrics.yaml(证明 callback 真的写了)
    metrics_path = os.path.join(seeded_task.semantic_dir, "metrics.yaml")
    assert os.path.exists(metrics_path), "metrics.yaml 未生成"
    assert "新指标" in open(metrics_path, encoding="utf-8").read(), "新指标未写入 metrics.yaml"


def test_add_category_button_no_widget_key_crash(seeded_task):
    """点「➕ 分类」(分类与别名 tab)→ 不抛 widget-key 异常,且「新分类」落盘 taxonomy.yaml。
    锁第 3 批 widget-key 陷阱的 _tax_cat 站点。"""
    tid = seeded_task.id
    at = AppTest.from_file(_page3_path(), default_timeout=30)
    at.session_state["current_task_id"] = tid
    at.run()
    btns = [b for b in at.button if getattr(b, "label", "") == "➕ 分类"]
    assert btns, "没找到「➕ 分类」按钮(seed taxonomy 须有分类树)"
    btns[0].click()
    at.run()
    assert len(at.exception) == 0, (
        "点「➕ 分类」抛异常(widget-key 陷阱复发): "
        + "; ".join(repr(getattr(e, "value", e)) for e in at.exception)
    )
    tax_path = os.path.join(seeded_task.semantic_dir, "taxonomy.yaml")
    assert os.path.exists(tax_path), "taxonomy.yaml 未生成"
    assert "新分类" in open(tax_path, encoding="utf-8").read(), "新分类未写入 taxonomy.yaml"


def test_add_node_button_no_widget_key_crash(seeded_task):
    """点「➕ 节点」(分类与别名 tab)→ 不抛 widget-key 异常,且「新节点」落盘 taxonomy.yaml。
    锁 _tax_node 站点(与 _tax_cat 同 callback 模式,独立测以覆盖另一个 widget key;
    应用此节点·重命名走 _on_apply_node callback,同模式不另测)。"""
    tid = seeded_task.id
    at = AppTest.from_file(_page3_path(), default_timeout=30)
    at.session_state["current_task_id"] = tid
    at.run()
    btns = [b for b in at.button if getattr(b, "label", "") == "➕ 节点"]
    assert btns, "没找到「➕ 节点」按钮(seed taxonomy 须有分类树)"
    btns[0].click()
    at.run()
    assert len(at.exception) == 0, (
        "点「➕ 节点」抛异常(widget-key 陷阱复发): "
        + "; ".join(repr(getattr(e, "value", e)) for e in at.exception)
    )
    tax_path = os.path.join(seeded_task.semantic_dir, "taxonomy.yaml")
    assert "新节点" in open(tax_path, encoding="utf-8").read(), "新节点未写入 taxonomy.yaml"

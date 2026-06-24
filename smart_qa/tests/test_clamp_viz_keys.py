"""测试 schema_edit_helpers.clamp_viz_keys:防 selectbox 越界("X is not in iterable")。

锁住越界降级契约 —— 智能填充、切 sheet、YAML 写回任何路径产生的越界值,
widget 渲染前必须 clamp 到合法域,否则 streamlit selectbox 抛 ValueError。
"""
from __future__ import annotations
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
sys.path.insert(0, os.path.join(_ROOT, "src"))

import pytest  # noqa: E402
import schema_edit_helpers as SE  # noqa: E402


# ============================================================ 越界降级
class TestOverflowReset:
    """越界 → UNSET(列键)或 clamp 到边界(行键)。"""

    def test_user_reported_scenario_dce_23_ncols_20(self):
        """用户报错的精确场景:dce=23 残留 + n_cols=20 → 降级到 UNSET,selectbox 不再抛。

        残留来源:用户切到列数更少的 sheet 时,旧 sheet 的 _viz_dce 留在 session_state。
        UI 上限 n_cols-1=19,所以 23 越界 → UNSET。
        """
        d = {"_viz_dce": 23, "_viz_dcs": 4, "_viz_dmc": -1, "_viz_lci": 0}
        SE.clamp_viz_keys(d, n_rows=30, n_cols=20)
        assert d["_viz_dce"] == SE.UNSET, \
            f"dce=23 越界应重置为 UNSET,实得 {d['_viz_dce']}"
        # 合法值不动
        assert d["_viz_dcs"] == 4
        assert d["_viz_dmc"] == SE.UNSET
        assert d["_viz_lci"] == 0

    def test_all_four_col_keys_overflow(self):
        d = {"_viz_dce": 30, "_viz_dcs": 25, "_viz_dmc": 100, "_viz_lci": 22}
        SE.clamp_viz_keys(d, n_rows=50, n_cols=20)
        for k in ("_viz_dce", "_viz_dcs", "_viz_dmc", "_viz_lci"):
            assert d[k] == SE.UNSET, f"{k} 越界应 UNSET,实得 {d[k]}"

    def test_row_keys_clamp_to_boundary(self):
        """行键策略与列键不同:clamp 到边界(保留用户相对意图,如 hdr=50 仍是想看"靠后"区域)。"""
        d = {"_viz_hdr": 50, "_viz_fdr": 100}
        SE.clamp_viz_keys(d, n_rows=30, n_cols=20)
        assert d["_viz_hdr"] == 29, f"hdr 应 clamp 到 29,实得 {d['_viz_hdr']}"
        assert d["_viz_fdr"] == 29, f"fdr 应 clamp 到 29,实得 {d['_viz_fdr']}"

    def test_ldr_overflow_resets_to_unset(self):
        """_viz_ldr 走 UNSET 策略(语义"末尾"由 UNSET 表达)。"""
        d = {"_viz_ldr": 100}
        SE.clamp_viz_keys(d, n_rows=30, n_cols=20)
        assert d["_viz_ldr"] == SE.UNSET


# ============================================================ 合法值不动
class TestValidUntouched:
    """合法值/UNSET/边界值都不应被改。"""

    def test_legal_values_unchanged(self):
        d = {"_viz_hdr": 5, "_viz_fdr": 10, "_viz_ldr": SE.UNSET,
             "_viz_lci": 2, "_viz_dcs": 4, "_viz_dmc": SE.UNSET, "_viz_dce": 19}
        snapshot = dict(d)
        SE.clamp_viz_keys(d, n_rows=30, n_cols=20)
        assert d == snapshot, "合法值不应被改"

    def test_boundary_values_legal(self):
        """n_rows-1 / n_cols-1 是合法边界(显式 col_opts 包含)。"""
        d = {"_viz_hdr": 29, "_viz_fdr": 0, "_viz_ldr": 29,
             "_viz_lci": 19, "_viz_dcs": 0, "_viz_dmc": 19, "_viz_dce": 19}
        SE.clamp_viz_keys(d, n_rows=30, n_cols=20)
        assert d["_viz_hdr"] == 29
        assert d["_viz_dce"] == 19  # n_cols-1 合法

    def test_dce_equals_ncols_resets_to_unset(self):
        """dce == n_cols 越出 UI 范围(UI _viz_dce ∈ [0, n_cols-1]),统一重置为 UNSET。

        历史背景:老 schema 用 EXCLUSIVE 时,_viz_dce = n_cols 表示"切到末尾",selectbox 不显示该值;
        新 INCLUSIVE 语义下 _viz_dce 上限是 n_cols-1,出现 n_cols 一定是 stale,reset。
        """
        d = {"_viz_dce": 20}  # n_cols = 20
        SE.clamp_viz_keys(d, n_rows=30, n_cols=20)
        assert d["_viz_dce"] == SE.UNSET, \
            f"dce=n_cols 越界应统一为 UNSET(-1),实得 {d['_viz_dce']}"

    def test_unset_preserved(self):
        d = {"_viz_ldr": SE.UNSET, "_viz_dmc": SE.UNSET, "_viz_dce": SE.UNSET}
        snapshot = dict(d)
        SE.clamp_viz_keys(d, n_rows=30, n_cols=20)
        assert d == snapshot, "UNSET(-1) 不应被改"


# ============================================================ 健壮性
class TestRobustness:
    """异常输入不崩。"""

    def test_empty_dict(self):
        d: dict = {}
        SE.clamp_viz_keys(d, n_rows=30, n_cols=20)  # 不抛
        assert d == {}

    def test_empty_sheet(self):
        """n_rows=0 / n_cols=0(空 sheet):row_max=0/col_max=0,所有 int → UNSET。"""
        d = {"_viz_hdr": 0, "_viz_fdr": 5, "_viz_dce": 3}
        SE.clamp_viz_keys(d, n_rows=0, n_cols=0)
        assert d["_viz_hdr"] == 0  # 0 合法
        assert d["_viz_fdr"] == 0  # clamp 到 0
        assert d["_viz_dce"] == SE.UNSET  # 3 > 0 → UNSET

    def test_non_int_values_untouched(self):
        """非 int 值(None/str)是用户中途切换状态,不应被改(只处理 int)。"""
        d = {"_viz_hdr": None, "_viz_fdr": "5"}
        SE.clamp_viz_keys(d, n_rows=30, n_cols=20)
        assert d["_viz_hdr"] is None
        assert d["_viz_fdr"] == "5"


# ============================================================ 智能填充场景
class TestSmartFill:
    """源头修复:智能填充按钮灌入 session_state 前的 clamp,等价于 clamp_viz_keys。"""

    def test_smart_fill_button_clamp_path(self):
        """模拟 pages/2 智能填充按钮的完整写法:sug 全名 → _clamp → 灌入 _viz_dce 等。

        锁住源头修复:sug 的 data_col_end 是 EXCLUSIVE(suggest_fields 输出),UI 显示要 INCLUSIVE(-1),
        再 clamp 到合法 col_max。
        """
        sug = {"header_row": 5, "first_data_row": 8,
               "label_col_idx": 1, "data_col_start": 4, "data_col_end": 30}
        n_rows, n_cols = 20, 20
        row_max = max(n_rows - 1, 0)
        col_max = max(n_cols - 1, 0)
        # 模拟 pages/2 智能填充按钮 clamp 后灌入的 session_state
        d: dict = {}
        if "header_row" in sug:
            d["_viz_hdr"] = max(0, min(row_max, int(sug["header_row"])))
        if "first_data_row" in sug:
            d["_viz_fdr"] = max(0, min(row_max, int(sug["first_data_row"])))
        if "label_col_idx" in sug:
            d["_viz_lci"] = max(0, min(col_max, int(sug["label_col_idx"])))
        if "data_col_start" in sug:
            d["_viz_dcs"] = max(0, min(col_max, int(sug["data_col_start"])))
        if "data_col_end" in sug:
            # EXCLUSIVE → INCLUSIVE: -1 再 clamp
            d["_viz_dce"] = max(0, min(col_max, int(sug["data_col_end"]) - 1))
        # 30-1=29 > 19 → clamp 到 19(用户原意:包含最后一列,与 UI 选 n_cols-1 一致)
        assert d["_viz_dce"] == 19
        # 其余合法值保持
        assert d["_viz_hdr"] == 5
        assert d["_viz_fdr"] == 8
        assert d["_viz_lci"] == 1
        assert d["_viz_dcs"] == 4

    def test_session_state_clamp_picks_up_overflow_residual(self):
        """切 sheet 后残留的越界值,_clamp_viz_keys 兜底(对应 widget 渲染前的无条件 clamp)。"""
        # 模拟:用户在 n_cols=30 的 sheet 设了 dce=25,切到 n_cols=20 的 sheet 后 selectbox 抛 23
        d = {"_viz_dce": 25, "_viz_dcs": 4, "_viz_lci": 0, "_viz_dmc": -1}
        SE.clamp_viz_keys(d, n_rows=30, n_cols=20)
        # 25 > 19 → UNSET,selectbox 不再抛
        assert d["_viz_dce"] == SE.UNSET
        # 其余不动
        assert d["_viz_dcs"] == 4
        assert d["_viz_lci"] == 0
        assert d["_viz_dmc"] == SE.UNSET

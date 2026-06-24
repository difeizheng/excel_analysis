"""loader.resolve_data_colkeys() 单测:数据范围全部列的 colkey 列表(供"构造预览"展示全空列)。

锁住:
- 列 idx 顺序与 Excel 列从左到右一致
- None colkey(表头为空)被过滤
- 显式 spec.columns 优先于表头派生
- data_col_end=None 时退化为 df.shape[1]
- data_col_end 越界被 clamp 到 df.shape[1]
"""
from __future__ import annotations
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))

import datetime as dt  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import loader  # noqa: E402
import schema_spec as SS  # noqa: E402


def _make_df(n_cols: int, header_cells: list) -> pd.DataFrame:
    """构造 n_cols 列的 DataFrame,第 0 行用 header_cells(不足补空)。"""
    pad = [""] * (n_cols - len(header_cells))
    row0 = list(header_cells) + pad
    return pd.DataFrame([row0])


def _spec(**kwargs) -> SS.TableSpec:
    """构造最小 TableSpec 用于单元测试(只关心数据列范围相关字段)。"""
    defaults = dict(name="t", header_row=0, first_data_row=1, target="row_map")
    defaults.update(kwargs)
    return SS.table_from_dict(defaults)


class TestResolveDataColkeys:
    def test_basic_range_with_year_headers(self):
        # Arrange
        df = _make_df(6, ["序号", "项目", "2018年", "2019年", "2020年", "2021年"])
        spec = _spec(data_col_start=2, data_col_end=5)
        # Act
        colkeys = loader.resolve_data_colkeys(spec, df)
        # Assert:EXCLUSIVE 5 → idx 2..4 → 3 列
        assert colkeys == ["2018年", "2019年", "2020年"]

    def test_full_range_no_data_col_end(self):
        # Arrange:无 data_col_end → 退化为 df.shape[1]
        df = _make_df(5, ["序号", "项目", "2018年", "2019年", "2020年"])
        spec = _spec(data_col_start=2, data_col_end=None)
        # Act
        colkeys = loader.resolve_data_colkeys(spec, df)
        # Assert:全部 5 - 2 = 3 列
        assert colkeys == ["2018年", "2019年", "2020年"]

    def test_explicit_data_col_end_equals_ncols_includes_last(self):
        # Arrange:data_col_end == n_cols → 含最后一列(语义"到末尾")
        df = _make_df(4, ["序号", "项目", "2018年", "合计"])
        spec = _spec(data_col_start=2, data_col_end=4)
        # Act
        colkeys = loader.resolve_data_colkeys(spec, df)
        # Assert:包含最后一列"合计"
        assert colkeys == ["2018年", "合计"]

    def test_data_col_end_exceeds_n_cols_clamped(self):
        # Arrange:stale YAML 里 data_col_end 大于实际 n_cols,被 clamp
        df = _make_df(4, ["序号", "项目", "2018年", "2019年"])
        spec = _spec(data_col_start=2, data_col_end=99)
        # Act
        colkeys = loader.resolve_data_colkeys(spec, df)
        # Assert:clamp 到 n_cols=4,包含 idx 2..3 = 2 列
        assert colkeys == ["2018年", "2019年"]

    def test_empty_header_filtered(self):
        # Arrange:表头行为空(None colkey)的列被过滤
        df = _make_df(5, ["序号", "项目", "2018年", "", "2020年"])
        spec = _spec(data_col_start=2, data_col_end=5)
        # Act
        colkeys = loader.resolve_data_colkeys(spec, df)
        # Assert:idx 3 表头为空 → 过滤
        assert colkeys == ["2018年", "2020年"]

    def test_datetime_header_normalized_to_ym(self):
        # Arrange:表头是 datetime 对象,_colkey 归一化为 YYYY-MM
        df = pd.DataFrame([["项目", dt.datetime(2026, 1, 1), dt.datetime(2026, 2, 1)]])
        spec = _spec(data_col_start=1, data_col_end=3)
        # Act
        colkeys = loader.resolve_data_colkeys(spec, df)
        # Assert
        assert colkeys == ["2026-01", "2026-02"]

    def test_explicit_columns_override_header(self):
        # Arrange:spec.columns 显式声明的列 idx/key 优先于表头派生
        df = _make_df(4, ["序号", "项目", "乱七八糟", "不应用"])
        spec = _spec(
            data_col_start=2, data_col_end=4,
            columns=[
                {"idx": 2, "role": "data", "key": "重命名 A"},
                {"idx": 3, "role": "data", "key": "重命名 B"},
            ],
        )
        # Act
        colkeys = loader.resolve_data_colkeys(spec, df)
        # Assert:用显式 key,不用表头派生
        assert colkeys == ["重命名 A", "重命名 B"]

    def test_no_data_col_start_returns_empty(self):
        # Arrange:无 data_col_start 时 loader 给空
        df = _make_df(4, ["序号", "项目", "2018年", "2019年"])
        spec = _spec(data_col_start=None, data_col_end=None)
        # Act
        colkeys = loader.resolve_data_colkeys(spec, df)
        # Assert
        assert colkeys == []

    def test_order_follows_idx_ascending(self):
        # Arrange:多列,验证顺序
        df = _make_df(8, ["lbl", "c1", "c2", "c3", "c4", "c5", "c6", "c7"])
        spec = _spec(data_col_start=2, data_col_end=7)
        # Act
        colkeys = loader.resolve_data_colkeys(spec, df)
        # Assert
        assert colkeys == ["c2", "c3", "c4", "c5", "c6"]
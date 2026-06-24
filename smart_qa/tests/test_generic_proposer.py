"""semantic_proposer 数据访问层泛型化测试。

锁住:build_label_inventory / _build_preview_grid 不依赖三峡 sheet 名(财务数据/装机/发电量),
分类维度从 schema detail_classifier_cols 动态发现(不写死 方式/区域)。
"""
from __future__ import annotations
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

import semantic_proposer as SP
from loader import Grid, Cell


def _cell(v, addr):
    return Cell(value=v, addr=addr, numeric=isinstance(v, (int, float)),
                row_idx=0, col_idx=0)


def _generic_grid() -> Grid:
    """合成非三峡 grid:一张 row_map 表(销售)+ 一张 detail 表(商品,分类维度=品类/产地)。"""
    g = Grid()
    g.row_maps["销售主表"] = {
        "收入": {"2024年": _cell(100, "销售!B2"), "2025年": _cell(120, "销售!C2")},
        "成本": {"2024年": _cell(60, "销售!B3"), "2025年": _cell(70, "销售!C3")},
    }
    g.details["商品明细"] = [
        {"name": "苹果", "品类": "水果", "产地": "山东",
         "values": {"2024年": _cell(10, "商品!B2")}},
        {"name": "煤炭", "品类": "矿产", "产地": "山西",
         "values": {"2024年": _cell(50, "商品!B3")}},
    ]
    g.table_index["销售主表"] = {"sheet": "销售", "target": "row_map"}
    g.table_index["商品明细"] = {"sheet": "商品", "target": "gen_detail"}
    return g


def _generic_schema() -> dict:
    return {
        "sheets": [
            {"name": "销售", "tables": [
                {"name": "销售主表", "target": "row_map", "label_col_idx": 0,
                 "first_data_row": 1, "header_row": 0, "data_col_start": 1}]},
            {"name": "商品", "tables": [
                {"name": "商品明细", "target": "gen_detail", "label_col_idx": 0,
                 "first_data_row": 1, "header_row": 0,
                 "detail_classifier_cols": {"name": 0, "品类": 1, "产地": 2}}]},
        ]
    }


class TestGenericInventory:
    def test_no_hardcoded_sheet_names(self):
        """inventory 不依赖财务数据/装机/发电量 —— 用任意 sheet 名也能抽。"""
        inv = SP.build_label_inventory(_generic_grid(), _generic_schema())
        names = {t["name"] for t in inv["tables"]}
        assert names == {"销售主表", "商品明细"}
        # 没有任何三峡 sheet 残留
        sheets = {t["sheet"] for t in inv["tables"]}
        assert sheets == {"销售", "商品"}

    def test_row_map_labels_extracted(self):
        inv = SP.build_label_inventory(_generic_grid(), _generic_schema())
        sales = next(t for t in inv["tables"] if t["name"] == "销售主表")
        assert sales["row_labels"] == ["收入", "成本"]
        assert "2024年" in sales["col_keys"]

    def test_classifier_dims_discovered_dynamically(self):
        """detail 分类维度从 schema detail_classifier_cols 动态发现,非写死 方式/区域。"""
        inv = SP.build_label_inventory(_generic_grid(), _generic_schema())
        detail = next(t for t in inv["tables"] if t["name"] == "商品明细")
        clf = detail["classifiers"]
        # 维度名 = schema 里的键(品类/产地),不是 方式/区域
        assert set(clf.keys()) == {"品类", "产地"}
        assert "水果" in clf["品类"] and "矿产" in clf["品类"]
        assert "山东" in clf["产地"] and "山西" in clf["产地"]
        # projects_sample 带动态维度字段
        assert detail["projects_sample"][0]["品类"] == "水果"

    def test_year_range_from_arbitrary_cols(self):
        inv = SP.build_label_inventory(_generic_grid(), _generic_schema())
        assert inv["year_range"] == (2024, 2025)


class TestGenericPreview:
    def test_preview_built_for_arbitrary_sheets(self):
        """_build_preview_grid 不依赖三峡 sheet 名。"""
        preview = SP._build_preview_grid(_generic_grid(), _generic_schema())
        assert "销售" in preview
        # 销售首表 label_col_idx=0 → 标签在第 0 列
        labels_at_col0 = {r[0] for r in preview["销售"] if r}
        assert "收入" in labels_at_col0 and "成本" in labels_at_col0

    def test_user_prompt_table_centric(self):
        """build_user_prompt 按【表】组织,不出现 Sheet: 财务数据 字样。"""
        inv = SP.build_label_inventory(_generic_grid(), _generic_schema())
        prompt = SP.build_user_prompt(inv)
        assert "销售主表" in prompt
        assert "品类" in prompt  # 动态分类维度进 prompt
        assert "财务数据" not in prompt

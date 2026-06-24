"""Grid 泛型化契约测试 —— 证明「sheet 清单不再与 Grid 派发绑定」。

老架构(Step 2 之前)的硬限制:
  - Grid 是固定 4 字段 fin/cap/gen_projects/gen_subtotals(三峡指纹)
  - load_grid 用 `if sh.name=="财务数据"→fin` 硬编码派发
  - 一个 sheet 只能映射到固定单字段;非三峡 sheet 名的 row_map 表被静默丢弃

泛型化后必须能做到(本文件锁住):
  - 同一 sheet 上挂多个 row_map 表,按逻辑表名(TableSpec.name)分别落桶
  - resolve_locator 用 table 键精确定位(主路径)
  - 任意陌生 sheet 名(非「财务数据/装机/发电量」)的 row_map 表能正常装载与取数
"""
from __future__ import annotations
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
sys.path.insert(0, os.path.join(_ROOT, "src"))

import loader
from loader import Grid, Cell


def _cell(v, addr):
    return Cell(value=v, addr=addr, numeric=isinstance(v, (int, float)),
                row_idx=0, col_idx=0)


# ============================================================ 数据结构层
class TestGenericContainer:
    """手构 Grid 直接验证泛型容器 + table 键解析(不依赖 Excel I/O)。"""

    def test_multiple_row_map_tables_on_same_sheet(self):
        """同一 sheet 上两个 row_map 表 —— 老架构 fin/cap 单字段做不到。"""
        g = Grid()
        g.row_maps["国内销售"] = {"收入": {"2024年": _cell(100, "销售!A2")}}
        g.row_maps["海外销售"] = {"收入": {"2024年": _cell(50, "销售!A3")}}
        g.table_index["国内销售"] = {"sheet": "销售", "target": "row_map"}
        g.table_index["海外销售"] = {"sheet": "销售", "target": "row_map"}

        # table 键主路径:两张同名行标签「收入」靠 table 名区分
        r1 = g.resolve_locator({"table": "国内销售", "row": "收入"})
        r2 = g.resolve_locator({"table": "海外销售", "row": "收入"})
        assert r1["2024年"].value == 100
        assert r2["2024年"].value == 50

        # iter_row_maps 两张表都遍历到
        rows = list(g.iter_row_maps())
        assert len(rows) == 2

    def test_subtotal_detail_table_key_resolution(self):
        """subtotal / detail 表同样按 table 名落桶、定位。"""
        g = Grid()
        g.subtotals["区域小计"] = {"合计": {"2024年": _cell(150, "数据!A2")}}
        g.details["项目明细"] = [{"name": "甲", "方式": "水电", "区域": "巴西",
                               "values": {"2024年": _cell(80, "数据!A3")}}]
        g.table_index["区域小计"] = {"sheet": "数据", "target": "gen_subtotals"}
        g.table_index["项目明细"] = {"sheet": "数据", "target": "gen_detail"}

        r = g.resolve_locator({"table": "区域小计", "row": "合计"})
        assert r["2024年"].value == 150
        assert len(list(g.iter_subtotals())) == 1
        assert len(list(g.iter_details())) == 1

    def test_backward_compat_aliases_still_project(self):
        """三峡遗留消费方读 fin/cap/gen_* —— property 别名仍按 (sheet,target) 投射。"""
        g = Grid()
        g.row_maps["财务主表"] = {"利润总额": {"2024年": _cell(9, "财务数据!A2")}}
        g.row_maps["装机主表"] = {"装机容量": {"2024年": _cell(20, "装机!A2")}}
        g.subtotals["年度小计"] = {"合计": {"2024年": _cell(100, "发电量!A2")}}
        g.details["年度明细"] = [{"name": "x", "方式": "水电", "区域": "巴西",
                               "values": {"2024年": _cell(40, "发电量!A3")}}]
        g.table_index["财务主表"] = {"sheet": "财务数据", "target": "row_map"}
        g.table_index["装机主表"] = {"sheet": "装机", "target": "row_map"}
        g.table_index["年度小计"] = {"sheet": "发电量", "target": "gen_subtotals"}
        g.table_index["年度明细"] = {"sheet": "发电量", "target": "gen_detail"}

        assert g.fin["利润总额"]["2024年"].value == 9
        assert g.cap["装机容量"]["2024年"].value == 20
        assert g.gen_subtotals["合计"]["2024年"].value == 100
        assert g.gen_projects[0]["values"]["2024年"].value == 40
        # 别名缺失时回落空容器(不抛错)
        g2 = Grid()
        assert g2.fin == {}
        assert g2.gen_projects == []


# ============================================================ load_grid 全链路
class TestLoadGridArbitraryExcel:
    """合成非三峡结构 Excel → schema → load_grid,证明任意格式可接入。

    老架构会因 sheet 名不是「财务数据/装机」而把这张表的 row_map 贡献静默丢弃;
    泛型化后按 TableSpec.name 装载,resolve_locator 用 table 键取数成功。
    """

    @pytest.fixture
    def synthetic_workbook(self, tmp_path):
        """写一张「销售」sheet 的 xlsx(三峡没有的 sheet 名)。"""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "销售"
        ws.append(["项目", "2024年", "2025年"])   # row0 表头
        ws.append(["收入", 100, 120])             # row1
        ws.append(["成本", 60, 70])               # row2
        xlsx_path = tmp_path / "synthetic.xlsx"
        wb.save(xlsx_path)

        schema_yaml = """
path: synthetic.xlsx
engine: openpyxl
version: "1"
sheets:
  - name: 销售
    tables:
      - name: 销售主表
        target: row_map
        header_row: 0
        first_data_row: 1
        label_col_idx: 0
        data_col_start: 1
"""
        schema_path = tmp_path / "synthetic.yaml"
        schema_path.write_text(schema_yaml, encoding="utf-8")
        return str(schema_path), str(xlsx_path)

    def test_arbitrary_sheet_loads_and_resolves(self, synthetic_workbook):
        schema_path, xlsx_path = synthetic_workbook
        g = loader.load_grid(schema_path, excel_path=xlsx_path)

        # 装进了以逻辑表名为键的通用容器(不是固定 fin/cap 字段)
        assert "销售主表" in g.row_maps
        assert "销售主表" in g.table_index
        assert g.table_index["销售主表"] == {"sheet": "销售", "target": "row_map"}

        # table 键定位取数
        r = g.resolve_locator({"table": "销售主表", "row": "收入"})
        assert r is not None
        assert r["2024年"].value == 100
        assert r["2025年"].value == 120

        # 成本行也在
        assert g.resolve_locator({"table": "销售主表", "row": "成本"})["2024年"].value == 60

        # iter_row_maps 能遍历到这张陌生 sheet 的表
        sheets_seen = {src for src, _label, _cells in g.iter_row_maps()}
        assert "销售" in sheets_seen

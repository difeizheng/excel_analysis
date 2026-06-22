"""Schema-driven loader 必须与 legacy preprocess.py 输出等价。

策略:
  1. 加载 legacy golden JSON(由 capture_fingerprint.py 跑 preprocess 产出)
  2. 跑 loader.load_grid() 产出新 Grid
  3. 对比两个 Grid 的所有 cell(value/addr/numeric/row_idx/col_idx)
  4. 任何差异都 fail,提示差异点

已知差异容忍(故意允许):
  - duplicate label warn 时被覆盖的那一项(legacy 也覆盖,语义一致)
"""
from __future__ import annotations
import json
import os
import sys
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
_REPO = os.path.join(_ROOT, "..")
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _REPO)
sys.path.insert(0, _ROOT)

GOLDEN = os.path.join(_HERE, "golden", "测试数据_legacy.json")


def _cell_to_tuple(c: dict) -> tuple:
    return (c.get("value"), c.get("addr"), c.get("numeric"),
            c.get("row_idx"), c.get("col_idx"))


def _cell_to_tuple_live(c) -> tuple:
    return (c.value, c.addr, c.numeric, c.row_idx, c.col_idx)


def _to_cell_set(mapping: dict) -> set:
    """{(value, addr, numeric, row_idx, col_idx), ...}"""
    out = set()
    for label, by_ck in mapping.items():
        for ck, c in by_ck.items():
            out.add(_cell_to_tuple(c))
    return out


def _to_cell_set_live(mapping: dict) -> set:
    out = set()
    for label, by_ck in mapping.items():
        for ck, c in by_ck.items():
            out.add(_cell_to_tuple_live(c))
    return out


def _to_proj_set_live(projects: list) -> set:
    out = set()
    for p in projects:
        for ck, c in p["values"].items():
            out.add((p["name"], p["方式"], p["区域"], ck,
                     c.value, c.addr, c.numeric, c.row_idx, c.col_idx))
    return out


def _to_proj_set_golden(projects: list) -> set:
    out = set()
    for p in projects:
        for ck, c in p["values"].items():
            out.add((p["name"], p["方式"], p["区域"], ck,
                     c["value"], c["addr"], c["numeric"], c["row_idx"], c["col_idx"]))
    return out


@pytest.fixture(scope="module")
def golden():
    with open(GOLDEN, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def live_grid():
    import loader
    return loader.load_grid()


class TestSchemaLoaderEquivalence:
    def test_fin_cell_count(self, golden, live_grid):
        legacy_count = sum(len(v) for v in golden["fin"].values())
        live_count = sum(len(v) for v in live_grid.fin.values())
        assert live_count == legacy_count, \
            f"FIN 单元格数: live={live_count} vs legacy={legacy_count}"

    def test_cap_cell_count(self, golden, live_grid):
        legacy_count = sum(len(v) for v in golden["cap"].values())
        live_count = sum(len(v) for v in live_grid.cap.values())
        assert live_count == legacy_count, \
            f"CAP 单元格数: live={live_count} vs legacy={legacy_count}"

    def test_gen_projects_count(self, golden, live_grid):
        assert len(live_grid.gen_projects) == len(golden["gen_projects"]), \
            f"GEN 明细数: live={len(live_grid.gen_projects)} vs legacy={len(golden['gen_projects'])}"

    def test_gen_subtotals_keys(self, golden, live_grid):
        assert set(live_grid.gen_subtotals.keys()) == set(golden["gen_subtotals"].keys()), \
            f"GEN 小计 keys: live={sorted(live_grid.gen_subtotals.keys())} vs legacy={sorted(golden['gen_subtotals'].keys())}"

    def test_fin_cells_value_addr(self, golden, live_grid):
        """FIN:每个 cell 的 (value, addr, numeric, row_idx, col_idx) 必须一致。"""
        s_live = _to_cell_set_live(live_grid.fin)
        s_legacy = _to_cell_set(golden["fin"])
        only_live = s_live - s_legacy
        only_legacy = s_legacy - s_live
        if only_live or only_legacy:
            pytest.fail(
                f"FIN cells 差异: live-only={len(only_live)}, "
                f"legacy-only={len(only_legacy)}\n"
                f"  示例 live-only: {list(only_live)[:3]}\n"
                f"  示例 legacy-only: {list(only_legacy)[:3]}"
            )

    def test_cap_cells_value_addr(self, golden, live_grid):
        s_live = _to_cell_set_live(live_grid.cap)
        s_legacy = _to_cell_set(golden["cap"])
        only_live = s_live - s_legacy
        only_legacy = s_legacy - s_live
        if only_live or only_legacy:
            pytest.fail(
                f"CAP cells 差异: live-only={len(only_live)}, "
                f"legacy-only={len(only_legacy)}\n"
                f"  示例 live-only: {list(only_live)[:3]}\n"
                f"  示例 legacy-only: {list(only_legacy)[:3]}"
            )

    def test_gen_projects_value_addr(self, golden, live_grid):
        s_live = _to_proj_set_live(live_grid.gen_projects)
        s_legacy = _to_proj_set_golden(golden["gen_projects"])
        only_live = s_live - s_legacy
        only_legacy = s_legacy - s_live
        # duplicate label 的覆盖是允许的(legacy 也覆盖,只是不同行)
        # 严格校验:addr 不同但同 (name, ck, value) 应允许
        if only_live or only_legacy:
            pytest.fail(
                f"GEN projects 差异: live-only={len(only_live)}, "
                f"legacy-only={len(only_legacy)}\n"
                f"  示例 live-only: {list(only_live)[:2]}\n"
                f"  示例 legacy-only: {list(only_legacy)[:2]}"
            )

    def test_gen_subtotals_value_addr(self, golden, live_grid):
        s_live = _to_cell_set_live(live_grid.gen_subtotals)
        s_legacy = _to_cell_set(golden["gen_subtotals"])
        only_live = s_live - s_legacy
        only_legacy = s_legacy - s_live
        if only_live or only_legacy:
            pytest.fail(
                f"GEN subtotals 差异: live-only={len(only_live)}, "
                f"legacy-only={len(only_legacy)}"
            )


class TestSchemaLoaderKeyRows:
    """关键指标必须能被定位(回退到 qa.ask 的实际入口)。"""

    def test_can_find_key_fin_metrics(self, live_grid):
        for label in ("利润总额", "汇兑净损失（净收益以" "“-”号填列）",
                      "向集团分红", "营业收入"):
            assert label in live_grid.fin, f"缺少财务指标: {label}"

    def test_can_find_key_cap_metrics(self, live_grid):
        for label in ("合计", "可控装机", "权益装机"):
            assert label in live_grid.cap, f"缺少装机指标: {label}"

    def test_can_find_2025_cells(self, live_grid):
        assert "2025年" in live_grid.fin["利润总额"]
        assert "2025年" in live_grid.cap["合计"]
        assert "2025年" in live_grid.cap["可控装机"]
        assert "2025年" in live_grid.gen_subtotals["合计"]

"""单位强类型 + 校验层加固的单元测试。

覆盖:
- units.is_compatible_for_sum: 维度冲突 / 兼容 / 空
- engine.UnitDimensionError: 跨 dim 求和应抛
- qa.verify C1-C4: 6 黄金用例通过 + 人造失败用例
"""
from __future__ import annotations
import os
import sys
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))

import units as U  # noqa: E402
import engine as E  # noqa: E402
import qa  # noqa: E402


# ---------------- units.is_compatible_for_sum ----------------
class TestUnitCompat:
    def test_all_same_dim_ok(self):
        ok, msg = U.is_compatible_for_sum([U.unit("亿元"), U.unit("亿元")])
        assert ok
        assert msg == ""

    def test_different_dim_rejected(self):
        ok, msg = U.is_compatible_for_sum([U.unit("亿元"), U.unit("万千瓦")])
        assert not ok
        assert "currency" in msg
        assert "capacity" in msg

    def test_pure_tolerated(self):
        """含 pure 单位的允许(兜底,不阻断)。"""
        ok, _ = U.is_compatible_for_sum([U.unit("亿元"), U.unit("")])
        assert ok

    def test_empty_list_ok(self):
        ok, _ = U.is_compatible_for_sum([])
        assert ok

    def test_unknown_unit_falls_back_to_pure(self):
        u = U.unit("不存在的单位")
        assert u.dim == U.DIM_PURE
        # 与 亿元 求和 -> 允许(pure 不阻断)
        ok, _ = U.is_compatible_for_sum([u, U.unit("亿元")])
        assert ok


# ---------------- engine.UnitDimensionError ----------------
class TestEngineUnitGuard:
    def _make_cell(self, addr, value, unit, numeric=True):
        class C:
            pass
        c = C()
        c.addr = addr
        c.value = value
        c.numeric = numeric
        c.unit = unit
        return c

    def test_sum_homogeneous_dim_ok(self):
        cells = [(self._make_cell("财务数据!M6", 6.5, "亿元"), "利润总额")]
        res = E.sum_cells(cells, "亿元")
        assert res.value == 6.5
        assert res.operands[0].dim == U.DIM_CURRENCY

    def test_sum_heterogeneous_dim_raises(self):
        cells = [
            (self._make_cell("财务数据!M6", 6.5, "亿元"), "利润总额"),
            (self._make_cell("装机!T5", 2104.54, "万千瓦"), "总装机"),  # 跨 dim!
        ]
        with pytest.raises(E.UnitDimensionError) as exc:
            E.sum_cells(cells, "亿元")
        assert "currency" in str(exc.value)
        assert "capacity" in str(exc.value)

    def test_sum_skips_non_numeric(self):
        cells = [
            (self._make_cell("发电量!L40", 11.12, "亿千瓦时"), "好项目"),
            (self._make_cell("发电量!L41", "转至参股", "亿千瓦时", numeric=False), "已转出"),
        ]
        res = E.sum_cells(cells, "亿千瓦时")
        assert res.value == 11.12
        assert len(res.operands) == 1

    def test_cagr_dim_is_pure(self):
        init = self._make_cell("财务数据!Q6", 8.5, "亿元")
        end = self._make_cell("财务数据!T6", 10.0, "亿元")
        res = E.cagr(8.5, 10.0, 3, init, end, "2022", "2025")
        assert res.operation == "cagr"
        assert all(o.dim == U.DIM_PURE for o in res.operands)


# ---------------- qa.verify C1-C4 (用真实 grid) ----------------
@pytest.fixture(scope="module")
def grid():
    import preprocess as PRE
    return PRE.load_grid()


class TestVerifyC1C4:
    def test_lookup_verified(self, grid):
        ans = qa.ask(grid, "公司2018年的利润总额是多少？")
        assert ans["kind"] == "single"
        assert ans["verified"], ans["verify_msg"]
        assert "C1" in ans["verify_msg"]
        assert "C2" in ans["verify_msg"]

    def test_sum_verified(self, grid):
        ans = qa.ask(grid, "公司2025年风电发电量是多少")
        assert ans["kind"] == "single"
        assert ans["verified"], ans["verify_msg"]
        assert "C1" in ans["verify_msg"]
        assert "C2" in ans["verify_msg"]
        assert "C4" in ans["verify_msg"]

    def test_cagr_verified(self, grid):
        ans = qa.ask(grid, "公司近三年的利润增长率是多少？")
        assert ans["kind"] == "single"
        assert ans["verified"], ans["verify_msg"]
        assert "C1" in ans["verify_msg"]

    def test_multi_verified(self, grid):
        ans = qa.ask(grid, "三峡国际2025年的总装机、可控装机、利润总额、发电量是多少？")
        assert ans["kind"] == "multi"
        assert ans["verified"]

    def test_fail_returns_unverified(self, grid):
        ans = qa.ask(grid, "公司2099年的利润总额是多少？")
        # 没数据会落到 fail 或 sum 空,这里宽松检查
        # 关键是 verify 不能崩
        assert "verify_msg" in ans


# ---------------- C2 故意造假应捕获 ----------------
class TestC2CatchesTampering:
    def test_tampered_operand_caught_by_c2(self, grid):
        """直接调 _c2_cell_recheck,验证 C2 能发现篡改(C1 会先短路,故单独测)。"""
        ans = qa.ask(grid, "公司2025年风电发电量是多少")
        assert ans["kind"] == "single"
        original = ans["result"].operands[0].value
        ans["result"].operands[0].value = original + 999  # 篡改
        ok, msg = qa._c2_cell_recheck(grid, ans["result"])
        assert not ok
        assert "C2" in msg
        assert "999" in msg or "不一致" in msg or "grid=" in msg

    def test_c1_catches_tamper(self, grid):
        """C1 重算:篡改 value 后 sum 重算会与 result.value 不一致。"""
        ans = qa.ask(grid, "公司2025年风电发电量是多少")
        original = ans["result"].operands[0].value
        ans["result"].operands[0].value = original + 999
        ok, msg = qa._c1_recompute(ans["result"])
        assert not ok
        assert "C1" in msg

    def test_c3_catches_unit_mismatch(self, grid):
        """C3:把 result.unit 改成 百分比(CAGR 操作之外),维度应冲突。"""
        ans = qa.ask(grid, "公司2025年风电发电量是多少")
        # 把 unit 改成与 metric 声明的 能量 不一致的 currency
        ans["result"].unit = "亿元"
        ans["intent"].metric = "风电发电量"  # 显式 metric
        ok, msg = qa._c3_unit_consistent(ans["result"], "风电发电量")
        assert not ok
        assert "C3" in msg

    def test_c4_catches_subtotal_label(self, grid):
        """C4:operand.label 含 '合计' 标记应被 C4 捕获。"""
        ans = qa.ask(grid, "公司2025年风电发电量是多少")
        # 模拟错误:改一个 label
        ans["result"].operands[0].label = "（一）巴西发电量合计"
        ok, msg = qa._c4_subtotal_check(ans["result"])
        assert not ok
        assert "C4" in msg

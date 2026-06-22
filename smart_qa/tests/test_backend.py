"""Backend 抽象层:MemoryBackend 与 SqliteBackend 逐原语等价。

核心保证 —— SQLite 接入查询链后,取数语义与内存引擎一致:
  - _match_row 前缀匹配(覆盖带"（...）"后缀的行名)
  - 区域小计行(走 cells.区域 列)
  - 发电量合计(emit_key="合计")
  - taxonomy 归并(addr 集合 + 求和值)
  - cagr 期初/期末取数
  - cell_by_addr(C2 回查)
"""
from __future__ import annotations
import os
import sys
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

import backend as B  # noqa: E402
import loader  # noqa: E402


@pytest.fixture(scope="module")
def grid():
    return loader.load_grid()


@pytest.fixture(scope="module")
def backends(grid, db_built):
    """返回 (MemoryBackend, SqliteBackend) 一对。db_built 由 conftest session fixture 提供。"""
    return B.MemoryBackend(grid), B.SqliteBackend(db_built)


class TestLookupEquivalence:
    """_match_row 前缀匹配在两端一致(覆盖带后缀的行名)。"""

    def test_profit_normal_row(self, backends):
        mem, sql = backends
        m = mem.lookup("利润总额", "三峡国际", "2018年")
        s = sql.lookup("利润总额", "三峡国际", "2018年")
        assert m and s
        assert m[0].addr == s[0].addr
        assert abs(float(m[0].value) - float(s[0].value)) < 1e-9

    def test_huiloss_prefix_match(self, backends):
        """汇兑净损失 —— 内存 key/SQL row_label 带'（净收益以"-"号填列）'后缀,前缀匹配两端一致。"""
        mem, sql = backends
        m = mem.lookup("汇兑净损失", "三峡国际", "2022年")
        s = sql.lookup("汇兑净损失", "三峡国际", "2022年")
        assert m and s, "前缀匹配应定位到带后缀的行"
        assert m[0].addr == s[0].addr
        assert abs(float(m[0].value) - float(s[0].value)) < 1e-9

    def test_cap_normal_row(self, backends):
        mem, sql = backends
        m = mem.lookup("可控装机", "三峡国际", "2025年")
        s = sql.lookup("可控装机", "三峡国际", "2025年")
        assert m and s
        assert m[0].addr == s[0].addr

    def test_region_subtotal_brazil(self, backends):
        """巴西发电量 —— 区域小计行,SQLite 走 区域 列查询。"""
        mem, sql = backends
        m = mem.lookup("巴西发电量", "三峡国际", "2025年")
        s = sql.lookup("巴西发电量", "三峡国际", "2025年")
        assert m and s, "区域小计应可定位"
        assert m[0].addr == s[0].addr
        assert abs(float(m[0].value) - float(s[0].value)) < 1e-9

    def test_gen_total_emits_key(self, backends):
        """发电量(合计行,locator.row='发电量合计' → emit_key='合计')。"""
        mem, sql = backends
        m = mem.lookup("发电量", "三峡国际", "2025年")
        s = sql.lookup("发电量", "三峡国际", "2025年")
        assert m and s
        assert abs(float(m[0].value) - float(s[0].value)) < 1e-9


class TestTaxonomyEquivalence:
    def test_wind_taxonomy_addr_set_and_sum(self, backends):
        mem, sql = backends
        m = mem.taxonomy_cells("风电发电量", "2025年")
        s = sql.taxonomy_cells("风电发电量", "2025年")
        # addr 集合一致(顺序可能不同:内存按 gen_projects 序,SQL 按返回序)
        assert {c.addr for c, _ in m} == {c.addr for c, _ in s}
        # 求和值一致
        assert abs(sum(float(c.value) for c, _ in m) -
                   sum(float(c.value) for c, _ in s)) < 1e-9

    def test_hydro_taxonomy(self, backends):
        mem, sql = backends
        m = mem.taxonomy_cells("水电发电量", "2025年")
        s = sql.taxonomy_cells("水电发电量", "2025年")
        assert {c.addr for c, _ in m} == {c.addr for c, _ in s}


class TestCagrAndAddr:
    def test_profit_cagr_cells(self, backends):
        mem, sql = backends
        m = mem.cagr_cells("利润总额", 2022, 2025)
        s = sql.cagr_cells("利润总额", 2022, 2025)
        assert m and s
        assert m[0].addr == s[0].addr   # 期初
        assert m[1].addr == s[1].addr   # 期末

    def test_cell_by_addr_roundtrip(self, backends):
        mem, sql = backends
        # 取一个已知 addr,两端 cell_by_addr 应一致
        pair = mem.lookup("利润总额", "三峡国际", "2018年")
        addr = pair[0].addr
        mv = mem.cell_by_addr(addr)
        sv = sql.cell_by_addr(addr)
        assert mv and sv
        assert abs(float(mv.value) - float(sv.value)) < 1e-9

    def test_cell_by_addr_missing(self, backends):
        mem, sql = backends
        assert mem.cell_by_addr("不存在!Z999") is None
        assert sql.cell_by_addr("不存在!Z999") is None

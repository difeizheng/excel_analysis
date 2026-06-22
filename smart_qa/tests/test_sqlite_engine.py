"""SQLite 引擎必须与 in-memory 引擎输出等价。

策略:
  1. 加载 in-memory Grid + 同源 SQLite
  2. 抽样关键指标比对 .get() / .find_by_方式() 结果
  3. 全量对比 cells 数 + 每个 addr 对应的 value
"""
from __future__ import annotations
import os
import sqlite3
import sys
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, ".."))

DB_PATH = os.path.join(_ROOT, "data", "grid.db")


@pytest.fixture(scope="module")
def db_built():
    if not os.path.exists(DB_PATH):
        from to_sqlite import build_db
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        build_db("测试数据.xls", DB_PATH)
    return DB_PATH


@pytest.fixture(scope="module")
def grid():
    import loader
    return loader.load_grid()


@pytest.fixture(scope="module")
def sgrid(db_built):
    from to_sqlite import SqliteGrid
    g = SqliteGrid(db_built)
    yield g
    g.close()


class TestSqliteBuild:
    def test_db_exists_and_nonempty(self, db_built):
        assert os.path.exists(db_built)
        conn = sqlite3.connect(db_built)
        n = conn.execute("SELECT COUNT(*) FROM cells").fetchone()[0]
        conn.close()
        assert n > 500, f"cells 行数太少: {n}"

    def test_three_sheets_present(self, db_built):
        conn = sqlite3.connect(db_built)
        sheets = [r[0] for r in conn.execute("SELECT DISTINCT sheet FROM cells")]
        conn.close()
        assert set(sheets) == {"财务数据", "装机", "发电量"}


class TestSqliteEquivalence:
    def test_profit_2018(self, grid, sgrid):
        # in-memory
        c = grid.fin["利润总额"]["2018年"]
        # sqlite
        r = sgrid.get("财务数据", "利润总额", "2018年")
        assert r is not None
        assert r["addr"] == c.addr
        assert float(r["value"]) == float(c.value)

    def test_controllable_cap_2025(self, grid, sgrid):
        c = grid.cap["可控装机"]["2025年"]
        r = sgrid.get("装机", "可控装机", "2025年")
        assert r is not None
        assert r["addr"] == c.addr
        assert abs(float(r["value"]) - float(c.value)) < 1e-6

    def test_wind_sum_via_sqlite(self, grid, sgrid):
        """风电求和:在内存端用 taxonomy;在 sqlite 端用 find_by_方式。"""
        import semantic_layer as S
        metric = "风电发电量"
        subs = S.expand_taxonomy(metric)  # 陆上风电, 海上风电
        # in-memory sum
        total_mem = 0.0
        for sub in subs:
            for p in grid.gen_projects:
                if p["方式"] == sub:
                    c = p["values"].get("2025年")
                    if c and c.numeric:
                        total_mem += float(c.value)
        # sqlite sum(在 python 端聚合,SQLite 引擎层只做查询)
        total_sql = 0.0
        for sub in subs:
            for r in sgrid.find_by_方式("发电量", sub):
                if r["col_key"] == "2025年" and r["numeric"] is not None:
                    total_sql += float(r["numeric"])
        assert abs(total_mem - total_sql) < 1e-6
        # 与已知正确答案 39.905 对照
        assert abs(total_mem - 39.905) < 0.01

    def test_brazil_subtotal_equals_sum_of_陆上风电_brazil(self, grid, sgrid):
        """巴西小计 vs 巴西陆上风电明细之和(口径一致性)。"""
        sub = sgrid.get("发电量", "小计_巴西", "2025年")
        assert sub is not None
        # 巴西明细:区域='巴西' 且 is_subtotal=0
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        detail_sum = conn.execute(
            "SELECT SUM(numeric) FROM cells "
            "WHERE sheet='发电量' AND 区域='巴西' AND is_subtotal=0 AND col_key='2025年' AND 方式='陆上风电'"
        ).fetchone()[0] or 0.0
        conn.close()
        # 巴西小计应 >= 陆上风电明细(可能还含其他方式)
        assert float(sub["value"]) >= detail_sum - 1e-6


class TestSqliteFullEnumeration:
    """全量遍历:SQLite 中每个 addr 对应的 numeric 应与 in-memory 一致。"""

    def test_all_cap_cells(self, grid, sgrid):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT addr, value, numeric FROM cells WHERE sheet='装机'"
        ).fetchall()
        conn.close()
        # 在内存端建立 addr -> numeric 索引
        mem = {}
        for label, by_ck in grid.cap.items():
            for ck, c in by_ck.items():
                mem[c.addr] = float(c.value) if c.numeric else None
        diff = []
        for r in rows:
            v_sql = r["numeric"] if r["numeric"] is not None else None
            v_mem = mem.get(r["addr"])
            if v_sql is None and v_mem is None:
                continue
            if v_sql is None or v_mem is None or abs(v_sql - v_mem) > 1e-6:
                diff.append((r["addr"], v_sql, v_mem))
        assert not diff, f"装机 cells 差异(前5): {diff[:5]}"

    def test_all_fin_cells(self, grid, sgrid):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT addr, value, numeric FROM cells WHERE sheet='财务数据'"
        ).fetchall()
        conn.close()
        mem = {}
        for label, by_ck in grid.fin.items():
            for ck, c in by_ck.items():
                mem[c.addr] = float(c.value) if c.numeric else None
        diff = []
        for r in rows:
            v_sql = r["numeric"] if r["numeric"] is not None else None
            v_mem = mem.get(r["addr"])
            if v_sql is None and v_mem is None:
                continue
            if v_sql is None or v_mem is None or abs(v_sql - v_mem) > 1e-6:
                diff.append((r["addr"], v_sql, v_mem))
        assert not diff, f"财务 cells 差异(前5): {diff[:5]}"

"""把 Grid 落到 SQLite,作为 in-memory 引擎的并行选项。

数据模型: 一张 cells 表,带 source_cells 字段(物理地址)做溯源。
- in-memory 引擎: 直接遍历 grid.fin / grid.cap / grid.gen_* 字典
- SQLite 引擎: 跑 SQL 查询;适合大数据量/交叉查询/远程访问场景

用法:
    python -m src.to_sqlite 测试数据.xls data/grid.db
    # 或
    from to_sqlite import build_db
    n = build_db('测试数据.xls', 'data/grid.db')
"""
from __future__ import annotations
import logging
import os
import sqlite3
from typing import Any

import loader

log = logging.getLogger(__name__)

DDL = """
DROP TABLE IF EXISTS cells;
CREATE TABLE cells (
    id          INTEGER PRIMARY KEY,
    sheet       TEXT NOT NULL,
    row_label   TEXT NOT NULL,
    col_key     TEXT NOT NULL,
    value       TEXT,
    numeric     REAL,
    addr        TEXT NOT NULL,            -- 物理地址,如 '发电量!L14'
    is_subtotal INTEGER NOT NULL DEFAULT 0,
    project_name TEXT,                    -- 仅 gen_projects
    方式        TEXT,                     -- 发电方式分类
    区域        TEXT,                     -- 区域
    UNIQUE(sheet, addr)
);
CREATE INDEX idx_cells_sheet_row      ON cells(sheet, row_label, col_key);
CREATE INDEX idx_cells_sheet_region   ON cells(sheet, 区域);
CREATE INDEX idx_cells_方式           ON cells(方式);
CREATE INDEX idx_cells_subtotal       ON cells(sheet, is_subtotal);
"""


def build_db(xls_path: str, db_path: str) -> int:
    """从 Excel 加载并写入 SQLite。返回插入行数。"""
    grid = loader.load_grid()
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(DDL)
        rows: list[tuple] = []
        # row_map 表(财务数据/装机)——经 Grid.iter_row_maps 统一遍历
        for sheet, label, by_ck in grid.iter_row_maps():
            for ck, c in by_ck.items():
                rows.append((
                    sheet, label, ck,
                    None if c.value is None else str(c.value),
                    c.value if c.numeric else None,
                    c.addr, 0, None, None, None,
                ))
        # 发电量 明细(gen_projects)
        for _sheet, p in grid.iter_details():
            for ck, c in p["values"].items():
                rows.append((
                    "发电量", p["name"], ck,
                    None if c.value is None else str(c.value),
                    c.value if c.numeric else None,
                    c.addr, 0, p["name"], p.get("方式"), p.get("区域"),
                ))
        # 发电量 小计(gen_subtotals)
        for _sheet, region, by_ck in grid.iter_subtotals():
            for ck, c in by_ck.items():
                rows.append((
                    "发电量", f"小计_{region}", ck,
                    None if c.value is None else str(c.value),
                    c.value if c.numeric else None,
                    c.addr, 1, None, None, region,
                ))
        conn.executemany(
            "INSERT INTO cells (sheet, row_label, col_key, value, numeric, "
            "addr, is_subtotal, project_name, 方式, 区域) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        n = len(rows)
        log.info("Wrote %d cells to %s", n, db_path)
        return n
    finally:
        conn.close()


class SqliteGrid:
    """对 cells 表的查询封装。提供与 loader.Grid 相似的查询方法。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteGrid":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def get(self, sheet: str, row_label: str, col_key: str) -> dict | None:
        """单点取数:返回 {'value', 'addr', 'numeric'} 或 None。"""
        cur = self._conn.execute(
            "SELECT value, numeric, addr FROM cells "
            "WHERE sheet=? AND row_label=? AND col_key=? "
            "LIMIT 1",
            (sheet, row_label, col_key),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "value": row["numeric"] if row["numeric"] is not None else row["value"],
            "addr": row["addr"],
            "numeric": row["numeric"] is not None,
        }

    def find_by_方式(self, sheet: str, 方式: str) -> list[dict]:
        """分类归并:取 sheet 下 发电方式=... 的所有项目明细。"""
        cur = self._conn.execute(
            "SELECT row_label, col_key, value, numeric, addr, 区域 "
            "FROM cells WHERE sheet=? AND 方式=? AND is_subtotal=0",
            (sheet, 方式),
        )
        return [dict(r) for r in cur.fetchall()]

    def row_labels(self, sheet: str) -> list[str]:
        cur = self._conn.execute(
            "SELECT DISTINCT row_label FROM cells WHERE sheet=? ORDER BY row_label",
            (sheet,),
        )
        return [r["row_label"] for r in cur.fetchall()]

    def cell_by_addr(self, addr: str) -> dict | None:
        """按物理地址精确取一行(供 C2 单元格回查 / 前端溯源高亮)。

        返回 {'value','addr','numeric'} 或 None,与 get() 返回形态一致。
        """
        cur = self._conn.execute(
            "SELECT value, numeric, addr FROM cells WHERE addr=? LIMIT 1",
            (addr,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "value": row["numeric"] if row["numeric"] is not None else row["value"],
            "addr": row["addr"],
            "numeric": row["numeric"] is not None,
        }

    def stats(self) -> dict:
        cur = self._conn.execute(
            "SELECT sheet, COUNT(*) AS n FROM cells GROUP BY sheet"
        )
        return {r["sheet"]: r["n"] for r in cur.fetchall()}


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("xls", help="Excel 文件路径")
    p.add_argument("db", help="输出 SQLite 路径")
    args = p.parse_args()
    n = build_db(args.xls, args.db)
    print(f"[OK] {n} cells -> {args.db}")
    with SqliteGrid(args.db) as g:
        print("  stats:", g.stats())


if __name__ == "__main__":
    main()

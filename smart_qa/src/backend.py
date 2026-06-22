"""取数后端抽象:把"指标 + 时间"翻译成 CellView,运算永远留在 engine.py。

两个实现,产出**结构等价**的取数结果,供双引擎互验:
- MemoryBackend: 包内存 Grid,搬用 pipeline 的 _match_row / _colkey 语义
- SqliteBackend: 走 cells 表 SQL,在 Python 层复刻 _match_row 前缀匹配

为什么 engine 不用改:engine.lookup / sum_cells / cagr 全程鸭子类型
(用 cell.value / cell.addr / cell.numeric,unit 走 getattr fallback)。
CellView 只暴露这三属性即可复用 engine 的全部运算(含单位 dim 闸门)。

双引擎互验(backend="both")的物理基础即此抽象:两条独立代码路径
取到同一组 CellView → engine 算出同一结果 → 不一致即暴露 bug。
"""
from __future__ import annotations
import os
import sqlite3
from dataclasses import dataclass

import semantic_layer as S


# ============================================================ 轻量取数结果
@dataclass(frozen=True)
class CellView:
    """engine 鸭子类型兼容的取数结果。

    无 unit 字段:engine 用 getattr(cell, "unit", unit) 兜底到外层传入的 unit,
    故 SQLite 端不必在 cells 表存 unit。
    """
    addr: str
    value: object          # float | str | None
    numeric: bool


# ============================================================ 时间 token → 列键(与 pipeline._colkey 同语义)
def _colkey(token) -> str:
    """时间 token -> 列键。YTD 规则:'1-N月' 取第 N 月列(当年累计)。"""
    if token[0] == "year":
        return f"{token[1]}年"
    if token[0] == "ytd_month":
        return f"{token[1]}-{token[2]:02d}"
    raise ValueError(f"无法解析时间 token: {token}")


def _match_row_label(candidates: list[str], want: str) -> str | None:
    """复刻 pipeline._match_row 语义,在候选 row_label 列表上做匹配。

    策略:精确 -> 取"（"前缀 startswith。返回匹配到的真实 label(供精确 SQL 查询)。
    """
    if not want:
        return None
    if want in candidates:
        return want
    prefix = want.split("（")[0]
    if not prefix:
        return None
    for k in candidates:
        if k == want or k.startswith(prefix):
            return k
    return None


def _match_row_struct(struct: dict, row_label: str):
    """复刻 pipeline._match_row,在内存结构 dict 上定位行字典。"""
    if not row_label:
        return None
    if row_label in struct:
        return struct[row_label]
    prefix = row_label.split("（")[0]
    for k, v in struct.items():
        if k == row_label or k.startswith(prefix):
            return v
    return None


# 发电量小计行的 row -> gen_subtotals/区域键 映射(与 pipeline._row_for_metric 同源)
_SUBTOTAL_KEY_MAP = {"发电量合计": "合计"}


def _subtotal_region_key(loc_row: str) -> str:
    return _SUBTOTAL_KEY_MAP.get(loc_row, loc_row)


# ============================================================ 默认路径 + 建库保障
def default_db_path() -> str:
    """smart_qa/data/grid.db(相对本文件位置推导)。"""
    here = os.path.dirname(os.path.abspath(__file__))      # smart_qa/src
    return os.path.join(os.path.dirname(here), "data", "grid.db")


def default_xls_path() -> str:
    """excel_analysis/测试数据.xls(仓库根)。"""
    here = os.path.dirname(os.path.abspath(__file__))      # smart_qa/src
    smart_qa = os.path.dirname(here)
    return os.path.join(os.path.dirname(smart_qa), "测试数据.xls")


def ensure_db(db_path: str | None = None) -> str:
    """SQLite 模式前确保 db 存在,缺失则从 Excel 落库。返回 db 绝对路径。"""
    db_path = db_path or default_db_path()
    if not os.path.exists(db_path):
        import loader
        from to_sqlite import build_db
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        build_db(default_xls_path(), db_path)   # build_db 内部 loader.load_grid(),xls 参数仅占位
    return db_path


# ============================================================ MemoryBackend
class MemoryBackend:
    """包内存 Grid 的取数后端(默认,零外部依赖)。"""

    def __init__(self, grid) -> None:
        self.grid = grid

    def close(self) -> None:
        pass

    def __enter__(self) -> "MemoryBackend":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _locate_row(self, metric: str):
        """复刻 pipeline._row_for_metric:返回行字典 {col_key: Cell} 或 None。"""
        loc = S.metric_info(metric).get("locator", {})
        sheet = loc.get("sheet")
        if sheet == "财务数据":
            return _match_row_struct(self.grid.fin, loc.get("row", ""))
        if sheet == "装机":
            return _match_row_struct(self.grid.cap, loc.get("row", ""))
        if sheet == "发电量":
            if loc.get("row"):                       # 合计/区域小计
                return self.grid.gen_subtotals.get(_subtotal_region_key(loc["row"]))
            return None                              # 项目明细走 taxonomy
        return None

    def lookup(self, metric: str, entity: str, col_key: str):
        row = self._locate_row(metric)
        cell = row.get(col_key) if row else None
        if not cell or not getattr(cell, "numeric", False):
            return None
        return CellView(cell.addr, cell.value, cell.numeric), f"{metric}·{entity}·{col_key}"

    def cumulative_cells(self, metric: str, time_tokens: list):
        row = self._locate_row(metric)
        out = []
        for tk in time_tokens:
            ck = _colkey(tk)
            cell = row.get(ck) if row else None
            ytd = "(YTD当月累计)" if tk[0] == "ytd_month" else ""
            label = f"{metric}·{ck}{ytd}"
            if cell:
                out.append((CellView(cell.addr, cell.value, cell.numeric), label))
            else:
                out.append((None, label))
        return out

    def taxonomy_cells(self, metric: str, col_key: str):
        subs = S.expand_taxonomy(metric)
        out = []
        for p in self.grid.gen_projects:
            if p.get("方式") in subs:
                c = p["values"].get(col_key)
                if c:
                    out.append((CellView(c.addr, c.value, c.numeric),
                                f"{p['name']}({p['方式']})"))
        return out

    def cagr_cells(self, base_metric: str, init_year: int, end_year: int):
        row = self._locate_row(base_metric)
        if not row:
            return None
        init_c = row.get(f"{init_year}年")
        end_c = row.get(f"{end_year}年")
        if not init_c or not end_c:
            return None
        return (CellView(init_c.addr, init_c.value, init_c.numeric),
                CellView(end_c.addr, end_c.value, end_c.numeric))

    def year_cells(self, metric: str, entity: str) -> list[tuple[int, CellView]]:
        """某指标行【所有年份】的 (year, CellView)(peak_year 用)。

        复刻 _locate_row:fin/cap 行 或 发电量小计行;col_key 形如 '2018年'。
        只返回数值单元格。两后端语义一致 → C5 双引擎可对 peak_year 交叉校验。
        """
        row = self._locate_row(metric)
        out: list[tuple[int, CellView]] = []
        if not row:
            return out
        for ck, cell in row.items():
            if not (isinstance(ck, str) and ck.endswith("年")):
                continue
            if not cell or not getattr(cell, "numeric", False):
                continue
            try:
                y = int(ck[:-1])
            except ValueError:
                continue
            out.append((y, CellView(cell.addr, cell.value, cell.numeric)))
        return out

    def cell_by_addr(self, addr: str) -> CellView | None:
        """C2 单元格回查:按 addr 反查(内存端遍历 grid)。"""
        c = _lookup_cell_in_grid(self.grid, addr)
        if not c:
            return None
        return CellView(c.addr, c.value, getattr(c, "numeric", False))


def _lookup_cell_in_grid(grid, addr: str):
    """从内存 grid 反查 cell(原 qa._cell_lookup_in_grid 逻辑迁此)。"""
    if not addr or "!" not in addr:
        return None
    sheet, _ref = addr.split("!", 1)
    if sheet == "财务数据" and grid.fin:
        for _label, cells in grid.fin.items():
            for _ck, cell in cells.items():
                if cell.addr == addr:
                    return cell
    if sheet == "装机" and grid.cap:
        for _label, cells in grid.cap.items():
            for _ck, cell in cells.items():
                if cell.addr == addr:
                    return cell
    if sheet == "发电量":
        for p in grid.gen_projects:
            for _ck, cell in p["values"].items():
                if cell.addr == addr:
                    return cell
        for _region, cells in grid.gen_subtotals.items():
            for _ck, cell in cells.items():
                if cell.addr == addr:
                    return cell
    return None


# ============================================================ SqliteBackend
class SqliteBackend:
    """走 cells 表 SQL 的取数后端(可切换 / 适合交叉查询 / 远程访问)。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._labels_cache: dict[str, list[str]] = {}

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteBackend":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- 内部查询原语 ----
    def _labels_of(self, sheet: str) -> list[str]:
        if sheet not in self._labels_cache:
            cur = self._conn.execute(
                "SELECT DISTINCT row_label FROM cells WHERE sheet=?", (sheet,))
            self._labels_cache[sheet] = [r["row_label"] for r in cur.fetchall()]
        return self._labels_cache[sheet]

    def _match_label(self, sheet: str, want: str) -> str | None:
        return _match_row_label(self._labels_of(sheet), want)

    @staticmethod
    def _row_to_view(r) -> CellView | None:
        if r is None:
            return None
        is_num = r["numeric"] is not None
        val = r["numeric"] if is_num else r["value"]
        return CellView(r["addr"], val, is_num)

    def _row_get(self, sheet: str, row_label: str, col_key: str) -> CellView | None:
        cur = self._conn.execute(
            "SELECT value, numeric, addr FROM cells "
            "WHERE sheet=? AND row_label=? AND col_key=? LIMIT 1",
            (sheet, row_label, col_key))
        return self._row_to_view(cur.fetchone())

    def _subtotal_get(self, region_key: str, col_key: str) -> CellView | None:
        cur = self._conn.execute(
            "SELECT value, numeric, addr FROM cells "
            "WHERE sheet='发电量' AND is_subtotal=1 AND 区域=? AND col_key=? LIMIT 1",
            (region_key, col_key))
        return self._row_to_view(cur.fetchone())

    def _resolve_fin_path(self, metric: str):
        """财务/装机:返回 (sheet, row_label) 或 None。"""
        loc = S.metric_info(metric).get("locator", {})
        sheet = loc.get("sheet")
        if sheet not in ("财务数据", "装机"):
            return None
        label = self._match_label(sheet, loc.get("row", ""))
        return (sheet, label) if label else None

    # ---- Backend 接口 ----
    def lookup(self, metric: str, entity: str, col_key: str):
        loc = S.metric_info(metric).get("locator", {})
        sheet = loc.get("sheet")
        if sheet in ("财务数据", "装机"):
            path = self._resolve_fin_path(metric)
            cv = self._row_get(path[0], path[1], col_key) if path else None
        elif sheet == "发电量":
            region_key = _subtotal_region_key(loc.get("row", ""))
            cv = self._subtotal_get(region_key, col_key)
        else:
            return None
        if not cv or not cv.numeric:
            return None
        return cv, f"{metric}·{entity}·{col_key}"

    def cumulative_cells(self, metric: str, time_tokens: list):
        loc = S.metric_info(metric).get("locator", {})
        sheet = loc.get("sheet")
        if sheet in ("财务数据", "装机"):
            path = self._resolve_fin_path(metric)
            getter = (lambda ck: self._row_get(path[0], path[1], ck)) if path else (lambda ck: None)
        elif sheet == "发电量":
            region_key = _subtotal_region_key(loc.get("row", ""))
            getter = lambda ck: self._subtotal_get(region_key, ck)      # noqa: E731
        else:
            getter = lambda ck: None                                    # noqa: E731
        out = []
        for tk in time_tokens:
            ck = _colkey(tk)
            cv = getter(ck)
            ytd = "(YTD当月累计)" if tk[0] == "ytd_month" else ""
            label = f"{metric}·{ck}{ytd}"
            out.append((cv, label) if cv else (None, label))
        return out

    def taxonomy_cells(self, metric: str, col_key: str):
        subs = S.expand_taxonomy(metric)
        out = []
        for sub in subs:
            cur = self._conn.execute(
                "SELECT row_label, value, numeric, addr FROM cells "
                "WHERE sheet='发电量' AND 方式=? AND is_subtotal=0 AND col_key=?",
                (sub, col_key))
            for r in cur.fetchall():
                cv = self._row_to_view(r)
                if cv:
                    out.append((cv, f"{r['row_label']}({sub})"))
        return out

    def cagr_cells(self, base_metric: str, init_year: int, end_year: int):
        path = self._resolve_fin_path(base_metric)
        if not path:
            return None
        init_cv = self._row_get(path[0], path[1], f"{init_year}年")
        end_cv = self._row_get(path[0], path[1], f"{end_year}年")
        if not init_cv or not end_cv:
            return None
        return init_cv, end_cv

    def year_cells(self, metric: str, entity: str) -> list[tuple[int, CellView]]:
        """某指标行【所有年份】的 (year, CellView)(peak_year 用,SQL 版)。

        与 MemoryBackend.year_cells 语义一致:fin/cap 按 row_label;发电量按区域小计;
        col_key LIKE '%年';只返回数值。供 C5 与 memory 端交叉校验。
        """
        loc = S.metric_info(metric).get("locator", {})
        sheet = loc.get("sheet")
        rows: list = []
        if sheet in ("财务数据", "装机"):
            path = self._resolve_fin_path(metric)
            if path:
                cur = self._conn.execute(
                    "SELECT col_key, value, numeric, addr FROM cells "
                    "WHERE sheet=? AND row_label=? AND col_key LIKE '%年'",
                    (path[0], path[1]))
                rows = cur.fetchall()
        elif sheet == "发电量":
            region_key = _subtotal_region_key(loc.get("row", ""))
            cur = self._conn.execute(
                "SELECT col_key, value, numeric, addr FROM cells "
                "WHERE sheet='发电量' AND is_subtotal=1 AND 区域=? AND col_key LIKE '%年'",
                (region_key,))
            rows = cur.fetchall()
        out: list[tuple[int, CellView]] = []
        for r in rows:
            cv = self._row_to_view(r)
            if not cv or not cv.numeric:
                continue
            ck = r["col_key"]
            try:
                y = int(ck[:-1])
            except (ValueError, TypeError):
                continue
            out.append((y, cv))
        return out

    def cell_by_addr(self, addr: str) -> CellView | None:
        """C2 单元格回查:SQL 按 addr 精确取。"""
        cur = self._conn.execute(
            "SELECT value, numeric, addr FROM cells WHERE addr=? LIMIT 1", (addr,))
        return self._row_to_view(cur.fetchone())


# ============================================================ 工厂
def make_backend(grid, backend: str = "memory", db_path: str | None = None):
    """构造取数后端。sqlite/both 模式自动确保 db 存在。

    backend:
      "memory" -> MemoryBackend(grid)
      "sqlite" -> SqliteBackend(ensure_db())
      "both"   -> 抛 ValueError(both 在 qa 层分两次构造)
    """
    if backend == "memory":
        return MemoryBackend(grid)
    if backend == "sqlite":
        return SqliteBackend(ensure_db(db_path))
    raise ValueError(f"未知 backend: {backend}(both 在 qa 层处理)")

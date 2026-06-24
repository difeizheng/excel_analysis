"""通用 Excel 加载器: 把任意 schema 化的 Excel 解析为 Grid。

设计原则:
- _num / _colkey / _cell 三个 helper 是契约锚点,与 preprocess.py 现状 bit-identical。
- load_table 走三种 target 模式: row_map / gen_detail / gen_subtotals。
- load_grid 读取 WorkbookSpec + 默认 schema,按 Sheet 名派发到 fin/cap,append/写入 gen_* 字段。
- _pick_engine 按扩展名自动选 xlrd/openpyxl。

不做的事:
- 不做层级继承/小计检测(语义层处理)
- 不调用 LLM(查询时路径永不碰 schema_proposer)
- 不做语义层业务规则应用(那些在 pipeline.py)
"""
from __future__ import annotations
import os
import re
import sys
import pandas as pd
from dataclasses import dataclass
from typing import Any

# 让 schema_spec 可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema_spec as SS
from excel_addr import address


# ============================================================ 内部数据类
@dataclass
class Cell:
    """与 preprocess.Cell 兼容,位置构造顺序保持不变。"""
    value: Any
    addr: str
    numeric: bool
    row_idx: int
    col_idx: int


@dataclass
class Grid:
    """通用 schema 驱动的解析产物。

    源真相容器(按逻辑表名 TableSpec.name 索引,sheet 名不再参与路由):
    - row_maps:    {table_name: {label: {colkey: Cell}}}      target=row_map
    - subtotals:   {table_name: {emit_key: {colkey: Cell}}}   target=gen_subtotals
    - details:     {table_name: [project_dict]}               target=gen_detail
    - table_index: {table_name: {sheet, target}}              locator 解析 + 别名投影
    - sheet_dispatch: {sheet: [table_name]}                   可观测性(装载副产物,不参与路由)

    fin/cap/gen_projects/gen_subtotals 为向后兼容 @property 别名,
    按 (sheet, target) 投射首个匹配表 —— 三峡遗留消费方(semantic_proposer /
    preprocess / 页面 / 脚本 / 测试)可直接访问,无需感知泛型容器。
    """
    row_maps: dict = None
    subtotals: dict = None
    details: dict = None
    table_index: dict = None
    sheet_dispatch: dict = None

    def __post_init__(self):
        if self.row_maps is None: self.row_maps = {}
        if self.subtotals is None: self.subtotals = {}
        if self.details is None: self.details = {}
        if self.table_index is None: self.table_index = {}
        if self.sheet_dispatch is None: self.sheet_dispatch = {}

    # ============================================================ 向后兼容别名
    def _first(self, sheet: str, target: str):
        """(sheet, target) → 首个匹配表的真实容器(三峡遗留 fin/cap/gen_* 投射用)。"""
        for name, meta in self.table_index.items():
            if meta.get("sheet") == sheet and meta.get("target") == target:
                if target == "row_map": return self.row_maps.get(name, {})
                if target == "gen_subtotals": return self.subtotals.get(name, {})
                if target == "gen_detail": return self.details.get(name, [])
        return [] if target == "gen_detail" else {}

    @property
    def fin(self): return self._first("财务数据", "row_map")
    @property
    def cap(self): return self._first("装机", "row_map")
    @property
    def gen_subtotals(self): return self._first("发电量", "gen_subtotals")
    @property
    def gen_projects(self): return self._first("发电量", "gen_detail")

    # ============================================================ 派发接缝(table 键驱动)
    def resolve_locator(self, loc: dict) -> dict | None:
        """语义层 locator → 行字典 {colkey: Cell} 或 None。table 键驱动 + sheet 回退。

        解析顺序:
        1. loc["table"] 命中真实表名 → 直接取该表容器(任意陌生 Excel 的主路径)
        2. 否则回退到 sheet 上匹配的表(三峡遗留 locator 只给 sheet+row):
           先查 row_map 表(_match_row_struct 精确→前缀),再查 subtotal 表(emit_key)
        row 缺省(纯 taxonomy 指标)返回 None。
        """
        if not loc:
            return None
        name = loc.get("table")
        if name and name in self.table_index:
            return self._resolve_in_table(name, loc)
        sheet = loc.get("sheet")
        row = loc.get("row", "")
        if not sheet:
            return None
        for tname, meta in self.table_index.items():
            if meta.get("sheet") == sheet and meta.get("target") == "row_map":
                r = _match_row_struct(self.row_maps.get(tname, {}), row)
                if r is not None:
                    return r
        if row:
            for tname, meta in self.table_index.items():
                if meta.get("sheet") == sheet and meta.get("target") == "gen_subtotals":
                    r = self.subtotals.get(tname, {}).get(_subtotal_region_key(row))
                    if r is not None:
                        return r
        return None

    def _resolve_in_table(self, name: str, loc: dict) -> dict | None:
        """loc["table"] 直接命中时的取数(detail 表不经 locator,走 taxonomy)。"""
        meta = self.table_index[name]
        row = loc.get("row", "")
        target = meta.get("target")
        if target == "row_map":
            return _match_row_struct(self.row_maps.get(name, {}), row)
        if target == "gen_subtotals":
            if not row:
                return None
            return self.subtotals.get(name, {}).get(_subtotal_region_key(row))
        return None

    def iter_row_maps(self):
        """遍历所有 row_map 表,产出 (sheet名, 行标签, 行字典)。顺序遵循 table_index。"""
        for name, meta in self.table_index.items():
            if meta.get("target") != "row_map":
                continue
            sheet = meta.get("sheet")
            for label, cells in self.row_maps.get(name, {}).items():
                yield sheet, label, cells

    def iter_subtotals(self):
        """遍历所有 gen_subtotals 表,产出 (sheet名, emit_key, 行字典)。"""
        for name, meta in self.table_index.items():
            if meta.get("target") != "gen_subtotals":
                continue
            sheet = meta.get("sheet")
            for emit_key, cells in self.subtotals.get(name, {}).items():
                yield sheet, emit_key, cells

    def iter_details(self):
        """遍历所有 gen_detail 表,产出 (sheet名, project_dict)。"""
        for name, meta in self.table_index.items():
            if meta.get("target") != "gen_detail":
                continue
            sheet = meta.get("sheet")
            for proj in self.details.get(name, []):
                yield sheet, proj


# 行匹配 + subtotal 键归一(与 backend._match_row_struct / _subtotal_region_key 同源)。
# 放 loader 顶层供 Grid.resolve_locator 用,避免 loader→backend 反向依赖。
def _match_row_struct(struct: dict, row_label: str):
    """精确 → "（"前缀 startswith。返回行字典或 None。"""
    if not row_label:
        return None
    if row_label in struct:
        return struct[row_label]
    prefix = row_label.split("（")[0]
    for k, v in struct.items():
        if k == row_label or k.startswith(prefix):
            return v
    return None


_SUBTOTAL_KEY_MAP = {"发电量合计": "合计"}


def _subtotal_region_key(loc_row: str) -> str:
    return _SUBTOTAL_KEY_MAP.get(loc_row, loc_row)


# 内部贡献(loader 内部流转,Grid 字段直接收)
Contribution = tuple  # ("row_map", label, values) | ("gen_detail", proj_dict) | ("gen_subtotals", emit_key, values)


# ============================================================ Helper(契约锚点)
def _num(v) -> float | None:
    try:
        f = float(v)
        return f if f == f else None  # drop nan
    except (TypeError, ValueError):
        return None


# 6 位 YYYYMM 裸数字(Excel 常把"202601"这类月份表头存成数字,pandas 读成 float 带 .0 残留)。
# 仅匹配整串 6 位数字(+ 可选 .0+ float 残留);年月都合法才归一化,避免误吞邮编/ID 等纯数字表头。
_YM_RE = re.compile(r"^(\d{4})(\d{2})(?:\.0+)?$")


def _colkey(h) -> str | None:
    """归一化表头:datetime → 'YYYY-MM';6 位 YYYYMM 裸数字(含 .0)→ 'YYYY-MM';
    其余字符串原样保留;None/空/nan → None。"""
    if h is None:
        return None
    if hasattr(h, "strftime"):
        return h.strftime("%Y-%m")
    s = str(h).strip()
    if not s or s.lower() == "nan":
        return None
    ym = _YM_RE.match(s)
    if ym:
        y, m = int(ym.group(1)), int(ym.group(2))
        if 1900 <= y <= 2100 and 1 <= m <= 12:
            return f"{y:04d}-{m:02d}"
    return s


def _cell(sheet: str, raw, r: int, c: int) -> Cell | None:
    """构造 Cell。None 表示该格应被跳过(与当前 preprocess.py 完全一致)。"""
    f = _num(raw)
    if f is None and (raw is None or str(raw).strip() in ("", "nan")):
        return None
    val = f if f is not None else str(raw).strip()
    return Cell(val, address(sheet, c, r), f is not None, r, c)


def _text_at(df, r: int, c: int) -> str:
    """取单元格文本(strip + nan→'')。用于 gen_projects 的 name/方式/区域。"""
    if c is None:
        return ""
    v = df.iloc[r, c]
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def _label_at(df, r: int, c: int) -> str:
    """取行标签(strip + nan→'')。"""
    if c is None:
        return ""
    return _text_at(df, r, c)


# ============================================================ 列展开与表头解析
def _resolve_data_col_indices(spec: SS.TableSpec, df_shape1: int) -> list[int]:
    """根据 data_col_start/data_col_end/columns 算出实际 data 列 idx 列表。
    df_shape1 用于 clamp 列范围,避免越界 IndexError。
    """
    if spec.columns:
        return [c.idx for c in spec.columns if c.role == "data" and c.idx < df_shape1]
    if spec.data_col_start is None:
        return []
    end = spec.data_col_end if spec.data_col_end is not None else df_shape1
    end = min(end, df_shape1)
    return list(range(spec.data_col_start, end)) if spec.data_col_start < end else []


def _resolve_colkeys(df, spec: SS.TableSpec, data_col_indices: list[int]) -> dict[int, str]:
    """data 列 idx → 归一化列键。"""
    out: dict[int, str] = {}
    for c in data_col_indices:
        if c >= df.shape[1]:
            continue
        explicit = next((col.key for col in spec.columns if col.idx == c and col.key), None)
        if explicit is not None:
            out[c] = explicit
        else:
            out[c] = _colkey(df.iloc[spec.header_row, c])
    return out


def resolve_data_colkeys(spec: SS.TableSpec, df) -> list[str]:
    """数据范围**全部**列的 colkey 列表(按 idx 升序,过滤 None)。

    与 _resolve_data_col_indices + _resolve_colkeys 配套使用,但输出 list[str] 而非 dict:
    - 供"构造预览"在 contribs 因列全空而拿不到 colkey 时,仍展示该列(列头 + 空 cell)。
    - 单测 + 页面都消费这个列表,避免页面侧重复实现 colkey 解析逻辑。
    顺序即 idx 顺序,对应 Excel 列从左到右。None colkey(表头为空)直接跳过。
    """
    indices = _resolve_data_col_indices(spec, df.shape[1])
    colkey_map = _resolve_colkeys(df, spec, indices)
    return [colkey_map[c] for c in indices if colkey_map.get(c)]


# ============================================================ 行准入逻辑
def _admit_row_map(label: str, spec: SS.TableSpec) -> bool:
    """row_map 目标:行标签级别的准入判定。"""
    if not label or label.lower() == "nan":
        return False
    if label in spec.skip_labels:
        return False
    if spec.skip_label_regex and re.search(spec.skip_label_regex, label):
        return False
    return True


def _classify_subtotal(label: str, spec: SS.TableSpec) -> str | None:
    """gen_subtotals 目标:first-wins 分类小计行,返回 emit_key 或 None。"""
    for rule in spec.subtotal_rules:
        if rule.match_substring in label:
            return rule.emit_key
    return None


def _handle_duplicate(seen: dict, label: str, spec: SS.TableSpec, sheet: str, r: int) -> None:
    """重复标签策略:warn=记录并仍覆盖;error=抛错;allow=静默。"""
    if label not in seen:
        seen[label] = r
        return
    if spec.duplicate_label_policy == "allow":
        return
    if spec.duplicate_label_policy == "error":
        raise ValueError(
            f"duplicate label {label!r} in sheet {sheet!r} at row {r} "
            f"(first at row {seen[label]})"
        )
    # warn
    print(
        f"[loader] WARNING: duplicate label {label!r} in sheet {sheet!r} "
        f"at row {r} (first at row {seen[label]}); second occurrence overwrites",
        file=sys.stderr,
    )


# ============================================================ 通用 load_table
def load_table(df, sheet_name: str, spec: SS.TableSpec) -> list[Contribution]:
    """单表加载:根据 spec.target 走三种模式之一,返回 Contribution 列表。"""
    data_col_indices = _resolve_data_col_indices(spec, df.shape[1])
    if not data_col_indices:
        return []
    colkeys = _resolve_colkeys(df, spec, data_col_indices)
    last_r = (spec.last_data_row if spec.last_data_row is not None else df.shape[0] - 1)
    first_r = max(spec.first_data_row, 0)
    seen_labels: dict[str, int] = {}  # row_map 重复检测

    contributions: list[Contribution] = []
    for r in range(first_r, last_r + 1):
        label = _label_at(df, r, spec.label_col_idx) if spec.label_col_idx is not None else ""
        marker = _text_at(df, r, spec.detail_marker_col_idx) if spec.detail_marker_col_idx is not None else ""

        # ---- 行类型判别 ----
        if spec.target == "row_map":
            if not _admit_row_map(label, spec):
                continue
        elif spec.target == "gen_detail":
            if not marker:                # col 4 空 → 不是明细行
                continue
        elif spec.target == "gen_subtotals":
            if marker:                   # col 4 非空 → 是明细行,跳过
                continue
            emit_key = _classify_subtotal(label, spec)
            if emit_key is None:
                continue

        # ---- 构造 values 字典(三种 target 共享这段)----
        values: dict[str, Cell] = {}
        for c in data_col_indices:
            ck = colkeys.get(c)
            if not ck:
                continue
            cell = _cell(sheet_name, df.iloc[r, c], r, c)
            if cell is not None:
                values[ck] = cell
        if not values:
            continue

        # ---- 派发贡献 ----
        if spec.target == "row_map":
            _handle_duplicate(seen_labels, label, spec, sheet_name, r)
            contributions.append(("row_map", label, values))
        elif spec.target == "gen_detail":
            cls = spec.detail_classifier_cols or {}
            # 分类维度字段动态来自 schema(三峡是 方式/区域;任意 Excel 是任意键),
            # 不再写死"方式"/"区域"——泛型化前提。
            proj = {"name": _text_at(df, r, cls.get("name")), "values": values}
            for dim, col_idx in cls.items():
                if dim == "name":
                    continue
                proj[dim] = _text_at(df, r, col_idx)
            contributions.append(("gen_detail", proj))
        elif spec.target == "gen_subtotals":
            contributions.append(("gen_subtotals", emit_key, values))

    return contributions


# ============================================================ Engine 选择
def _pick_engine(path: str, mode: str = "auto") -> str:
    if mode != "auto":
        return mode
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xls":
        return "xlrd"
    if ext == ".xlsx":
        return "openpyxl"
    raise ValueError(f"unsupported extension {ext!r} for {path} (expected .xls or .xlsx)")


# ============================================================ 路径解析
def _resolve_workbook_path(spec_path: str, spec: SS.WorkbookSpec) -> str:
    """WorkbookSpec.path 相对 schema YAML 目录解析;绝对路径直接用。"""
    p = spec.path
    if os.path.isabs(p):
        return p
    base = os.path.dirname(os.path.abspath(spec_path))
    return os.path.abspath(os.path.join(base, p))


# ============================================================ 主入口
DEFAULT_SCHEMA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "schemas",
    "三峡国际经营数据库.yaml",
)


def load_grid(
    spec_path: str | None = None,
    excel_path: str | None = None,
) -> Grid:
    """端到端:加载 schema → 读 Excel → 解析 → 装入 Grid。

    excel_path 显式指定 Excel 文件路径(优先于 schema 内的 path 字段),
    供工作台上传的临时文件使用;不传则按 schema 的 path 字段解析。
    """
    spec_path = spec_path or DEFAULT_SCHEMA
    spec = SS._load_spec(spec_path)
    if excel_path:
        wb_path = excel_path
    else:
        wb_path = _resolve_workbook_path(spec_path, spec)
    engine = _pick_engine(wb_path, spec.engine)

    g = Grid()

    # 派发记录闭包:把 sheet → [table_name] 记进 sheet_dispatch(仅可观测性,不参与路由)。
    def _record(sheet: str, table_name: str) -> None:
        bucket = g.sheet_dispatch.setdefault(sheet, [])
        if table_name not in bucket:
            bucket.append(table_name)

    # 按 schema 遍历,每个 table 以自己的 TableSpec.name 为桶键落入对应通用容器。
    # sheet 名只作为数据源地址(读 Excel 用),不再决定落到哪个 Grid 桶 ——
    # 这正是"sheet 清单不与 Grid 派发绑定"的落点。
    for sh in spec.sheets:
        df = pd.read_excel(wb_path, sheet_name=sh.name, engine=engine, header=None)
        for tbl in sh.tables:
            if not tbl.enabled:
                continue
            # 选桶(target 决定容器形状),按逻辑表名开桶
            if tbl.target == "row_map":
                bucket = g.row_maps.setdefault(tbl.name, {})
            elif tbl.target == "gen_subtotals":
                bucket = g.subtotals.setdefault(tbl.name, {})
            elif tbl.target == "gen_detail":
                bucket = g.details.setdefault(tbl.name, [])
            else:
                continue
            for contrib in load_table(df, sh.name, tbl):
                tag = contrib[0]
                if tag == "row_map":
                    _, label, values = contrib
                    bucket[label] = values
                elif tag == "gen_detail":
                    bucket.append(contrib[1])
                elif tag == "gen_subtotals":
                    _, key, values = contrib
                    bucket[key] = values
                _record(sh.name, tbl.name)
            # 登记 table_index:locator 解析 + fin/cap/gen_* 别名投影均依赖它
            g.table_index[tbl.name] = {"sheet": sh.name, "target": tbl.target}
    return g

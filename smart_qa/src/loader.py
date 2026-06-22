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
    fin: dict = None
    cap: dict = None
    gen_projects: list = None
    gen_subtotals: dict = None

    def __post_init__(self):
        if self.fin is None: self.fin = {}
        if self.cap is None: self.cap = {}
        if self.gen_projects is None: self.gen_projects = []
        if self.gen_subtotals is None: self.gen_subtotals = {}


# 内部贡献(loader 内部流转,Grid 字段直接收)
Contribution = tuple  # ("row_map", label, values) | ("gen_detail", proj_dict) | ("gen_subtotals", emit_key, values)


# ============================================================ Helper(契约锚点)
def _num(v) -> float | None:
    try:
        f = float(v)
        return f if f == f else None  # drop nan
    except (TypeError, ValueError):
        return None


def _colkey(h) -> str | None:
    """归一化表头: datetime → 'YYYY-MM'; 字符串原样保留; None/空/nan → None。"""
    if h is None:
        return None
    if hasattr(h, "strftime"):
        return h.strftime("%Y-%m")
    s = str(h).strip()
    return s if s and s.lower() != "nan" else None


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
            cls = spec.detail_classifier_cols
            proj = {
                "name": _text_at(df, r, cls.get("name")),
                "方式": _text_at(df, r, cls.get("方式")),
                "区域": _text_at(df, r, cls.get("区域")),
                "values": values,
            }
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
    for sh in spec.sheets:
        df = pd.read_excel(wb_path, sheet_name=sh.name, engine=engine, header=None)
        for tbl in sh.tables:
            if not tbl.enabled:
                continue
            for contrib in load_table(df, sh.name, tbl):
                tag = contrib[0]
                if tag == "row_map":
                    _, label, values = contrib
                    # Sheet 名派发到 fin/cap(locator 派发键一致)
                    if sh.name == "财务数据":
                        g.fin[label] = values
                    elif sh.name == "装机":
                        g.cap[label] = values
                    # 其他 row_map 表忽略
                elif tag == "gen_detail":
                    g.gen_projects.append(contrib[1])
                elif tag == "gen_subtotals":
                    _, key, values = contrib
                    g.gen_subtotals[key] = values
    return g

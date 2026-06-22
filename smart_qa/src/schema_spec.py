"""SchemaSpec: Excel schema 的 frozen 数据类层级 + YAML 加载。

层级:
    WorkbookSpec
      └── SheetSpec[]
            └── TableSpec[]
                  ├── ColumnSpec[]
                  ├── SubtotalRule[]      (gen_subtotals 用)
                  └── detail_classifier_cols (gen_detail 用)

设计原则:
- 字段名与 YAML 键 1:1,`from_dict` 工厂保证两者对齐。
- 所有 dataclass frozen,字段顺序即 YAML 顺序。
- 加载函数复用 semantic_layer._load() 的模式(os.path + yaml.safe_load)。
"""
from __future__ import annotations
import yaml
from dataclasses import dataclass, field


# ---------------------------------------------------------------- 数据类
@dataclass(frozen=True)
class ColumnSpec:
    idx: int                                       # 0-based 列索引
    role: str                                      # "label"|"data"|"classifier"|"skip"
    key: str | None = None                         # data 列专用: 派生自 header 的列键("2018年","2026-01"); None=自动派生
    classifier_dim: str | None = None              # classifier 列专用: 维度名("方式"|"区域"|"name")


@dataclass(frozen=True)
class SubtotalRule:
    match_substring: str                           # 行标签包含此子串则匹配
    emit_key: str                                  # 匹配后写入 gen_subtotals 的键


@dataclass(frozen=True)
class TableSpec:
    name: str                                      # 逻辑表名(用于日志/调试)
    header_row: int                                # 0-based 表头行
    first_data_row: int                            # 0-based 首个数据行(含)
    last_data_row: int | None = None               # None = 到 DataFrame 末尾
    label_col_idx: int = 0                         # 行标签列 0-based
    target: str = "row_map"                        # "row_map"|"gen_detail"|"gen_subtotals"
    columns: list[ColumnSpec] = field(default_factory=list)   # 显式列定义(可选)
    # ---- 便利字段: 若 columns 为空,loader 用这些自动展开 ----
    data_col_start: int | None = None              # data 列起始 idx
    data_col_end: int | None = None                # data 列终止 idx(EXCLUSIVE); None = 到 DataFrame 末尾
    skip_cols: tuple[int, ...] = ()                # 显式 skip 的列 idx(在 data 范围外/内均可)
    # ---- 业务规则 ----
    skip_labels: list[str] = field(default_factory=list)             # row_map 专用: 精确匹配的跳过标签
    skip_label_regex: str | None = None                            # row_map 专用: 跳过标签正则
    duplicate_label_policy: str = "warn"                            # "warn"|"error"|"allow"
    # ---- gen_* 专用 ----
    detail_marker_col_idx: int | None = None                       # 行类型判别列
    subtotal_rules: list[SubtotalRule] = field(default_factory=list)  # gen_subtotals 专用
    detail_classifier_cols: dict[str, int] = field(default_factory=dict)  # gen_detail: 字段名 -> 列 idx
    enabled: bool = True                           # False = 跳过该表(占位用,见月度表)


@dataclass(frozen=True)
class SheetSpec:
    name: str                                      # Excel Sheet 名 = locator 派发键("财务数据"等)
    engine_hint: str | None = None                 # "xlrd"|"openpyxl"|None(沿用 workbook.engine)
    tables: list[TableSpec] = field(default_factory=list)


@dataclass(frozen=True)
class WorkbookSpec:
    path: str                                      # 相对项目根或绝对路径
    engine: str = "auto"                           # "auto"|"xlrd"|"openpyxl"
    version: str = "1"                             # schema 版本
    sheets: list[SheetSpec] = field(default_factory=list)


# ---------------------------------------------------------------- from_dict 工厂
def column_from_dict(d: dict) -> ColumnSpec:
    return ColumnSpec(
        idx=int(d["idx"]),
        role=d["role"],
        key=d.get("key"),
        classifier_dim=d.get("classifier_dim"),
    )


def subtotal_rule_from_dict(d: dict) -> SubtotalRule:
    return SubtotalRule(
        match_substring=str(d["match_substring"]),
        emit_key=str(d["emit_key"]),
    )


def table_from_dict(d: dict) -> TableSpec:
    skip_cols_raw = d.get("skip_cols", [])
    return TableSpec(
        name=str(d.get("name", "")),
        header_row=int(d["header_row"]),
        first_data_row=int(d["first_data_row"]),
        last_data_row=(int(d["last_data_row"]) if d.get("last_data_row") is not None else None),
        label_col_idx=int(d.get("label_col_idx", 0)),
        target=str(d.get("target", "row_map")),
        columns=[column_from_dict(c) for c in d.get("columns", [])],
        data_col_start=(int(d["data_col_start"]) if d.get("data_col_start") is not None else None),
        data_col_end=(int(d["data_col_end"]) if d.get("data_col_end") is not None else None),
        skip_cols=tuple(int(c) for c in skip_cols_raw),
        skip_labels=list(d.get("skip_labels", [])),
        skip_label_regex=d.get("skip_label_regex"),
        duplicate_label_policy=str(d.get("duplicate_label_policy", "warn")),
        detail_marker_col_idx=(int(d["detail_marker_col_idx"]) if d.get("detail_marker_col_idx") is not None else None),
        subtotal_rules=[subtotal_rule_from_dict(r) for r in d.get("subtotal_rules", [])],
        detail_classifier_cols={k: int(v) for k, v in d.get("detail_classifier_cols", {}).items()},
        enabled=bool(d.get("enabled", True)),
    )


def sheet_from_dict(d: dict) -> SheetSpec:
    return SheetSpec(
        name=str(d["name"]),
        engine_hint=d.get("engine_hint"),
        tables=[table_from_dict(t) for t in d.get("tables", [])],
    )


def workbook_from_dict(d: dict) -> WorkbookSpec:
    return WorkbookSpec(
        path=str(d["path"]),
        engine=str(d.get("engine", "auto")),
        version=str(d.get("version", "1")),
        sheets=[sheet_from_dict(s) for s in d.get("sheets", [])],
    )


# ---------------------------------------------------------------- YAML 加载
def _load_spec(path: str) -> WorkbookSpec:
    """加载 schema YAML → WorkbookSpec。"""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"schema YAML 顶层必须是 dict,实际={type(data).__name__}")
    return workbook_from_dict(data)

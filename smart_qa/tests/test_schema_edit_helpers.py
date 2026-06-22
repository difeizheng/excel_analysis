"""schema_edit_helpers 单测:纯函数,AAA 结构。

覆盖:col_letter 边界、行/列选项标签、表格补丁(写入/UNSET删除/深拷贝隔离/保留高级字段)、
YAML 序列化往返。
"""
from __future__ import annotations
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))

import schema_edit_helpers as SE  # noqa: E402


# ---------------------------------------------------------------- col_letter
def test_col_letter_boundaries():
    # Arrange & Act & Assert
    assert SE.col_letter(0) == "A"
    assert SE.col_letter(25) == "Z"
    assert SE.col_letter(26) == "AA"
    assert SE.col_letter(27) == "AB"
    assert SE.col_letter(51) == "AZ"
    assert SE.col_letter(-1) == "?"


# ---------------------------------------------------------------- row_option_label
def test_row_option_label_shows_content_preview():
    # Arrange
    grid = [
        ["", "项目", "2018年", "2019年"],
        ["", "利润总额", "100", "120"],
    ]
    # Act
    label = SE.row_option_label(1, grid)
    # Assert
    assert label.startswith("r01 · 第2行 · ")
    assert "利润总额" in label
    assert "100" in label  # 前 max_cells 非空单元格


def test_row_option_label_empty_row_has_no_preview():
    # Arrange
    grid = [["", "", ""], ["", "", ""]]
    # Act
    label = SE.row_option_label(0, grid)
    # Assert
    assert label == "r00 · 第1行"


def test_row_option_label_truncates_long_cell():
    # Arrange
    long = "这是一个非常非常非常非常长的单元格内容"
    grid = [[long]]
    # Act
    label = SE.row_option_label(0, grid)
    # Assert
    assert "…" in label  # 超过 _short limit(10)被截断


# ---------------------------------------------------------------- col_option_label
def test_col_option_label_uses_header_cell():
    # Arrange
    grid = [
        ["序号", "项目", "2018年"],   # row 0 = 表头
        ["1", "利润总额", "100"],
    ]
    # Act
    label = SE.col_option_label(1, grid, label_row=0)
    # Assert
    assert label == "c01 · B列 · 项目"


def test_col_option_label_empty_header_shows_only_ruler():
    # Arrange
    grid = [["序号", "", "2018年"]]
    # Act
    label = SE.col_option_label(1, grid, label_row=0)
    # Assert
    assert label == "c01 · B列"  # 空表头单元格 → 不加预览


# ---------------------------------------------------------------- with_table_edited
def _sample_workbook():
    """两个 sheet、含高级字段的最小样例。"""
    return {
        "path": "x.xls",
        "engine": "xlrd",
        "version": "1",
        "sheets": [
            {"name": "财务数据", "tables": [
                {"name": "财务主表", "target": "row_map", "header_row": 2,
                 "first_data_row": 3, "label_col_idx": 1, "data_col_start": 2,
                 "skip_labels": ["备注"]},
            ]},
            {"name": "发电量", "tables": [
                {"name": "年度小计", "target": "gen_subtotals", "header_row": 3,
                 "first_data_row": 4,
                 "subtotal_rules": [
                     {"match_substring": "合计", "emit_key": "合计"}]},
            ]},
        ],
    }


def test_with_table_edited_writes_core_fields():
    # Arrange
    raw = _sample_workbook()
    values = {"target": "row_map", "header_row": 5, "first_data_row": 6,
              "label_col_idx": 2, "data_col_start": 3, "data_col_end": 9,
              "last_data_row": 20, "detail_marker_col_idx": SE.UNSET}

    # Act
    out = SE.with_table_edited(raw, sheet_idx=0, table_idx=0, values=values)

    # Assert
    t = out["sheets"][0]["tables"][0]
    assert t["header_row"] == 5
    assert t["first_data_row"] == 6
    assert t["label_col_idx"] == 2
    assert t["data_col_start"] == 3
    assert t["data_col_end"] == 9
    assert t["last_data_row"] == 20
    assert "detail_marker_col_idx" not in t  # UNSET → 删除
    assert t["target"] == "row_map"


def test_with_table_edited_unset_removes_optional_keys():
    # Arrange: 目标表原本有 data_col_end
    raw = _sample_workbook()
    raw["sheets"][0]["tables"][0]["data_col_end"] = 9
    values = {"data_col_end": SE.UNSET, "last_data_row": SE.UNSET,
              "data_col_start": SE.UNSET}

    # Act
    out = SE.with_table_edited(raw, 0, 0, values)

    # Assert
    t = out["sheets"][0]["tables"][0]
    assert "data_col_end" not in t
    assert "last_data_row" not in t
    assert "data_col_start" not in t


def test_with_table_edited_does_not_mutate_input():
    # Arrange
    raw = _sample_workbook()
    original_header = raw["sheets"][0]["tables"][0]["header_row"]
    values = {"header_row": 99}

    # Act
    SE.with_table_edited(raw, 0, 0, values)

    # Assert: 入参未被改动(不可变契约)
    assert raw["sheets"][0]["tables"][0]["header_row"] == original_header
    assert original_header != 99


def test_with_table_edited_preserves_advanced_fields_and_siblings():
    # Arrange
    raw = _sample_workbook()
    values = {"header_row": 7}

    # Act: 改 sheet1 的 table0
    out = SE.with_table_edited(raw, 0, 0, values)

    # Assert: sheet0 高级字段保留
    assert out["sheets"][0]["tables"][0]["skip_labels"] == ["备注"]
    # sheet1(兄弟)完全不动
    assert out["sheets"][1]["tables"][0]["subtotal_rules"] == [
        {"match_substring": "合计", "emit_key": "合计"}]
    assert out["sheets"][1]["tables"][0]["header_row"] == 3


def test_with_table_edited_out_of_range_returns_copy_unchanged():
    # Arrange
    raw = _sample_workbook()

    # Act
    out = SE.with_table_edited(raw, sheet_idx=99, table_idx=0, values={"header_row": 7})

    # Assert: 越界不抛、不改(返回的拷贝与原值一致)
    assert out["sheets"][0]["tables"][0]["header_row"] == 2


# ---------------------------------------------------------------- dump_workbook_yaml
def test_dump_workbook_yaml_preserves_chinese_and_order():
    # Arrange
    raw = {"path": "数据.xls", "version": "1", "sheets": [
        {"name": "财务数据", "tables": []}]}

    # Act
    text = SE.dump_workbook_yaml(raw)

    # Assert: 中文不被转义、键顺序保持
    assert "财务数据" in text  # allow_unicode
    assert "数据.xls" in text
    assert text.index("path:") < text.index("version:") < text.index("sheets:")


def test_dump_workbook_yaml_roundtrip():
    # Arrange
    raw = {"path": "x.xls", "sheets": [
        {"name": "S", "tables": [
            {"target": "row_map", "header_row": 0, "first_data_row": 1}]}]}

    # Act
    import yaml
    text = SE.dump_workbook_yaml(raw)
    back = yaml.safe_load(text)

    # Assert
    assert back == raw


def test_dump_workbook_yaml_non_dict_returns_empty():
    assert SE.dump_workbook_yaml(None) == ""  # type: ignore[arg-type]
    assert SE.dump_workbook_yaml([]) == ""    # type: ignore[arg-type]


# ---------------------------------------------------------------- col_index
def test_col_index_letters_and_digits():
    assert SE.col_index("A") == 0
    assert SE.col_index("B") == 1
    assert SE.col_index("Z") == 25
    assert SE.col_index("AA") == 26
    assert SE.col_index("ab") == 27          # 大小写不敏感
    assert SE.col_index("3") == 3            # 纯数字视为 idx
    assert SE.col_index("") is None
    assert SE.col_index("A1") is None        # 混合无效
    assert SE.col_index("?") is None


# ---------------------------------------------------------------- parse/format 高级字段
def test_subtotal_rules_roundtrip_preserves_order():
    # Arrange
    text = "合计 => 合计\n（一）巴西 => 巴西\n# 注释行\n\n"
    # Act
    rules = SE.parse_subtotal_rules(text)
    # Assert
    assert rules == [
        {"match_substring": "合计", "emit_key": "合计"},
        {"match_substring": "（一）巴西", "emit_key": "巴西"},
    ]
    # 顺序保持(first-wins 生效)
    assert SE.format_subtotal_rules(rules) == "合计 => 合计\n（一）巴西 => 巴西"


def test_subtotal_rules_single_side_defaults_to_same_name():
    rules = SE.parse_subtotal_rules("备注")
    assert rules == [{"match_substring": "备注", "emit_key": "备注"}]


def test_subtotal_rules_empty_returns_empty_list():
    assert SE.parse_subtotal_rules("") == []
    assert SE.parse_subtotal_rules(None) == []  # type: ignore[arg-type]


def test_classifiers_parse_accepts_letter_and_index():
    # Arrange
    text = "name => E\n方式 => C\n区域 => 3"
    # Act
    cls = SE.parse_classifiers(text, n_cols=10)
    # Assert
    assert cls == {"name": 4, "方式": 2, "区域": 3}


def test_classifiers_parse_drops_out_of_range():
    # J=9(在界), Z=25(越界), 99(越界)
    cls = SE.parse_classifiers("ok => J\nbz1 => Z\nbz2 => 99", n_cols=10)
    assert cls == {"ok": 9}  # Z(25)、99 越界丢弃


def test_classifiers_format_uses_letters():
    assert SE.format_classifiers({"name": 4, "方式": 2}) == "name => E\n方式 => C"


def test_skip_labels_roundtrip():
    assert SE.parse_skip_labels("备注\n# 注释\n小计") == ["备注", "小计"]
    assert SE.format_skip_labels(["备注", "小计"]) == "备注\n小计"
    assert SE.parse_skip_labels("") == []


# ---------------------------------------------------------------- with_table_edited 高级字段
def test_with_table_edited_writes_and_clears_advanced():
    # Arrange
    raw = {"sheets": [{"name": "S", "tables": [
        {"target": "gen_subtotals", "header_row": 0,
         "subtotal_rules": [{"match_substring": "合计", "emit_key": "合计"}],
         "detail_classifier_cols": {"name": 4}}]}]}

    # Act: 覆盖 subtotal_rules、清空 classifier
    out = SE.with_table_edited(raw, 0, 0, {
        "subtotal_rules": [{"match_substring": "总", "emit_key": "总"}],
        "detail_classifier_cols": {},
    })
    # Assert
    t = out["sheets"][0]["tables"][0]
    assert t["subtotal_rules"] == [{"match_substring": "总", "emit_key": "总"}]
    assert "detail_classifier_cols" not in t   # 空 → 删键


def test_with_table_edited_skip_label_regex_blank_removes_key():
    # Arrange
    raw = {"sheets": [{"name": "S", "tables": [
        {"target": "row_map", "header_row": 0, "skip_label_regex": ".*合计.*"}]}]}
    # Act
    out = SE.with_table_edited(raw, 0, 0, {"skip_label_regex": "   "})
    # Assert
    assert "skip_label_regex" not in out["sheets"][0]["tables"][0]


# ---------------------------------------------------------------- suggest_fields
def test_suggest_fields_basic_financial_shape():
    # Arrange:row2=表头(年份),row3+=数据,col0=序号,col1=项目(文本),col2-3=数值
    grid = [
        ["", "", "", ""],                       # r0
        ["", "", "", ""],                       # r1
        ["序号", "项目", "2018年", "2019年"],   # r2 表头
        ["1", "利润总额", "100", "120"],        # r3 数据
        ["2", "收入", "200", "240"],
    ]
    # Act
    sug = SE.suggest_fields(grid, n_rows=5, n_cols=4, target="row_map")
    # Assert
    assert sug["header_row"] == 2
    assert sug["first_data_row"] == 3
    assert sug["label_col_idx"] == 1          # "项目"列文本最多
    assert sug["data_col_start"] == 2
    assert sug["data_col_end"] == 4           # EXCLUSIVE


def test_suggest_fields_empty_grid_returns_empty():
    assert SE.suggest_fields([], 0, 0) == {}


def test_suggest_fields_picks_leftmost_period_block_in_dual_table_sheet():
    # Arrange:模拟发电量"年度表(左, r3, len3)+ 月度表(右, r4, len6 更大)"并排。
    # 应选最左(主表),不被更大的右表带偏。
    grid = [
        [""] * 15,                                                                   # r0
        [""] * 15,                                                                   # r1
        [""] * 15,                                                                   # r2
        ["", "", "", "", "", "2019年", "2020年", "2021年"] + [""] * 7,               # r3 左周期块 5-7
        ["lbl", "水电", "x", "y", "项目甲", "1", "2", "3", "", "202601", "202602",
         "202603", "202604", "202605", "202606"],                                    # r4 右周期块 9-14
        ["lbl2", "风电", "x", "y", "项目乙", "4", "5", "6", "", "", "", "", "", "", ""],
    ]
    # Act
    sug = SE.suggest_fields(grid, n_rows=6, n_cols=15, target="gen_detail")
    # Assert:取最左块(r3, cols 5-7),非更大的右块(r4, cols 9-14)
    assert sug["header_row"] == 3
    assert sug["first_data_row"] == 4
    assert sug["data_col_start"] == 5
    assert sug["data_col_end"] == 8
    assert sug["detail_marker_col_idx"] == 4   # 数据段左侧文本最多列(项目甲/乙)


def test_suggest_fields_numeric_fallback_without_period_headers():
    # Arrange:无年/月表头,纯分类+数值
    grid = [
        ["", "", "", ""],
        ["名称", "A", "B", "C"],   # r1 表头(无周期)
        ["x", "1", "2", "3"],
        ["y", "4", "5", "6"],
    ]
    # Act
    sug = SE.suggest_fields(grid, n_rows=4, n_cols=4, target="row_map")
    # Assert:回退到"非空最多行"作表头 + 最长数值列段
    assert sug["header_row"] == 1
    assert sug["first_data_row"] == 2
    assert sug["label_col_idx"] == 0
    assert sug["data_col_start"] == 1
    assert sug["data_col_end"] == 4


# ---------------------------------------------------------------- preflight_table
def test_preflight_data_row_at_or_above_header():
    # Arrange: 数据行 2 <= 表头 2
    vals = {"header_row": 2, "first_data_row": 2}
    # Act
    msgs, hl = SE.preflight_table(vals, n_cols=10)
    # Assert
    assert msgs and "应在表头" in msgs[0]
    assert ("row", 2) in hl


def test_preflight_data_col_start_ge_end():
    # Arrange: 起 5 >= 终 5(EXCLUSIVE,即空范围)
    vals = {"data_col_start": 5, "data_col_end": 5}
    # Act
    msgs, hl = SE.preflight_table(vals, n_cols=10)
    # Assert
    assert msgs and "早于" in msgs[0]
    assert ("col", 5) in hl


def test_preflight_label_col_out_of_range():
    vals = {"label_col_idx": 99}
    msgs, hl = SE.preflight_table(vals, n_cols=8)
    assert msgs and "超出预览列数" in msgs[0]
    assert ("col", 99) in hl


def test_preflight_clean_values_pass():
    vals = {"header_row": 2, "first_data_row": 3, "label_col_idx": 1,
            "data_col_start": 2, "data_col_end": 9}
    msgs, hl = SE.preflight_table(vals, n_cols=10)
    assert msgs == []
    assert hl == []


def test_preflight_unset_optionals_ignored():
    # data_col_start/end 为 UNSET 不应触发"起≥止"
    vals = {"data_col_start": SE.UNSET, "data_col_end": SE.UNSET,
            "header_row": 2, "first_data_row": 5}
    msgs, _ = SE.preflight_table(vals, n_cols=10)
    assert msgs == []

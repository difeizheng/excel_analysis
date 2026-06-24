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
import loader  # noqa: E402  (构造 loader.Cell 作 contributions 输入)


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
    # values["data_col_end"] = 9 是 UI INCLUSIVE(含本列) → 写 YAML EXCLUSIVE = 10
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
    assert t["data_col_end"] == 10            # INCLUSIVE 9 → YAML EXCLUSIVE 10
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
    # Arrange: UI INCLUSIVE 语义,起 5 > 终 4(空范围)报错;起 5 == 终 5 单列合法。
    vals = {"data_col_start": 5, "data_col_end": 4}
    # Act
    msgs, hl = SE.preflight_table(vals, n_cols=10)
    # Assert
    assert msgs and "早于或等于" in msgs[0]
    assert ("col", 5) in hl
    assert ("col", 4) in hl


def test_preflight_label_col_out_of_range():
    vals = {"label_col_idx": 99}
    msgs, hl = SE.preflight_table(vals, n_cols=8)
    assert msgs and "超出预览列数" in msgs[0]
    assert ("col", 99) in hl


def test_preflight_clean_values_pass():
    # UI INCLUSIVE:data_col_end=9 表示"包含第 9 列",n_cols=10 时合法
    vals = {"header_row": 2, "first_data_row": 3, "label_col_idx": 1,
            "data_col_start": 2, "data_col_end": 9}
    msgs, hl = SE.preflight_table(vals, n_cols=10)
    assert msgs == []
    assert hl == []

    # 单列范围(dcs == dce)合法(以前 EXCLUSIVE 会报起==止错)
    vals_single = {"data_col_start": 5, "data_col_end": 5}
    msgs_s, _ = SE.preflight_table(vals_single, n_cols=10)
    assert msgs_s == []


def test_preflight_unset_optionals_ignored():
    # data_col_start/end 为 UNSET 不应触发"起≥止"
    vals = {"data_col_start": SE.UNSET, "data_col_end": SE.UNSET,
            "header_row": 2, "first_data_row": 5}
    msgs, _ = SE.preflight_table(vals, n_cols=10)
    assert msgs == []


# ---------------------------------------------------------------- with_table_added
def test_with_table_added_appends_to_end():
    # Arrange
    raw = {"sheets": [
        {"name": "A", "tables": [{"name": "t1"}, {"name": "t2"}]},
        {"name": "B", "tables": [{"name": "t3"}]},
    ]}
    new_table = {"name": "new", "target": "row_map", "enabled": False}
    # Act
    out = SE.with_table_added(raw, 0, new_table)
    # Assert
    assert len(out["sheets"][0]["tables"]) == 3
    assert out["sheets"][0]["tables"][-1]["name"] == "new"
    assert out["sheets"][0]["tables"][-1]["enabled"] is False
    # 其他 sheet 不动
    assert out["sheets"][1] == raw["sheets"][1]


def test_with_table_added_is_immutable():
    """返回新 dict,原 raw 一字未动。"""
    # Arrange
    raw = {"sheets": [{"name": "A", "tables": [{"name": "t1"}]}]}
    before = {"sheets": [{"name": "A", "tables": [{"name": "t1"}], "tables_id": id(raw["sheets"][0]["tables"])}]}
    # Act
    out = SE.with_table_added(raw, 0, {"name": "new"})
    # Assert
    assert len(raw["sheets"][0]["tables"]) == 1, "原 raw 的 tables 长度不应变"
    assert out is not raw, "应返回新 dict"
    assert out["sheets"][0] is not raw["sheets"][0], "sheet dict 也应是新对象"
    assert before["sheets"][0]["tables_id"] == id(raw["sheets"][0]["tables"])


def test_with_table_added_overflow_returns_copy():
    """越界 sheet_idx 原样返回(深拷贝),不抛。"""
    # Arrange
    raw = {"sheets": [{"name": "A", "tables": [{"name": "t1"}]}]}
    # Act
    out = SE.with_table_added(raw, 99, {"name": "x"})
    # Assert
    assert out == raw
    assert out is not raw


def test_with_table_added_creates_tables_key_if_missing():
    """sheet 没 tables 键时,自动创建空 list 后 append。"""
    # Arrange
    raw = {"sheets": [{"name": "A"}]}  # 无 tables 键
    # Act
    out = SE.with_table_added(raw, 0, {"name": "t1"})
    # Assert
    assert out["sheets"][0]["tables"] == [{"name": "t1"}]


# ---------------------------------------------------------------- with_table_removed
def test_with_table_removed_pops_correct_index():
    # Arrange
    raw = {"sheets": [
        {"name": "A", "tables": [{"name": "t1"}, {"name": "t2"}, {"name": "t3"}]},
    ]}
    # Act
    out = SE.with_table_removed(raw, 0, 1)  # 删 t2
    # Assert
    assert len(out["sheets"][0]["tables"]) == 2
    assert out["sheets"][0]["tables"][0]["name"] == "t1"
    assert out["sheets"][0]["tables"][1]["name"] == "t3"


def test_with_table_removed_last_one_yields_empty_list():
    """仅剩 1 张表时 pop 后变空 list,不抛(让上层处理"请去 YAML 添加"提示)。"""
    # Arrange
    raw = {"sheets": [{"name": "A", "tables": [{"name": "only"}]}]}
    # Act
    out = SE.with_table_removed(raw, 0, 0)
    # Assert
    assert out["sheets"][0]["tables"] == []
    assert raw["sheets"][0]["tables"] == [{"name": "only"}], "原 raw 不变"


def test_with_table_removed_is_immutable():
    """返回新 dict,原 raw 一字未动。"""
    # Arrange
    raw = {"sheets": [{"name": "A", "tables": [{"name": "t1"}]}]}
    # Act
    out = SE.with_table_removed(raw, 0, 0)
    # Assert
    assert raw["sheets"][0]["tables"] == [{"name": "t1"}]
    assert out is not raw
    assert out["sheets"][0] is not raw["sheets"][0]


def test_with_table_removed_overflow_returns_copy():
    """越界 sheet_idx/table_idx 原样返回(深拷贝),不抛。"""
    # Arrange
    raw = {"sheets": [{"name": "A", "tables": [{"name": "t1"}]}]}
    # Act
    out1 = SE.with_table_removed(raw, 99, 0)  # sheet 越界
    out2 = SE.with_table_removed(raw, 0, 99)  # table 越界
    # Assert
    assert out1 == raw
    assert out2 == raw
    assert out1 is not raw
    assert out2 is not raw


def test_with_table_added_and_removed_chain():
    """链式调用:先 add 再 remove,最终态应正确(锁住 viz tab 的"加 1 → 删 idx N"流程)。"""
    # Arrange
    raw = {"sheets": [{"name": "A", "tables": [{"name": "t1"}]}]}
    # Act: 新增 → 变 2 张;删 idx 0(t1)→ 变 1 张,内容是新增的那张
    step1 = SE.with_table_added(raw, 0, {"name": "added"})
    step2 = SE.with_table_removed(step1, 0, 0)
    # Assert
    assert step2["sheets"][0]["tables"] == [{"name": "added"}]


# ---------------------------------------------------------------- contributions_to_preview
def _cell(val, addr="S!A1", numeric=True, r=0, c=0):
    """构造 loader.Cell 的简写(Cell 字段顺序:value, addr, numeric, row_idx, col_idx)。"""
    return loader.Cell(val, addr, numeric, r, c)


def test_contributions_to_preview_row_map():
    # Arrange
    contribs = [
        ("row_map", "营业成本", {"2018年": _cell(100.0, "S!C3", r=3, c=2),
                                  "2019年": _cell(120.0, "S!D3", r=3, c=3)}),
        ("row_map", "营业收入", {"2018年": _cell(200.0, "S!C4", r=4, c=2),
                                  "2019年": _cell(240.0, "S!D4", r=4, c=3)}),
    ]
    # Act
    rows, columns, json_obj = SE.contributions_to_preview(contribs, "row_map")
    # Assert
    assert columns == ["标签", "2018年", "2019年"]
    assert rows[0] == {"标签": "营业成本", "2018年": 100.0, "2019年": 120.0}
    assert json_obj == {
        "营业成本": {"2018年": 100.0, "2019年": 120.0},
        "营业收入": {"2018年": 200.0, "2019年": 240.0},
    }


def test_contributions_to_preview_gen_subtotals():
    # Arrange
    contribs = [
        ("gen_subtotals", "合计", {"2018年": _cell(500.0, "S!C3", r=3, c=2)}),
        ("gen_subtotals", "巴西", {"2018年": _cell(50.0, "S!C5", r=5, c=2),
                                    "2019年": _cell(60.0, "S!D5", r=5, c=3)}),
    ]
    # Act
    rows, columns, json_obj = SE.contributions_to_preview(contribs, "gen_subtotals")
    # Assert
    assert columns == ["小计", "2018年", "2019年"]
    assert rows[0] == {"小计": "合计", "2018年": 500.0}
    assert json_obj["巴西"] == {"2018年": 50.0, "2019年": 60.0}


def test_contributions_to_preview_gen_detail():
    # Arrange
    contribs = [
        ("gen_detail", {"name": "甲项目", "方式": "水电", "区域": "巴西",
                        "values": {"2018年": _cell(1.0, "S!F4", r=4, c=5)}}),
        ("gen_detail", {"name": "乙项目", "方式": "风电", "区域": "智利",
                        "values": {"2019年": _cell(2.0, "S!G5", r=5, c=6)}}),
    ]
    # Act
    rows, columns, json_obj = SE.contributions_to_preview(contribs, "gen_detail")
    # Assert
    assert columns == ["name", "方式", "区域", "2018年", "2019年"]
    assert rows[1] == {"name": "乙项目", "方式": "风电", "区域": "智利", "2019年": 2.0}
    assert isinstance(json_obj, list) and len(json_obj) == 2
    assert json_obj[0]["values"] == {"2018年": 1.0}
    assert json_obj[1]["name"] == "乙项目"


def test_contributions_to_preview_empty():
    assert SE.contributions_to_preview([], "row_map") == ([], [], {})
    assert SE.contributions_to_preview([], "gen_subtotals") == ([], [], {})
    assert SE.contributions_to_preview([], "gen_detail") == ([], [], [])


def test_contributions_to_preview_colkey_union_order():
    # Arrange:row0 只有 2018年,row1 只有 2019年 → 并集 first-seen = [2018年, 2019年]
    contribs = [
        ("row_map", "a", {"2018年": _cell(1.0, r=3, c=2)}),
        ("row_map", "b", {"2019年": _cell(2.0, r=4, c=3)}),
    ]
    # Act
    rows, columns, _ = SE.contributions_to_preview(contribs, "row_map")
    # Assert:列序按首次出现;缺失列不在该行 dict(交由页面 fillna)
    assert columns == ["标签", "2018年", "2019年"]
    assert "2019年" not in rows[0]
    assert "2018年" not in rows[1]


# ---------------------------------------------------------------- with_table_edited 写 name
def test_with_table_edited_writes_name():
    # Arrange:_sample 财务主表 name="财务主表"
    raw = _sample_workbook()
    # Act
    out = SE.with_table_edited(raw, 0, 0, {"name": "财务主表(改名)"})
    # Assert
    assert out["sheets"][0]["tables"][0]["name"] == "财务主表(改名)"


def test_with_table_edited_blank_name_keeps_original():
    # Arrange:空串不应覆盖原名(防误清空成无名表)
    raw = _sample_workbook()
    # Act
    out = SE.with_table_edited(raw, 0, 0, {"name": ""})
    # Assert:保留磁盘原名
    assert out["sheets"][0]["tables"][0]["name"] == "财务主表"


# ---------------------------------------------------------------- _yaml_to_viz_dce 边界
# 锁住 UI INCLUSIVE ↔ YAML EXCLUSIVE 桥接的语义:语义改动一旦回归,UI 上
# 用户会看到"未指定"而不是"包含最后一列",破坏 Phase 9.3 UX 直觉修复。
class TestYamlToVizDce:
    def test_unset_and_none_return_unset(self):
        assert SE._yaml_to_viz_dce(None, 10) == SE.UNSET
        assert SE._yaml_to_viz_dce(SE.UNSET, 10) == SE.UNSET

    def test_zero_returns_unset(self):
        """EXCLUSIVE 0 = 空范围,无意义 → UNSET。"""
        assert SE._yaml_to_viz_dce(0, 10) == SE.UNSET

    def test_negative_returns_unset(self):
        assert SE._yaml_to_viz_dce(-1, 10) == SE.UNSET  # 负值同 UNSET 走 else
        assert SE._yaml_to_viz_dce(-5, 10) == SE.UNSET

    def test_equals_ncols_returns_last_index(self):
        """EXCLUSIVE n_cols = 切片到末尾,与 UI '包含最后一列'等价 → viz_dce = n_cols-1。
        修复 9.3 回归:之前误归 UNSET,UI 显示'未指定'让用户看不出'已设到末尾'。"""
        assert SE._yaml_to_viz_dce(10, 10) == 9
        assert SE._yaml_to_viz_dce(23, 23) == 22
        assert SE._yaml_to_viz_dce(1, 1) == 0  # 1 列 sheet 的边界

    def test_exceeds_ncols_returns_unset(self):
        """EXCLUSIVE > n_cols 越界,stale,默认走 df_shape1。"""
        assert SE._yaml_to_viz_dce(11, 10) == SE.UNSET
        assert SE._yaml_to_viz_dce(999, 10) == SE.UNSET

    def test_in_range_subtracts_one(self):
        """EXCLUSIVE dce (1..n_cols-1) → INCLUSIVE dce-1。"""
        assert SE._yaml_to_viz_dce(5, 10) == 4
        assert SE._yaml_to_viz_dce(1, 10) == 0  # EXCLUSIVE 1 → INCLUSIVE 0 = 第一列
        assert SE._yaml_to_viz_dce(9, 10) == 8  # EXCLUSIVE 9 → INCLUSIVE 8

    def test_non_int_returns_unset(self):
        assert SE._yaml_to_viz_dce("10", 10) == SE.UNSET  # type: ignore[arg-type]
        assert SE._yaml_to_viz_dce(10.0, 10) == SE.UNSET  # type: ignore[arg-type]

    def test_round_trip_with_last_column(self):
        """Phase 9.3 回归锁:dce==n_cols 经 UI 一轮回来,YAML 应仍是 n_cols。"""
        for n_cols in (5, 10, 23, 30):
            viz = SE._yaml_to_viz_dce(n_cols, n_cols)
            assert viz == n_cols - 1
            yaml_back = SE._viz_to_yaml_dce(viz)
            assert yaml_back == n_cols


# ---------------------------------------------------------------- contributions_to_preview expected_columns
class TestContributionsToPreviewExpectedColumns:
    """锁住'空列也展示'语义:某列所有数据行为空时,UI 仍展示列头。"""

    def test_expected_columns_appear_even_when_no_cell_populated(self):
        # Arrange: 列 X 出现在 expected_columns,但所有 contribs 都不含 X
        contribs = [
            ("row_map", "营业成本", {"2018年": _cell(100.0, r=3, c=2),
                                      "2019年": _cell(120.0, r=3, c=3)}),
        ]
        expected = ["2018年", "2019年", "合计完成"]  # 合计完成 在 contribs 里完全没出现
        # Act
        rows, columns, json_obj = SE.contributions_to_preview(
            contribs, "row_map", expected_columns=expected)
        # Assert
        assert "合计完成" in columns
        # 列顺序:expected 先入(first-seen),后续 contrib colkeys 不重复
        assert columns == ["标签", "2018年", "2019年", "合计完成"]
        # 行 dict 不含合计完成(没值,fillna 兜底)— 该列保留在列头里
        assert "合计完成" not in rows[0]
        assert rows[0]["2018年"] == 100.0

    def test_expected_columns_none_falls_back_to_contribs(self):
        """不传 expected_columns 时,行为与原来一致(纯 contribs 推导)。"""
        contribs = [
            ("row_map", "a", {"2018年": _cell(1.0, r=3, c=2)}),
        ]
        rows, columns, _ = SE.contributions_to_preview(contribs, "row_map")
        assert columns == ["标签", "2018年"]

    def test_expected_columns_dedup_with_contribs(self):
        """expected 已有 + contribs 也有的 colkey,不重复。"""
        contribs = [
            ("row_map", "a", {"2018年": _cell(1.0), "2020年": _cell(3.0)}),
        ]
        expected = ["2018年", "2019年"]  # 2018年 重复
        rows, columns, _ = SE.contributions_to_preview(
            contribs, "row_map", expected_columns=expected)
        assert columns == ["标签", "2018年", "2019年", "2020年"]  # first-seen 保序

    def test_expected_columns_for_gen_detail(self):
        """gen_detail 也走 expected_columns 注入路径。"""
        contribs = [
            ("gen_detail", {"name": "甲", "方式": "水电", "区域": "巴西",
                            "values": {"2018年": _cell(1.0)}}),
        ]
        rows, columns, _ = SE.contributions_to_preview(
            contribs, "gen_detail", expected_columns=["2018年", "2025年"])
        assert columns == ["name", "方式", "区域", "2018年", "2025年"]
        # 2025年 在 contribs 没值,不出现在 row dict;fillna 兜底
        assert "2025年" not in rows[0]
        assert rows[0]["name"] == "甲"

    def test_empty_contribs_with_expected_columns(self):
        """contribs 空时 fast-return,expected_columns 不生效(已是空表 UX)。"""
        rows, columns, json_obj = SE.contributions_to_preview(
            [], "row_map", expected_columns=["2018年"])
        assert rows == []
        assert columns == []
        assert json_obj == {}

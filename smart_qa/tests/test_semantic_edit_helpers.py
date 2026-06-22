"""semantic_edit_helpers 单测:纯函数,AAA 结构。

覆盖:解析、locator_shape 五态、跨层选项来源(row/subtotal/taxonomy)、
即时 resolve、跨文件 lint、指标补丁(写/清/改名/撞名/隔离/不可变)、序列化往返。
"""
from __future__ import annotations
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))

import semantic_edit_helpers as SE  # noqa: E402


# ---------------------------------------------------------------- 解析
def test_load_metrics_returns_dict():
    assert SE.load_metrics("利润总额:\n  unit: 亿元\n") == {"利润总额": {"unit": "亿元"}}


def test_load_metrics_invalid_returns_none():
    assert SE.load_metrics("not: [a: b") is None   # YAML 语法错
    assert SE.load_metrics("- a\n- b\n") is None    # 顶层非 dict(是 list)
    assert SE.load_metrics("") in (None, {})        # 空文本:safe_load → None → None


# ---------------------------------------------------------------- locator_shape
def test_locator_shape_five_forms():
    assert SE.locator_shape({"derived": True, "base_metric": "x"}) == "derived"
    assert SE.locator_shape({"aggregation": "sum_by_方式"}) == "taxonomy"
    assert SE.locator_shape({"is_subtotal": True, "locator": {"row": "巴西"}}) == "subtotal"
    assert SE.locator_shape({"locator": {"sheet": "财务数据", "row": "利润总额"}}) == "row_map"
    assert SE.locator_shape({"locator": {"sheet": "财务数据"}}) == "unknown"
    assert SE.locator_shape({}) == "unknown"
    assert SE.locator_shape("not a dict") == "unknown"


def test_locator_shape_priority_derived_over_others():
    # derived 即使带 aggregation/is_subtotal 也优先
    assert SE.locator_shape({"derived": True, "aggregation": "x"}) == "derived"


# ---------------------------------------------------------------- row_label_options
def test_row_label_options_dedup_and_order():
    # Arrange: col 1 = 标签列
    grid = [["", "项目"], ["", "利润总额"], ["", "收入"], ["", "利润总额"]]
    # Act
    out = SE.row_label_options(grid, label_col_idx=1)
    # Assert: 去重 + 保序
    assert out == ["项目", "利润总额", "收入"]


def test_row_label_options_empty_and_bad_col():
    assert SE.row_label_options([], 1) == []
    assert SE.row_label_options([["a"]], -1) == []
    assert SE.row_label_options([["", ""], ["", ""]], 1) == []  # 全空


# ---------------------------------------------------------------- subtotal_key_options
def test_subtotal_key_options_from_schema():
    # Arrange
    schema = {"sheets": [{"name": "发电量", "tables": [
        {"name": "年度明细", "target": "gen_detail", "subtotal_rules": []},
        {"name": "年度小计", "target": "gen_subtotals", "subtotal_rules": [
            {"match_substring": "发电量合计", "emit_key": "合计"},
            {"match_substring": "合计", "emit_key": "合计"},        # 同 emit_key 去重
            {"match_substring": "（一）巴西", "emit_key": "巴西"}]},
    ]}]}
    # Act / Assert: 不指定 table → 该 sheet 所有 gen_subtotals 并集,去重保序
    assert SE.subtotal_key_options(schema, "发电量") == ["合计", "巴西"]
    # 指定 table
    assert SE.subtotal_key_options(schema, "发电量", "年度小计") == ["合计", "巴西"]
    assert SE.subtotal_key_options(schema, "发电量", "年度明细") == []   # 非 gen_subtotals
    assert SE.subtotal_key_options(schema, "其他sheet") == []
    assert SE.subtotal_key_options({}, "发电量") == []


# ---------------------------------------------------------------- taxonomy_node_options
def test_taxonomy_node_options_excludes_meta_keys():
    # Arrange:aggregation_rules 子项只有 description → 非树,应排除
    taxonomy = {
        "发电方式": {"风电": {"includes": ["陆上风电"]}, "水电": {"includes": ["水电"]}},
        "区域": {"巴西": ["巴西"], "南亚": ["南亚", "巴基斯坦"]},
        "aggregation_rules": {"avoid_subtotal_overlap": {"description": "x"}},
    }
    # Act
    out = SE.taxonomy_node_options(taxonomy)
    # Assert
    assert "风电" in out and "水电" in out and "巴西" in out and "南亚" in out
    assert "avoid_subtotal_overlap" not in out   # 元键被排除
    assert SE.taxonomy_node_options({}) == []


# ---------------------------------------------------------------- resolve_locator
def test_resolve_locator_row_map_hit_and_miss():
    # Arrange
    schema = {"sheets": [{"name": "财务数据", "tables": [
        {"name": "财务主表", "target": "row_map", "label_col_idx": 1}]}]}
    grid = {"财务数据": [["", "项目"], ["", "利润总额"], ["", "收入"]]}
    # Act / Assert: 命中
    ok, msg = SE.resolve_locator(
        {"locator": {"sheet": "财务数据", "row": "利润总额"}}, grid, schema)
    assert ok and "利润总额" in msg and "r01" in msg
    # 未命中
    ok2, msg2 = SE.resolve_locator(
        {"locator": {"sheet": "财务数据", "row": "不存在"}}, grid, schema)
    assert not ok2 and "不存在" in msg2


def test_resolve_locator_subtotal_hit_and_miss():
    schema = {"sheets": [{"name": "发电量", "tables": [
        {"name": "年度小计", "target": "gen_subtotals",
         "subtotal_rules": [{"match_substring": "巴西", "emit_key": "巴西"}]}]}]}
    ok, _ = SE.resolve_locator(
        {"is_subtotal": True, "locator": {"sheet": "发电量", "row": "巴西"}}, {}, schema)
    assert ok
    ok2, msg2 = SE.resolve_locator(
        {"is_subtotal": True, "locator": {"sheet": "发电量", "row": "火星"}}, {}, schema)
    assert not ok2 and "火星" in msg2


def test_resolve_locator_derived_with_and_without_base():
    metrics = {"利润总额": {"unit": "亿元"}}
    ok, _ = SE.resolve_locator(
        {"derived": True, "base_metric": "利润总额"}, {}, {}, metrics=metrics)
    assert ok
    ok2, msg2 = SE.resolve_locator(
        {"derived": True, "base_metric": "不存在"}, {}, {}, metrics=metrics)
    assert not ok2 and "不存在" in msg2
    ok3, _ = SE.resolve_locator({"derived": True, "base_metric": "x"}, {}, {})  # 无 metrics 不校验存在
    assert ok3
    ok4, _ = SE.resolve_locator({"derived": True}, {}, {})   # 缺 base_metric
    assert not ok4


# ---------------------------------------------------------------- lint_metrics
def _clean_semantic():
    """一份跨文件一致的 metrics/taxonomy/synonyms/schema(lint 应无 error)。"""
    metrics = {
        "利润总额": {"locator": {"sheet": "财务数据", "row": "利润总额"},
                    "synonyms": ["利润"], "unit": "亿元"},
        "发电量": {"locator": {"sheet": "发电量", "table": "年度", "row": "发电量合计"},
                  "synonyms": ["总发电量"], "unit": "亿千瓦时"},
        "风电发电量": {"parent": "发电量", "taxonomy_node": "风电",
                      "aggregation": "sum_by_方式", "locator": {"sheet": "发电量"},
                      "synonyms": ["风电"]},
        "巴西发电量": {"is_subtotal": True,
                      "locator": {"sheet": "发电量", "row": "巴西"}},
    }
    taxonomy = {"发电方式": {"风电": {"includes": ["陆上风电"]}}}
    synonyms = {"metric_aliases": {"利润": "利润总额"}}
    schema = {"sheets": [
        {"name": "财务数据", "tables": [
            {"name": "t", "target": "row_map", "label_col_idx": 1}]},
        {"name": "发电量", "tables": [
            {"name": "年度小计", "target": "gen_subtotals",
             "subtotal_rules": [{"match_substring": "巴西", "emit_key": "巴西"}]}]},
    ]}
    return metrics, taxonomy, synonyms, schema


def test_lint_clean_has_no_errors():
    m, tx, sy, sc = _clean_semantic()
    findings = SE.lint_metrics(m, tx, sy, sc)
    assert not any(s == "error" for s, _, _ in findings)
    assert not any(s == "warn" for s, _, _ in findings)


def test_lint_bad_sheet_is_error():
    m = {"X": {"locator": {"sheet": "不存在的sheet", "row": "a"}, "synonyms": ["x"]}}
    f = SE.lint_metrics(m, {}, {}, {"sheets": []})
    assert any(s == "error" and "locator.sheet" in msg for s, n, msg in f)


def test_lint_bad_taxonomy_node_is_error():
    m = {"X": {"taxonomy_node": "不存在的节点", "synonyms": ["x"]}}
    tx = {"发电方式": {"风电": {"includes": []}}}
    f = SE.lint_metrics(m, tx, {}, {"sheets": [{"name": "s"}]})
    assert any(s == "error" and "taxonomy_node" in msg for s, n, msg in f)


def test_lint_bad_parent_is_error():
    m = {"X": {"parent": "无此指标", "synonyms": ["x"]}}
    f = SE.lint_metrics(m, {}, {}, {"sheets": []})
    assert any(s == "error" and "parent" in msg for s, n, msg in f)


def test_lint_subtotal_row_not_in_emitkeys_is_error():
    m = {"X": {"is_subtotal": True, "locator": {"sheet": "发电量", "row": "火星"}}}
    sc = {"sheets": [{"name": "发电量", "tables": [
        {"name": "t", "target": "gen_subtotals",
         "subtotal_rules": [{"match_substring": "巴西", "emit_key": "巴西"}]}]}]}
    f = SE.lint_metrics(m, {}, {}, sc)
    assert any(s == "error" and "emit_keys" in msg for s, n, msg in f)


def test_lint_duplicate_alias_is_warn():
    m = {"A": {"synonyms": ["收入"]}, "B": {"synonyms": ["收入"]}}
    f = SE.lint_metrics(m, {}, {}, {"sheets": []})
    assert any(s == "warn" and "收入" in msg for s, n, msg in f)


# ---------------------------------------------------------------- with_metric_edited
def test_with_metric_edited_writes_fields():
    m = {"A": {"unit": "x"}}
    out = SE.with_metric_edited(m, "A", {"unit": "亿元", "synonyms": ["a", "b"],
                                         "locator": {"sheet": "财务数据", "row": "A"}})
    assert out["A"]["unit"] == "亿元"
    assert out["A"]["synonyms"] == ["a", "b"]
    assert out["A"]["locator"] == {"sheet": "财务数据", "row": "A"}


def test_with_metric_edited_blank_removes_optional_keys():
    m = {"A": {"unit": "x", "synonyms": ["a"], "note": "n"}}
    out = SE.with_metric_edited(m, "A", {"unit": "", "synonyms": [], "note": ""})
    assert "unit" not in out["A"]
    assert "synonyms" not in out["A"]
    assert "note" not in out["A"]


def test_with_metric_edited_rename_preserves_order():
    m = {"A": {}, "B": {}, "C": {}}
    out = SE.with_metric_edited(m, "B", {"_new_name": "B2", "unit": "y"})
    assert list(out.keys()) == ["A", "B2", "C"]
    assert out["B2"]["unit"] == "y"


def test_with_metric_edited_rename_collision_raises():
    m = {"A": {}, "B": {}}
    with pytest.raises(ValueError):
        SE.with_metric_edited(m, "A", {"_new_name": "B"})


def test_with_metric_edited_sibling_isolation_and_preserves_unknown():
    m = {"A": {"unit": "x", "synonyms": ["a"], "custom_field": 99}, "B": {"unit": "z"}}
    out = SE.with_metric_edited(m, "A", {"unit": "new"})
    assert out["B"] == {"unit": "z"}                 # 兄弟不动
    assert out["A"]["synonyms"] == ["a"]             # 既有字段保留
    assert out["A"]["custom_field"] == 99            # 未知字段也保留


def test_with_metric_edited_does_not_mutate_input():
    m = {"A": {"unit": "x"}}
    SE.with_metric_edited(m, "A", {"unit": "y"})
    assert m["A"]["unit"] == "x"                     # 入参未被改


def test_with_metric_edited_new_metric_when_absent():
    m = {"A": {"unit": "x"}}
    out = SE.with_metric_edited(m, "新指标", {"unit": "亿元"})
    assert "新指标" in out and out["新指标"]["unit"] == "亿元"
    assert out["A"] == {"unit": "x"}


# ---------------------------------------------------------------- delete / add
def test_delete_metric():
    m = {"A": {}, "B": {}}
    out = SE.delete_metric(m, "A")
    assert list(out.keys()) == ["B"]
    assert SE.delete_metric(m, "不存在") == m          # 不存在原样


def test_add_metric_idempotent():
    m = {"A": {"unit": "x"}}
    out = SE.add_metric(m, "B")
    assert "B" in out and out["B"] == {"locator": {}, "unit": ""}
    out2 = SE.add_metric(out, "B")                    # 已存在不覆盖
    assert out2["B"] == {"locator": {}, "unit": ""}
    assert SE.add_metric(m, "") == m                   # 空名无操作


# ---------------------------------------------------------------- dump_metrics_yaml
def test_dump_metrics_yaml_roundtrip_and_chinese():
    m = {"利润总额": {"unit": "亿元", "synonyms": ["利润"]}}
    text = SE.dump_metrics_yaml(m)
    assert "利润总额" in text            # allow_unicode
    import yaml
    assert yaml.safe_load(text) == m     # 往返


def test_dump_metrics_yaml_preserves_order():
    m = {"甲": {"unit": "1"}, "乙": {"unit": "2"}, "丙": {"unit": "3"}}
    text = SE.dump_metrics_yaml(m)
    assert text.index("甲") < text.index("乙") < text.index("丙")


def test_dump_metrics_yaml_non_dict_returns_empty():
    assert SE.dump_metrics_yaml(None) == ""
    assert SE.dump_metrics_yaml([]) == ""


# ================================================================ taxonomy 编辑(Phase B)
def _sample_taxonomy():
    return {
        "发电方式": {
            "风电": {"includes": ["陆上风电", "海上风电"], "description": "风力"},
            "水电": {"includes": ["水电"]},
        },
        "区域": {"巴西": ["巴西"], "南亚": ["南亚", "巴基斯坦"]},
        "aggregation_rules": {"avoid_subtotal_overlap": {"description": "x"}},
    }


def test_taxonomy_categories_flags_trees():
    cats = dict(SE.taxonomy_categories(_sample_taxonomy()))
    assert cats["发电方式"] is True
    assert cats["区域"] is True
    assert cats["aggregation_rules"] is False   # 元键非树


def test_tree_nodes_handles_list_and_dict_shapes():
    nodes = {n["name"]: n for n in SE.tree_nodes(_sample_taxonomy(), "区域")}
    assert nodes["巴西"]["is_list"] is True
    assert nodes["巴西"]["includes"] == ["巴西"]
    nodes2 = {n["name"]: n for n in SE.tree_nodes(_sample_taxonomy(), "发电方式")}
    assert nodes2["风电"]["is_list"] is False
    assert nodes2["风电"]["includes"] == ["陆上风电", "海上风电"]
    assert nodes2["风电"]["description"] == "风力"


def test_with_taxonomy_node_edited_preserves_list_shape():
    tx = _sample_taxonomy()
    out = SE.with_taxonomy_node_edited(tx, "区域", "巴西",
                                       {"includes": ["巴西", "圣保罗"]})
    assert out["区域"]["巴西"] == ["巴西", "圣保罗"]   # 仍是 list
    assert tx["区域"]["巴西"] == ["巴西"]               # 入参不变


def test_with_taxonomy_node_edited_preserves_dict_shape_with_desc():
    tx = _sample_taxonomy()
    out = SE.with_taxonomy_node_edited(tx, "发电方式", "水电",
                                       {"includes": ["水电", "抽水蓄能"], "description": "水电含抽蓄"})
    assert out["发电方式"]["水电"] == {"includes": ["水电", "抽水蓄能"], "description": "水电含抽蓄"}


def test_with_taxonomy_node_edited_rename_and_collision():
    tx = _sample_taxonomy()
    out = SE.with_taxonomy_node_edited(tx, "发电方式", "水电", {"_new_name": "水力", "includes": ["水电"]})
    assert "水力" in out["发电方式"] and "水电" not in out["发电方式"]
    assert list(out["发电方式"].keys()) == ["风电", "水力"]   # 保序
    with pytest.raises(ValueError):
        SE.with_taxonomy_node_edited(tx, "发电方式", "水电", {"_new_name": "风电"})


def test_add_delete_taxonomy_node_and_category():
    tx = _sample_taxonomy()
    out = SE.add_taxonomy_node(tx, "发电方式", "光伏")
    assert out["发电方式"]["光伏"] == {"includes": []}
    out = SE.delete_taxonomy_node(out, "发电方式", "风电")
    assert "风电" not in out["发电方式"]
    out = SE.add_taxonomy_category(tx, "新分类")
    assert "新分类" in out
    out = SE.delete_taxonomy_category(out, "新分类")
    assert "新分类" not in out


def test_dump_taxonomy_yaml_roundtrip():
    tx = {"发电方式": {"风电": {"includes": ["陆上风电"]}}}
    text = SE.dump_taxonomy_yaml(tx)
    import yaml
    assert yaml.safe_load(text) == tx


# ================================================================ synonyms 编辑(Phase B)
def _sample_synonyms():
    return {
        "entities": {"三峡国际": {"aliases": ["公司", "三峡"], "means": "三峡口径"}},
        "metric_aliases": {"利润": "利润总额", "发电": "发电量"},
        "time_aliases": {"近一年": "latest_year"},
        "quantity_aliases": {"累计": "cumulative"},
    }


def test_metric_alias_pairs_and_edit_roundtrip():
    sy = _sample_synonyms()
    pairs = SE.metric_alias_pairs(sy)
    assert pairs == [("利润", "利润总额"), ("发电", "发电量")]
    out = SE.with_metric_aliases_edited(sy, [("发电", "发电量"), ("新词", "总装机"), ("", "空"), ("dup", "")])
    assert out["metric_aliases"] == {"发电": "发电量", "新词": "总装机"}
    assert sy["metric_aliases"] == {"利润": "利润总额", "发电": "发电量"}  # 入参不变


def test_entity_rows_and_edit():
    sy = _sample_synonyms()
    rows = SE.entity_rows(sy)
    assert rows == [{"name": "三峡国际", "aliases": ["公司", "三峡"], "means": "三峡口径"}]
    out = SE.with_entities_edited(sy, [
        {"name": "三峡国际", "aliases": ["公司"], "means": ""},
        {"name": "集团", "aliases": ["母公司"], "means": "集团口径"},
        {"name": "", "aliases": ["x"]},   # 空名丢弃
    ])
    assert out["entities"] == {
        "三峡国际": {"aliases": ["公司"]},                       # means 空→不写
        "集团": {"aliases": ["母公司"], "means": "集团口径"},
    }


def test_kv_pairs_and_edit():
    sy = _sample_synonyms()
    assert SE.kv_pairs(sy, "time_aliases") == [("近一年", "latest_year")]
    out = SE.with_kv_edited(sy, "time_aliases", [("近三年", "recent_3_years"), ("", "x")])
    assert out["time_aliases"] == {"近三年": "recent_3_years"}
    assert out["quantity_aliases"] == {"累计": "cumulative"}   # 其他 section 保留


def test_lint_synonyms_bad_alias_target_and_entity_collision():
    sy = {"metric_aliases": {"利润": "利润总额", "坏": "不存在的指标"},
          "entities": {"A": {"aliases": ["公司"]}, "B": {"aliases": ["公司"]}}}
    f = SE.lint_synonyms(sy, {"利润总额": {}})
    assert any(s == "error" and "不是已定义指标" in m for s, _, m in f)
    assert any(s == "warn" and "公司" in m for s, _, m in f)


def test_dump_synonyms_yaml_roundtrip():
    sy = _sample_synonyms()
    import yaml
    assert yaml.safe_load(SE.dump_synonyms_yaml(sy)) == sy


# ================================================================ rules(Phase C2/C3)
def _sample_rules():
    return {
        "recent_years": {"windows": {"近三年": [2023, 2024, 2025]},
                         "cagr_initial_rule": {"近三年": {"initial_year": 2022}}},
        "monthly_ytd": {"description": "月度列=当年累计"},
        "cagr": {"formula": "(e/s)^(1/n)-1", "example": {"question": "近三年增长"}},
    }


def test_rules_summary_extracts_description_and_example():
    summ = dict(SE.rules_summary(_sample_rules()))
    assert "月度列=当年累计" in summ["monthly_ytd"]
    assert "近三年增长" in summ["cagr"]   # 取自 example.question
    assert summ["recent_years"] == ""     # 无 description/example → 空


def test_lint_rules_flags_missing_recent_years_subkeys():
    f = SE.lint_rules({"recent_years": {"windows": {}}})   # 缺 cagr_initial_rule
    assert any(s == "warn" and "cagr_initial_rule" in m for s, _, m in f)
    f2 = SE.lint_rules({})   # 整段缺
    assert any(s == "warn" and "recent_years" in m for s, _, m in f)
    f3 = SE.lint_rules(_sample_rules())   # 完整 → 无 warn
    assert f3 == []

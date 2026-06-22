"""validate.py 单测:每条 V 规则至少覆盖一个场景。"""
import os
import pytest
import schema_spec as SS
import validate as V


def _build_spec(target="row_map", **overrides) -> SS.WorkbookSpec:
    tbl_kwargs = dict(
        name="test_tbl",
        header_row=2,
        first_data_row=3,
        target=target,
        data_col_start=2,
    )
    tbl_kwargs.update(overrides)
    sh = SS.SheetSpec(name="财务数据", tables=[SS.TableSpec(**tbl_kwargs)])
    return SS.WorkbookSpec(path="<dummy>", sheets=[sh])


def test_validate_missing_sheet():
    spec = SS.WorkbookSpec(
        path=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "测试数据.xls")),
        sheets=[SS.SheetSpec(name="不存在的Sheet", tables=[])],
    )
    errors = V.validate(spec)
    assert any("V1" in e and "not found" in e for e in errors)


def test_validate_real_schema_passes():
    here = os.path.dirname(os.path.abspath(__file__))
    spec_path = os.path.abspath(os.path.join(here, "..", "schemas", "三峡国际经营数据库.yaml"))
    spec = SS._load_spec(spec_path)
    wb_path = os.path.abspath(os.path.join(here, "..", "..", "测试数据.xls"))
    errors = V.validate(spec, wb_path)
    assert errors == [], f"committed schema should pass: {errors}"
"""指纹回归测试:用 golden snapshot 验证 loader 与 legacy 输出一致。

golden 快照由 tests/capture_fingerprint.py 一次性生成并提交。
任何 cell 的 value/addr/numeric 漂移都会让本测试失败。
"""
import json
import os
import sys

# 共享 helper:capture_fingerprint 同款序列化
import capture_fingerprint as CF


def _serialize_grid(g) -> dict:
    return {
        "fin": {lbl: {ck: CF._cell_to_dict(c) for ck, c in row.items()}
                for lbl, row in g.fin.items()},
        "cap": {lbl: {ck: CF._cell_to_dict(c) for ck, c in row.items()}
                for lbl, row in g.cap.items()},
        "gen_projects": CF._serialize_gen_projects(g.gen_projects),
        "gen_subtotals": CF._serialize_gen_subtotals(g.gen_subtotals),
    }


def _load_golden(workbook_basename: str) -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "golden", f"{workbook_basename.split('.')[0]}_legacy.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_fingerprint_matches_golden(workbook_path):
    """加载新 loader,逐字段与 golden snapshot 对比。"""
    import preprocess as PRE

    PRE.XLS = workbook_path
    g = PRE.load_grid()
    actual = _serialize_grid(g)
    actual_full = {
        "workbook": os.path.basename(workbook_path),
        "engine": "xlrd",
        "schema_version": "generic-v1",
        **actual,
    }

    golden = _load_golden(os.path.basename(workbook_path))

    # 字段级对比,失败时给出局部 diff
    for field in ("fin", "cap", "gen_projects", "gen_subtotals"):
        if actual_full[field] != golden[field]:
            _diff_and_dump(field, actual_full[field], golden[field])
            raise AssertionError(f"指纹不匹配:字段 {field!r} 有差异(见上方 diff)")

    # 总维度对比(给个可读摘要)
    assert set(actual_full["fin"]) == set(golden["fin"]), \
        f"fin labels diverged: only_new={set(actual_full['fin'])-set(golden['fin'])}"
    assert set(actual_full["cap"]) == set(golden["cap"])
    assert len(actual_full["gen_projects"]) == len(golden["gen_projects"])
    assert set(actual_full["gen_subtotals"]) == set(golden["gen_subtotals"])


def _diff_and_dump(field, actual, golden):
    print(f"\n[DIFF] field = {field}")
    if field in ("gen_projects",):
        for i, (pa, pb) in enumerate(zip(actual, golden)):
            if pa != pb:
                print(f"  gen_projects[{i}]: name={pa.get('name')!r}")
                for ck in set(pa.get("values", {})) | set(pb.get("values", {})):
                    if pa["values"].get(ck) != pb["values"].get(ck):
                        print(f"    {ck}: actual={pa['values'].get(ck)} golden={pb['values'].get(ck)}")
    else:
        for lbl in set(actual) | set(golden):
            if lbl not in actual:
                print(f"  {field} {lbl!r}: ONLY IN GOLDEN")
            elif lbl not in golden:
                print(f"  {field} {lbl!r}: ONLY IN ACTUAL")
            else:
                for ck in set(actual[lbl]) | set(golden[lbl]):
                    if actual[lbl].get(ck) != golden[lbl].get(ck):
                        print(f"  {field} {lbl!r} {ck}: actual={actual[lbl].get(ck)} golden={golden[lbl].get(ck)}")
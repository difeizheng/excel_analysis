"""一次性脚本:捕获当前 preprocess.load_grid() 的输出为 golden JSON。

**必须在任何重构之前运行**,作为后续回归测试的对照基线。

用法:
    python tests/capture_fingerprint.py
    # 输出: tests/golden/测试数据_legacy.json

约定:
- 序列化所有 Cell 的 (value, addr, numeric, row_idx, col_idx)
- 排序后写入,保证 order-independent
- 写入文件同时打印 4 个 Grid 字段的规模摘要
"""
import json
import os
import sys

# 让脚本既可被 pytest 调用,也可独立运行
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_REPO = os.path.abspath(os.path.join(_ROOT, ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _REPO)
sys.path.insert(0, _ROOT)

import preprocess as PRE


def _cell_to_dict(cell) -> dict:
    return {
        "value": cell.value,
        "addr": cell.addr,
        "numeric": cell.numeric,
        "row_idx": cell.row_idx,
        "col_idx": cell.col_idx,
    }


def _serialize_row_map(row_map: dict) -> dict:
    """row_map 形态: {label: {colkey: Cell}} → {label: {colkey: cell_dict}}"""
    out = {}
    for label, cells in row_map.items():
        out[label] = {ck: _cell_to_dict(c) for ck, c in cells.items()}
    return out


def _serialize_gen_projects(projects: list) -> list:
    out = []
    for p in projects:
        out.append({
            "name": p["name"],
            "方式": p["方式"],
            "区域": p["区域"],
            "values": {ck: _cell_to_dict(c) for ck, c in p["values"].items()},
        })
    return out


def _serialize_gen_subtotals(subtotals: dict) -> dict:
    return {k: {ck: _cell_to_dict(c) for ck, c in v.items()} for k, v in subtotals.items()}


def capture(workbook_path: str) -> dict:
    PRE.XLS = workbook_path  # override 默认路径
    g = PRE.load_grid()
    return {
        "workbook": os.path.basename(workbook_path),
        "engine": "xlrd",  # 当前 .xls 默认引擎,钉住以便后续重生成
        "schema_version": "legacy",
        "fin": _serialize_row_map(g.fin),
        "cap": _serialize_row_map(g.cap),
        "gen_projects": _serialize_gen_projects(g.gen_projects),
        "gen_subtotals": _serialize_gen_subtotals(g.gen_subtotals),
    }


def main():
    xls = os.path.join(_REPO, "测试数据.xls")
    if not os.path.exists(xls):
        print(f"[ERR] 找不到 {xls}", file=sys.stderr)
        sys.exit(1)

    snap = capture(xls)
    out_path = os.path.join(_HERE, "golden", f"{snap['workbook'].split('.')[0]}_legacy.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2, sort_keys=True)

    # 摘要
    print(f"[OK] golden snapshot written: {out_path}")
    print(f"     workbook:           {snap['workbook']} (engine={snap['engine']})")
    print(f"     fin labels:         {len(snap['fin'])}")
    print(f"     cap labels:         {len(snap['cap'])}")
    print(f"     gen_projects:       {len(snap['gen_projects'])}")
    print(f"     gen_subtotals keys: {sorted(snap['gen_subtotals'].keys())}")
    total_cells = (
        sum(len(v) for v in snap['fin'].values())
        + sum(len(v) for v in snap['cap'].values())
        + sum(len(p['values']) for p in snap['gen_projects'])
        + sum(len(v) for v in snap['gen_subtotals'].values())
    )
    print(f"     total cells:        {total_cells}")


if __name__ == "__main__":
    main()

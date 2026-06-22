# -*- coding: utf-8 -*-
"""导出完整 Grid: 写 grid_full.json(每个 cell 全 5 字段) + 控制台完整紧凑打印。"""
import sys
import os
import json

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
import preprocess as PRE

grid = PRE.load_grid()


def cell_to_dict(c):
    return {"value": c.value, "addr": c.addr, "numeric": c.numeric,
            "row_idx": c.row_idx, "col_idx": c.col_idx}


def val(v):
    return f"{v:g}" if isinstance(v, float) else str(v)


# ---- 完整 JSON ----
full = {
    "fin": {lbl: {ck: cell_to_dict(c) for ck, c in cells.items()}
            for lbl, cells in grid.fin.items()},
    "cap": {lbl: {ck: cell_to_dict(c) for ck, c in cells.items()}
            for lbl, cells in grid.cap.items()},
    "gen_projects": [
        {k: ({ck: cell_to_dict(c) for ck, c in v.items()} if k == "values" else v)
         for k, v in p.items()}
        for p in grid.gen_projects
    ],
    "gen_subtotals": {k: {ck: cell_to_dict(c) for ck, c in cells.items()}
                      for k, cells in grid.gen_subtotals.items()},
}
out_json = os.path.join(HERE, "grid_full.json")
with open(out_json, "w", encoding="utf-8") as f:
    json.dump(full, f, ensure_ascii=False, indent=2)


# ---- 统计 ----
def count_rowmap(d):
    return sum(len(cells) for cells in d.values())


n_fin = count_rowmap(grid.fin)
n_cap = count_rowmap(grid.cap)
n_gen = sum(len(p["values"]) for p in grid.gen_projects)
n_sub = sum(len(cells) for cells in grid.gen_subtotals.values())
total = n_fin + n_cap + n_gen + n_sub

print("=" * 76)
print(f" 完整 Grid 已导出 -> {out_json}")
print(f" 总 cell 数: fin={n_fin} + cap={n_cap} + gen_projects={n_gen} "
      f"+ gen_subtotals={n_sub} = {total}")
print("=" * 76)

# ---- 紧凑完整打印(每条一行) ----
def dump_rowmap(name, d):
    print(f"\n### {name}  ({len(d)} labels)   结构 = {{行标签: {{列键: Cell}}}}")
    for lbl, cells in d.items():
        parts = [f"{ck}={val(c.value)}@{c.addr}" for ck, c in cells.items()]
        print(f"  {lbl}: " + " | ".join(parts))


def dump_gen_projects():
    print(f"\n### gen_projects  ({len(grid.gen_projects)} items)   "
          f"结构 = [{{name, 方式, 区域, values:{{列键:Cell}}}}]")
    for i, p in enumerate(grid.gen_projects):
        parts = [f"{ck}={val(c.value)}@{c.addr}" for ck, c in p["values"].items()]
        print(f"  [{i:>2}] {p['name']}  | 方式={p['方式']}  区域={p['区域']}")
        print(f"        " + " | ".join(parts))


def dump_subtotals():
    print(f"\n### gen_subtotals  ({len(grid.gen_subtotals)} keys)   "
          f"结构 = {{emit_key: {{列键: Cell}}}}")
    for k, cells in grid.gen_subtotals.items():
        parts = [f"{ck}={val(c.value)}@{c.addr}" for ck, c in cells.items()]
        print(f"  {k}: " + " | ".join(parts))


dump_rowmap("fin", grid.fin)
dump_rowmap("cap", grid.cap)
dump_gen_projects()
dump_subtotals()

# -*- coding: utf-8 -*-
"""一次性检视脚本: 打印 preprocess.load_grid() 产出的 Grid(人类可读摘要)。

Grid 是预处理层(①)交给下游的唯一中间数据结构。本脚本只读不改。
"""
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
import preprocess as PRE

g = PRE.load_grid()


def val(c):
    """数值用 %g 去尾零, 文本原样。"""
    v = c.value
    if c.numeric and isinstance(v, float):
        return f"{v:g}"
    return str(v)


def colkeys_of(values):
    return list(values.keys())


W = 74
print("=" * W)
print(" Grid 概览  — preprocess.load_grid() 的总产出(4 个字段)")
print("=" * W)
print(f"  fin           : {len(g.fin):>3} 个行标签")
print(f"  cap           : {len(g.cap):>3} 个行标签")
print(f"  gen_projects  : {len(g.gen_projects):>3} 个明细项目")
print(f"  gen_subtotals : {len(g.gen_subtotals):>3} 个小计键 -> {list(g.gen_subtotals.keys())}")

# ---- 列键形状 ----
print("\n" + "-" * W)
print(" 列键(colkey)形状  —— 下游 pipeline/engine 按这些字符串 key 取数")
print("-" * W)
if g.fin:
    ks = colkeys_of(next(iter(g.fin.values())))
    print(f"  fin : {len(ks)} 列 -> {ks}")
if g.cap:
    ks = colkeys_of(next(iter(g.cap.values())))
    print(f"  cap : {len(ks)} 列 -> {ks}")
if g.gen_projects:
    ks = colkeys_of(g.gen_projects[0]["values"])
    print(f"  gen : {len(ks)} 列 -> {ks}")

# ---- Cell 实例 ----
print("\n" + "-" * W)
print(" Cell 实例(最小单元, 5 个字段)  —— addr 是溯源凭证(接地/发票铁律的载体)")
print("-" * W)
for lbl in ["利润总额", "合计"]:
    d = g.fin.get(lbl) or g.cap.get(lbl)
    if d:
        sample = next(iter(d.values()))
        print(f"  {lbl}: Cell(value={val(sample)}, addr={sample.addr!r}, "
              f"numeric={sample.numeric}, row_idx={sample.row_idx}, col_idx={sample.col_idx})")
        break

# ---- fin / cap 全部标签 ----
print("\n" + "-" * W)
print(" fin 全部行标签")
print("-" * W)
print("   " + " | ".join(g.fin.keys()))
print("\n" + "-" * W)
print(" cap 全部行标签")
print("-" * W)
print("   " + " | ".join(g.cap.keys()))

# ---- fin 切片 ----
print("\n" + "-" * W)
print(" fin 切片  结构 = {行标签: {列键: Cell}}   取 2025年 + 2026-02(月度YTD)")
print("-" * W)
for lbl in ["利润总额", "汇兑净损失", "向集团分红", "净资产收益率"]:
    d = g.fin.get(lbl)
    if not d:
        continue
    cells = []
    for ck in ["2025年", "2026-02"]:
        if ck in d:
            cells.append(f"{ck}={val(d[ck])}@{d[ck].addr}")
    print(f"  {lbl:<10} | " + "  ".join(cells))

# ---- cap 切片 ----
print("\n" + "-" * W)
print(" cap 切片  结构 = {行标签: {列键: Cell}}   取 2025年")
print("-" * W)
for lbl in ["合计", "可控装机", "权益装机"]:
    d = g.cap.get(lbl)
    if not d:
        continue
    c = d.get("2025年")
    print(f"  {lbl:<10} | 2025年={val(c)}@{c.addr}" if c else f"  {lbl:<10} | (无 2025年)")

# ---- gen_projects ----
print("\n" + "-" * W)
print(" gen_projects 切片  结构 = [{name, 方式, 区域, values:{列键:Cell}}]")
print(" * 标记为风电类(对应测试用例 6: 风电=陆上+海上)")
print("-" * W)
for p in g.gen_projects:
    way = p["方式"]
    flag = " *" if "风电" in way else "  "
    c25 = p["values"].get("2025年")
    print(f"{flag}{p['name']:<24} | 方式={way:<8} | 区域={p['区域']:<6} | 2025={val(c25) if c25 else '-'}")

# 单个项目完整展开
print("\n  —— 单个项目完整展开(values 里每个 Cell 都带 addr) ——")
if g.gen_projects:
    p = next((p for p in g.gen_projects if "风电" in p["方式"]), g.gen_projects[0])
    print(f"  name={p['name']!r}  方式={p['方式']!r}  区域={p['区域']!r}")
    for ck, c in p["values"].items():
        print(f"    {ck:<8} -> {val(c):>10}  @{c.addr}")

# ---- gen_subtotals ----
print("\n" + "-" * W)
print(" gen_subtotals 切片  结构 = {emit_key: {列键: Cell}}   (必有 '合计' 键)")
print("-" * W)
for k, d in g.gen_subtotals.items():
    c25 = d.get("2025年")
    print(f"  {k:<6} | 列数={len(colkeys_of(d))} | 2025年={val(c25) if c25 else '-'}  @{c25.addr if c25 else ''}")

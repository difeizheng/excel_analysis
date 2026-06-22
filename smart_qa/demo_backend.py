# -*- coding: utf-8 -*-
"""演示取数 backend: memory vs sqlite 两条独立路径 + 双引擎互验 C5。"""
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
import preprocess as PRE
import backend as B
import qa

grid = PRE.load_grid()
q = "公司2018年的利润总额是多少？"

print("=" * 64)
print(" 同一个语义坐标 (利润总额·三峡国际·2018年),两条路各取一次")
print("=" * 64)

be_mem = B.make_backend(grid, "memory")
cv_mem, label = be_mem.lookup("利润总额", "三峡国际", "2018年")
print(f"  memory backend.lookup -> {cv_mem}")

be_sql = B.make_backend(grid, "sqlite")
cv_sql, _ = be_sql.lookup("利润总额", "三峡国际", "2018年")
print(f"  sqlite backend.lookup -> {cv_sql}")

print(f"\n  两条独立代码路径(CellView 是否相等): {cv_mem == cv_sql}")
be_sql.close()

print("\n" + "=" * 64)
print(' 双引擎互验 backend="both": memory + sqlite 各跑一遍 + C5 比对')
print("=" * 64)
ans = qa.ask(grid, q, backend="both")
print(f"  verified   = {ans['verified']}")
print(f"  verify_msg = {ans['verify_msg']}")
print(f"  答案       = {ans['text'].splitlines()[0]}")

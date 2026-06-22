"""预处理层入口(向后兼容)。

职责已委托给 `loader.py`:
- Cell / Grid 数据类定义保留(避免破坏下游 import)
- load_grid() 委托 loader.load_grid()
- load_fin / load_cap / load_gen 作为薄包装保留(向后兼容旧调用方)
- XLS 常量保留(部分历史脚本可能引用)

新逻辑(通用 schema 驱动)见:
- src/loader.py
- src/schema_spec.py
- schemas/*.yaml
"""
from __future__ import annotations
import os
import sys
from typing import Any

# 复用 loader 的 Cell/Grid(保持位置构造顺序一致)
from loader import Cell, Grid

# 默认工作簿路径(保持与历史一致)
XLS = os.path.join(os.path.dirname(__file__), "..", "..", "测试数据.xls")


# ============================================================ 主入口(委托)
def load_grid(spec_path: str | None = None) -> Grid:
    """委托 loader.load_grid()。

    默认加载 schemas/三峡国际经营数据库.yaml(committed schema,已验证)。
    传 spec_path 可切换 schema。
    """
    import loader as L
    # 委托前允许外部覆盖 XLS(向后兼容: 旧代码 PRE.XLS = ... 再调用)
    # 通过临时改 env var 路径实现;此处直接走 schema 路径解析
    return L.load_grid(spec_path)


def load_fin() -> dict:
    """向后兼容:返回 grid.fin。"""
    return load_grid().fin


def load_cap() -> dict:
    """向后兼容:返回 grid.cap。"""
    return load_grid().cap


def load_gen():
    """向后兼容:返回 (gen_projects, gen_subtotals)。"""
    g = load_grid()
    return g.gen_projects, g.gen_subtotals


# ============================================================ __main__ 自检
if __name__ == "__main__":
    g = load_grid()
    print("FIN 指标数:", len(g.fin))
    print("CAP 指标数:", len(g.cap))
    print("GEN 明细项目数:", len(g.gen_projects))
    print("GEN 小计区:", list(g.gen_subtotals.keys()))
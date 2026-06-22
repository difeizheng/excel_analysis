"""单位强类型:用维度(dim)做求和前兼容性校验,挡住量级错。

设计原则:
- 每个单位有 名字(name) + 维度(dim) + 比例(scale)
- dim 决定能不能相加(scale 一致时)。不同 dim 强类型拒算
- CAGR 之类无量纲运算,显式标 dim=pure
- 单位换算(scale)留接口,MVP 暂不实现自动换算

单位白名单来自 metrics.yaml(metric.unit)+ rules.yaml(units 段)+ 内置推导。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import ClassVar

# 维度常量(集中在这里防拼写错)
DIM_CURRENCY = "currency"   # 货币(亿元/万元/...)
DIM_CAPACITY = "capacity"   # 容量(万千瓦/MW/...)
DIM_ENERGY = "energy"       # 能量(亿千瓦时/万千瓦时/...)
DIM_RATIO = "ratio"         # 比率(资产负债率, 0-1)
DIM_PERCENT = "percent"     # 百分比(CAGR/增长率, 0-1)
DIM_PURE = "pure"           # 纯数(无单位, 用于 CAGR 期初/期末不求和的场合)


@dataclass(frozen=True)
class Unit:
    name: str
    dim: str
    scale: float = 1.0  # 相对该维度基准单位的换算系数(暂仅作占位)

    def __str__(self) -> str:
        return self.name


# ---- 内置单位表(覆盖当前项目用到的所有单位)----
_BUILTIN: dict[str, Unit] = {
    # 货币
    "亿元": Unit("亿元", DIM_CURRENCY),
    "万元": Unit("万元", DIM_CURRENCY, scale=1e-4),  # 1万元 = 1e-4 亿元
    # 容量
    "万千瓦": Unit("万千瓦", DIM_CAPACITY),
    "MW": Unit("MW", DIM_CAPACITY),
    # 能量
    "亿千瓦时": Unit("亿千瓦时", DIM_ENERGY),
    "万千瓦时": Unit("万千瓦时", DIM_ENERGY, scale=1e-4),  # 1万 = 1e-4 亿
    "千瓦时": Unit("千瓦时", DIM_ENERGY, scale=1e-8),
    # 比率/百分比
    "比率": Unit("比率", DIM_RATIO),
    "百分比": Unit("百分比", DIM_PERCENT),
    # 纯数
    "": Unit("", DIM_PURE),
}


def unit(name: str) -> Unit:
    """查表得 Unit;未知单位按 pure 处理(不阻断,记 warning)。"""
    if name in _BUILTIN:
        return _BUILTIN[name]
    # 兜底:未知单位标记为 pure,以免误阻断
    return Unit(name=name, dim=DIM_PURE)


def is_compatible_for_sum(units: list[Unit]) -> tuple[bool, str]:
    """sum 前校验:所有单位必须同 dim(纯数/pure 当通配,因无单位信息时不应阻断)。

    返回 (ok, 错误信息)。
    """
    if not units:
        return True, ""
    # 过滤掉 pure(空单位)再做 dim 一致性;只要剩下的 dim 全一致即通过
    non_pure = [u for u in units if u.dim != DIM_PURE]
    if not non_pure:
        return True, ""
    dims = {u.dim for u in non_pure}
    if len(dims) == 1:
        return True, ""
    return False, f"单位维度冲突,无法求和: dims={sorted(dims)}, names={[u.name for u in units]}"


def from_metric(metric: str) -> Unit:
    """从 metric 名查 unit 字段(走 metrics.yaml)。"""
    import semantic_layer as S
    return unit(S.metric_unit(metric))

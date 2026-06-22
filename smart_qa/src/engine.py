"""确定性执行引擎:所有数值运算在此完成,LLM 碰不到。

每个操作返回 Result,内含完整溯源链(操作数 + 单元格地址 + 公式 + 命中规则)。
单位强类型校验: sum 前 assert 同 dim,挡量级错。
"""
from __future__ import annotations
from dataclasses import dataclass, field
import units as U


class UnitDimensionError(ValueError):
    """单位维度不兼容(试图把不同 dim 的值相加)。"""


@dataclass
class Operand:
    addr: str        # 物理地址,如 "发电量!L14"
    label: str       # 语义标签,如 "巴西帕尔梅拉风电(陆上风电)"
    value: float
    unit: str
    dim: str = ""    # 单位维度,sum 前校验用;空表示未标注


@dataclass
class Result:
    value: float
    unit: str
    operation: str
    operands: list[Operand] = field(default_factory=list)
    formula: str = ""
    rules: list[str] = field(default_factory=list)
    note: str = ""


def lookup(cell, label: str, unit: str) -> Result:
    """单点取数。"""
    return Result(
        value=float(cell.value), unit=unit, operation="lookup",
        operands=[Operand(cell.addr, label, float(cell.value), unit)],
        formula=f"{label} = {cell.value}",
    )


def sum_cells(cells: list[tuple], unit: str) -> Result:
    """求和。cells = [(cell, label), ...]。跳过非数值项(如"转至参股")。

    强类型:所有有效 operand 必须同 dim,否则抛 UnitDimensionError 阻断。
    """
    u = U.unit(unit)
    ops: list[Operand] = []
    total = 0.0
    for cell, label in cells:
        if not cell or not getattr(cell, "numeric", False):
            continue
        cell_unit = getattr(cell, "unit", unit) or unit
        cell_u = U.unit(cell_unit)
        # dim 强一致(允许空 unit 的兜底)
        if cell_u.dim != u.dim and cell_u.dim != U.DIM_PURE and u.dim != U.DIM_PURE:
            raise UnitDimensionError(
                f"求和单位维度冲突: 期望 dim={u.dim}({u.name}), "
                f"但 {label}@{cell.addr} dim={cell_u.dim}({cell_u.name})"
            )
        ops.append(Operand(cell.addr, label, float(cell.value), cell_unit, cell_u.dim))
        total += float(cell.value)
    addrs = " + ".join(o.addr for o in ops)
    formula = f"Σ({len(ops)}项) = {addrs} = {round(total, 4)}"
    return Result(value=total, unit=unit, operation="sum", operands=ops, formula=formula)


def cagr(initial: float, end: float, n: int, init_cell, end_cell,
         init_label: str, end_label: str) -> Result:
    """复合年增长率 = (期末/期初)^(1/n) - 1。"""
    ratio = (end / initial) ** (1.0 / n) - 1.0
    ops = [
        Operand(init_cell.addr, init_label, initial, "", U.DIM_PURE),
        Operand(end_cell.addr, end_label, end, "", U.DIM_PURE),
    ]
    formula = (f"CAGR = (期末/期初)^(1/年数) - 1 "
               f"= ({end}/{initial})^(1/{n}) - 1 = {round(ratio*100, 2)}%")
    return Result(value=ratio, unit="百分比", operation="cagr",
                  operands=ops, formula=formula,
                  rules=[f"cagr: 年数={n}; 期初取窗口前一年底"])


def yoy(curr: float, prev: float, curr_cell, prev_cell,
        curr_label: str, prev_label: str) -> Result:
    """同比增长率 = (本期 - 上期) / 上期。比率,unit=百分比,operands 标 DIM_PURE。"""
    ratio = (curr - prev) / prev
    ops = [
        Operand(prev_cell.addr, prev_label, prev, "", U.DIM_PURE),
        Operand(curr_cell.addr, curr_label, curr, "", U.DIM_PURE),
    ]
    formula = (f"同比 = (本期 - 上期)/上期 "
               f"= ({curr} - {prev})/{prev} = {round(ratio*100, 2)}%")
    return Result(value=ratio, unit="百分比", operation="yoy",
                  operands=ops, formula=formula, rules=["yoy: 与上年同期比"])


def share(part: float, total: float, part_cell, total_cell,
          part_label: str, total_label: str) -> Result:
    """占比 = 部分 / 总体。比率,unit=百分比,operands 标 DIM_PURE。"""
    ratio = part / total
    ops = [
        Operand(part_cell.addr, part_label, part, "", U.DIM_PURE),
        Operand(total_cell.addr, total_label, total, "", U.DIM_PURE),
    ]
    formula = (f"占比 = 部分/总体 "
               f"= {part}/{total} = {round(ratio*100, 2)}%")
    return Result(value=ratio, unit="百分比", operation="share",
                  operands=ops, formula=formula,
                  rules=["share: 部分 ÷ 总体(默认发电量合计)"])


def peak_year(best_year: int, best_cell, label: str) -> Result:
    """峰值年:某指标全年份中取值最大的年份(argmax,strict >,首年赢并列)。

    value=年份(int,非数值量),operands=[峰值单元格](带 addr,供 C2 回查)。
    C1/C3 对 peak_year 跳过(argmax 非可重算算术 / 年份无量纲)。
    """
    peak_val = float(best_cell.value)
    op = Operand(best_cell.addr, label, peak_val, "", U.DIM_PURE)
    formula = (f"峰值年 = {best_year}年 ({label} = {round(peak_val, 4)} 最高)")
    return Result(value=int(best_year), unit="", operation="peak_year",
                  operands=[op], formula=formula,
                  rules=["peak_year: 全年份取值最高的年份"])

"""校验 + 格式化 + 顶层入口 ask()。

校验层 4 项独立复核(C1-C4),任一不通过则标 verified=False:
  C1 重算      用不同代码路径再算一遍(纯 python sum/手算 CAGR)
  C2 单元格回查  按 addr 重新从 grid 读值,比对 operand.value
  C3 单位一致   result.unit dim 必须与 metric 声明的 dim 一致
  C4 小计查重   检查 taxonomy 展开是否混入了小计/区域合计行
"""
from __future__ import annotations
import logging
import re
import parser as P
import pipeline as PL
import engine as E
import units as U
import backend as B

log = logging.getLogger(__name__)

# 发电量区域小计的特征串(防误入求和)
SUBTOTAL_MARKERS = ("（一）", "（二）", "（三）", "（四）", "发电量合计", "合计", "小计")


def fmt_num(v, unit: str) -> str:
    if v is None:
        return "<未取到>"
    if unit == "百分比":
        return f"{v*100:.2f}%"
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v))}"
    return f"{v:.2f}"


def _c1_recompute(res) -> tuple[bool, str]:
    """C1:用不同代码路径重算。"""
    if res.operation == "lookup":
        return True, "C1 跳过(lookup)"
    if res.operation == "peak_year":
        return True, "C1 跳过(peak_year/argmax)"
    if res.operation == "sum":
        if not res.operands:
            return False, "C1 sum 无 operand"
        recon = sum(o.value for o in res.operands)  # 纯 python sum
        ok = abs(recon - res.value) < 1e-6
        return ok, f"C1 重算={round(recon, 4)} ({'一致' if ok else '不一致'})"
    if res.operation == "cagr":
        if len(res.operands) < 2:
            return False, "C1 cagr 缺操作数"
        a, b = res.operands[0].value, res.operands[1].value
        # n 取自 formula 或默认 3(从 rules.yaml 推导本期不变)
        n = 3
        m = re.search(r"1/(\d+)", res.formula)
        if m:
            n = int(m.group(1))
        recon = (b / a) ** (1 / n) - 1
        ok = abs(recon - res.value) < 1e-6
        return ok, f"C1 重算={round(recon * 100, 2)}% ({'一致' if ok else '不一致'})"
    if res.operation == "yoy":
        # E.yoy operands = [上期, 本期];ratio = (本期-上期)/上期
        if len(res.operands) < 2:
            return False, "C1 yoy 缺操作数"
        prev, curr = res.operands[0].value, res.operands[1].value
        if prev == 0:
            return False, "C1 yoy 上期为0"
        recon = (curr - prev) / prev
        ok = abs(recon - res.value) < 1e-6
        return ok, f"C1 重算={round(recon * 100, 2)}% ({'一致' if ok else '不一致'})"
    if res.operation == "share":
        # E.share operands = [部分, 总体];ratio = 部分/总体
        if len(res.operands) < 2:
            return False, "C1 share 缺操作数"
        part, total = res.operands[0].value, res.operands[1].value
        if total == 0:
            return False, "C1 share 总体为0"
        recon = part / total
        ok = abs(recon - res.value) < 1e-6
        return ok, f"C1 重算={round(recon * 100, 2)}% ({'一致' if ok else '不一致'})"
    return True, "C1 无适用检查"


def _c2_cell_recheck(src, res) -> tuple[bool, str]:
    """C2:按 addr 经 backend 重新读值,比对 operand.value。

    src 可传 backend(有 cell_by_addr)或 grid(自动包 MemoryBackend)——
    后者为兼容旧的直接调用(如测试里 qa._c2_cell_recheck(grid, res))。
    """
    be = src if hasattr(src, "cell_by_addr") else B.MemoryBackend(src)
    bad: list[str] = []
    for o in res.operands:
        if not o.addr or "!" not in o.addr:
            continue
        cv = be.cell_by_addr(o.addr)
        if cv is None:
            bad.append(f"{o.addr}:不可定位")
            continue
        try:
            grid_v = float(cv.value)
            if abs(grid_v - o.value) > 1e-6:
                bad.append(f"{o.addr}:grid={grid_v} vs operand={o.value}")
        except (TypeError, ValueError):
            # cell 是文本(如"转至参股")而非数值;sum 时本应被跳过
            if cv.numeric:
                bad.append(f"{o.addr}:grid非数值")
    if bad:
        return False, f"C2 单元格回查失败: {'; '.join(bad[:3])}"
    return True, f"C2 单元格回查一致({len(res.operands)}项)"


def _c3_unit_consistent(res, metric: str | None) -> tuple[bool, str]:
    """C3:result.unit 的 dim 必须与 metric 声明的 dim 一致(比率/argmax 除外)。"""
    if res.operation in ("cagr", "lookup", "yoy", "share", "peak_year"):
        return True, f"C3 跳过({res.operation})"
    expected_u = U.from_metric(metric) if metric else None
    if expected_u is None:
        return True, "C3 跳过(无 metric 声明)"
    actual_u = U.unit(res.unit)
    if actual_u.dim == expected_u.dim:
        return True, f"C3 一致: dim={actual_u.dim}"
    return False, f"C3 维度冲突: result.unit={actual_u.name}({actual_u.dim}) vs metric={metric}({expected_u.dim})"


def _c4_subtotal_check(res) -> tuple[bool, str]:
    """C4:检查 operand.label 是否混入小计/区域合计行(防重复计入)。"""
    bad: list[str] = []
    for o in res.operands:
        for marker in SUBTOTAL_MARKERS:
            if marker in (o.label or ""):
                bad.append(f"{o.addr}:{o.label} 命中小计标记「{marker}」")
                break
    if bad:
        return False, f"C4 疑似小计入账: {'; '.join(bad[:3])}"
    return True, "C4 无小计混入"


def verify(ans: dict, be=None, grid=None) -> tuple[bool, str]:
    """独立重算 + 4 项校验。

    be:  取数后端(优先,用于 C2 单元格回查)。
    grid: be 为 None 时兜底包成 MemoryBackend(grid) 做 C2;两者皆无则跳过 C2。
    """
    if ans["kind"] == "fail":
        return False, "未取到数据"
    if ans["kind"] == "multi":
        miss = [it["metric"] for it in ans["items"] if it["value"] is None]
        if miss:
            return False, f"multi 缺值: {miss}"
        return True, "multi 全部取到"

    res = ans["result"]
    metric = None
    if hasattr(ans.get("intent"), "metric"):
        metric = ans["intent"].metric

    # C1
    ok1, msg1 = _c1_recompute(res)
    if not ok1:
        return False, msg1
    # C2(优先 be;否则 MemoryBackend(grid);皆无则跳过)
    c2_src = be if be is not None else grid
    if c2_src is not None:
        ok2, msg2 = _c2_cell_recheck(c2_src, res)
        if not ok2:
            return False, msg2
    else:
        msg2 = "C2 跳过(无 grid/backend)"
    # C3
    ok3, msg3 = _c3_unit_consistent(res, metric)
    if not ok3:
        return False, msg3
    # C4
    ok4, msg4 = _c4_subtotal_check(res)
    if not ok4:
        return False, msg4
    return True, f"{msg1}; {msg2}; {msg3}; {msg4}"


def format_answer(ans: dict, intent) -> str:
    lines = []
    if ans["kind"] == "fail":
        return f"  ⚠ 无法回答：{ans['msg']}\n  （系统拒绝编造，请确认指标/时间）"

    if ans["kind"] == "multi":
        lines.append("  答：")
        for it in ans["items"]:
            v = fmt_num(it["value"], it["unit"])
            lines.append(f"    · {it['label']} = {v} {it['unit']}  [{it['addr']}]")
        lines.append("  规则依据：")
        for r in ans.get("rules", []):
            lines.append(f"    - {r}")
        return "\n".join(lines)

    res = ans["result"]
    if res.operation == "peak_year":
        # 峰值年:value=年份(int);operands[0]=峰值单元格(带值+addr)
        lines.append(f"  答：{int(res.value)}年最高")
        lines.append(f"  计算链：{res.formula}")
        if res.operands:
            lines.append(f"  溯源（峰值单元格）：")
            for o in res.operands[:12]:
                lines.append(f"    · {o.addr}  {o.label}  {fmt_num(o.value, '')}")
        return "\n".join(lines)
    unit = "百分比" if res.operation in ("cagr", "yoy", "share") else res.unit
    lines.append(f"  答：{fmt_num(res.value, unit)}" + ("" if unit == "百分比" else f" {unit}"))
    lines.append(f"  计算链：{res.formula}")
    if res.operands:
        lines.append(f"  溯源（{len(res.operands)}项）：")
        for o in res.operands[:12]:
            ou = o.unit if o.unit else ("" if res.operation == "cagr" else unit)
            lines.append(f"    · {o.addr}  {o.label}  {fmt_num(o.value, ou)}")
        if len(res.operands) > 12:
            lines.append(f"    · ... 其余 {len(res.operands)-12} 项")
    if res.rules:
        lines.append("  规则依据：")
        for r in res.rules:
            lines.append(f"    - {r}")
    return "\n".join(lines)


def ask(grid, question: str, use_llm: bool = False,
        backend: str = "memory", db_path: str | None = None):
    """端到端:解析 -> 执行 -> 校验 -> (答案文本, 结构)。

    参数:
        grid:     preprocess.Grid 语义化单元格网格(memory/both 模式取数/校验用)
        question: 用户自然语言问题
        use_llm:  True=尝试 LLM(失败回退规则);False=纯规则(默认,无需 key)
        backend:  "memory"(默认) | "sqlite" | "both"(双引擎互验)
        db_path:  sqlite/both 模式的 SQLite 路径(None 用默认 data/grid.db)
    """
    if use_llm:
        intent = P.parse_hybrid(question, fallback=True)
    else:
        intent = P.parse(question)

    if backend == "both":
        return _ask_both(grid, intent, db_path)

    be = B.make_backend(grid, backend, db_path)
    ans = PL.execute(grid, intent, backend=backend, db_path=db_path)
    ok, msg = verify(ans, be=be)
    ans["verified"] = ok
    ans["verify_msg"] = msg
    ans["intent"] = intent
    ans["text"] = format_answer(ans, intent)
    return ans


def _cross_check(ans_mem: dict, ans_sql: dict) -> tuple[bool, str]:
    """C5 双引擎互验:比对两路结果的 value + addr 集合。

    addr 必须用集合(taxonomy 求和时 memory 按列表序、SQLite 按返回序,顺序可能不同)。
    """
    if ans_mem["kind"] != ans_sql["kind"]:
        return False, f"kind 不一致 mem={ans_mem['kind']} sql={ans_sql['kind']}"
    if ans_mem["kind"] == "fail":
        return True, "双引擎均拒绝(一致)"
    if ans_mem["kind"] == "multi":
        v_mem = sorted(it["value"] for it in ans_mem["items"] if it["value"] is not None)
        v_sql = sorted(it["value"] for it in ans_sql["items"] if it["value"] is not None)
        a_mem = {it["addr"] for it in ans_mem["items"]}
        a_sql = {it["addr"] for it in ans_sql["items"]}
        if v_mem != v_sql:
            return False, f"multi value 不一致 mem={v_mem} sql={v_sql}"
        if a_mem != a_sql:
            return False, f"multi addr 不一致 mem-only={a_mem - a_sql} sql-only={a_sql - a_mem}"
        return True, "multi 一致"
    # single
    r_mem, r_sql = ans_mem["result"], ans_sql["result"]
    if abs(r_mem.value - r_sql.value) > 1e-9:
        return False, f"value 不一致 mem={r_mem.value} sql={r_sql.value}"
    addr_mem = {o.addr for o in r_mem.operands}
    addr_sql = {o.addr for o in r_sql.operands}
    if addr_mem != addr_sql:
        return False, f"addr 集合不一致 mem-only={addr_mem - addr_sql} sql-only={addr_sql - addr_mem}"
    return True, "single 一致"


def _ask_both(grid, intent, db_path: str | None = None) -> dict:
    """双引擎互验:memory + sqlite 各跑一遍,以 memory 为主答案 + C5 交叉校验。"""
    be_mem = B.make_backend(grid, "memory")
    be_sql = B.make_backend(grid, "sqlite", db_path)
    ans_mem = PL.execute(grid, intent, backend="memory")
    ans_sql = PL.execute(grid, intent, backend="sqlite", db_path=db_path)

    ok_mem, msg_mem = verify(ans_mem, be=be_mem)
    ok_sql, msg_sql = verify(ans_sql, be=be_sql)
    cross_ok, cross_msg = _cross_check(ans_mem, ans_sql)

    ans = ans_mem                       # 以 memory 为主答案(完整溯源链)
    ans["intent"] = intent
    parts = [msg_mem, msg_sql]
    parts.append("C5 双引擎互验一致" if cross_ok else f"C5 双引擎互验不一致: {cross_msg}")
    ans["verified"] = bool(ok_mem and ok_sql and cross_ok)
    ans["verify_msg"] = "; ".join(parts)
    ans["text"] = format_answer(ans, intent)
    return ans

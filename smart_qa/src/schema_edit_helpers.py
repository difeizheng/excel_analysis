"""可视化 Schema 编辑的纯函数:行列标尺、按内容点选的选项标签、表格补丁、YAML 序列化。

纯逻辑,不依赖 streamlit / pandas / 文件 IO,便于单测。
页面层负责读 Excel → 生成 Grid(字符串化单元格)→ 调用本模块的函数。

为什么单独抽出:
- Schema 编辑的本质是"在表格上指位置"。这些函数把"按内容点选"的认知负荷
  转成可读的选项标签,并把表单值补丁回 workbook dict 再序列化。
- 后端(schema_spec/validate/loader)零改动;本模块是 UI 专用的纯工具。

Grid 约定:list[list[str]],外层为行,内层为该行各列的字符串化单元格(NaN/None→"")。
"""
from __future__ import annotations

import copy
import re

import yaml

# "未指定"哨兵:对应 TableSpec 里可缺省的字段(last_data_row / data_col_end /
# data_col_start / detail_marker_col_idx)。selectbox 用它表示"到末尾/自动"。
UNSET = -1


# ---------------------------------------------------------------- 行列标尺
def col_letter(idx: int) -> str:
    """0-based 列号 → Excel 列字母(0→A, 25→Z, 26→AA, 27→AB)。负数返回 '?'。"""
    if idx < 0:
        return "?"
    s = ""
    n = idx
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


def _short(value, limit: int = 10) -> str:
    """单元格内容截断,去掉会干扰显示的管道/换行。"""
    s = str(value).strip().replace("|", "/").replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


def row_option_label(idx: int, grid: list[list[str]], max_cells: int = 4) -> str:
    """行选项标签:'r03 · 第4行 · 利润总额 | 2018年'。

    取该行前 max_cells 个非空单元格拼成预览,让人"按含义选行"而非按数字。
    """
    cells = grid[idx] if 0 <= idx < len(grid) else []
    preview = [_short(c) for c in cells if str(c).strip()][:max_cells]
    base = f"r{idx:02d} · 第{idx + 1}行"
    return base + (f" · {' | '.join(preview)}" if preview else "")


def col_option_label(
    idx: int, grid: list[list[str]], label_row: int = 0, max_len: int = 10
) -> str:
    """列选项标签:'c01 · B列 · 项目'。

    用 label_row 处的单元格内容作列含义提示(通常取表头行)。无内容则只显标尺。
    """
    cell = ""
    if 0 <= label_row < len(grid) and 0 <= idx < len(grid[label_row]):
        cell = grid[label_row][idx]
    base = f"c{idx:02d} · {col_letter(idx)}列"
    return base + (f" · {_short(cell, max_len)}" if str(cell).strip() else "")


# ---------------------------------------------------------------- 表格补丁
# 视化表单可编辑的核心字段名(与 TableSpec/YAML 键 1:1)。
EDITABLE_KEYS = (
    "header_row",
    "first_data_row",
    "last_data_row",
    "label_col_idx",
    "data_col_start",
    "data_col_end",
    "detail_marker_col_idx",
)


def with_table_edited(raw: dict, sheet_idx: int, table_idx: int, values: dict) -> dict:
    """返回 raw 的深拷贝,其中指定 table 被 values 覆盖(不可变风格,不改入参)。

    values 键见 EDITABLE_KEYS + 'target'。
    - 必填(header_row/first_data_row/label_col_idx):始终写入(UNSET→0 兜底)。
    - 可选(last_data_row/data_col_start/data_col_end/detail_marker_col_idx):
      UNSET/None → 删除该键(loader 视为默认/到末尾);否则写 int。
    - 高级字段(subtotal_rules/detail_classifier_cols/skip_labels 等)原样保留。
    越界或结构异常时原样返回拷贝(不抛)。
    """
    out = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    if not isinstance(raw, dict):
        return out
    sheets = out.get("sheets") or []
    if not (0 <= sheet_idx < len(sheets)):
        return out
    tables = sheets[sheet_idx].get("tables") or []
    if not (0 <= table_idx < len(tables)):
        return out

    table = tables[table_idx]

    if values.get("target"):
        table["target"] = values["target"]

    # 必填字段:有合理值就写,UNSET→0
    for key in ("header_row", "first_data_row", "label_col_idx"):
        if key in values:
            v = values[key]
            table[key] = 0 if v in (UNSET, None) else int(v)

    # 可选字段:UNSET/None → 删除键;否则写 int
    for key in ("last_data_row", "data_col_start", "data_col_end", "detail_marker_col_idx"):
        if key in values:
            v = values[key]
            if v in (UNSET, None):
                table.pop(key, None)
            else:
                table[key] = int(v)

    # 高级字段(由可视化"高级编辑"文本区解析而来):空 → 删键;非空 → 覆盖
    if "subtotal_rules" in values:
        v = values["subtotal_rules"]
        if v:
            table["subtotal_rules"] = v
        else:
            table.pop("subtotal_rules", None)
    if "detail_classifier_cols" in values:
        v = values["detail_classifier_cols"]
        if v:
            table["detail_classifier_cols"] = v
        else:
            table.pop("detail_classifier_cols", None)
    if "skip_labels" in values:
        v = values["skip_labels"]
        if v:
            table["skip_labels"] = v
        else:
            table.pop("skip_labels", None)
    if "skip_label_regex" in values:
        v = str(values["skip_label_regex"] or "").strip()
        if v:
            table["skip_label_regex"] = v
        else:
            table.pop("skip_label_regex", None)

    return out


# ---------------------------------------------------------------- YAML 序列化
def dump_workbook_yaml(raw: dict) -> str:
    """workbook dict → YAML(保持中文、保持键顺序、块风格)。

    注意:SafeDump 不保留注释,且会把 flow 风格({a: 1})规范化为块风格——
    这是"可视化应用"的已知代价;若在意注释/精确格式请用 YAML 原文 tab 手编。
    """
    if not isinstance(raw, dict):
        return ""
    return yaml.safe_dump(
        raw, allow_unicode=True, sort_keys=False, default_flow_style=False
    )


# ---------------------------------------------------------------- 列字母反查
def col_index(token) -> int | None:
    """Excel 列字母或数字字符串 → 0-based idx。'A'→0, 'AA'→26, '3'→3(已是 idx)。无效→None。"""
    s = str(token).strip().upper()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    n = 0
    for ch in s:
        if not ("A" <= ch <= "Z"):
            return None
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


# ---------------------------------------------------------------- 高级字段 parse/format(文本区 ↔ 结构)
def parse_subtotal_rules(text) -> list[dict]:
    """'匹配子串 => 输出键' 每行一条 → [{match_substring, emit_key}]。
    顺序即优先级(first-wins),故用文本行序天然保序。空行/# 注释跳过。
    单边行(无 =>)→ match 与 emit 同名。
    """
    rules: list[dict] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            m, e = line.split("=>", 1)
            m, e = m.strip(), e.strip()
            if m and e:
                rules.append({"match_substring": m, "emit_key": e})
        else:
            rules.append({"match_substring": line, "emit_key": line})
    return rules


def format_subtotal_rules(rules) -> str:
    return "\n".join(
        f"{r['match_substring']} => {r['emit_key']}" for r in (rules or [])
    )


def parse_classifiers(text, n_cols: int | None = None) -> dict:
    """'维度 => 列(字母或 idx)' 每行 → {维度: idx}。列越界(给定 n_cols)或无效→跳过。"""
    out: dict = {}
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=>" not in line:
            continue
        dim, col = line.split("=>", 1)
        dim, col = dim.strip(), col.strip()
        if not dim:
            continue
        idx = col_index(col)
        if idx is None:
            continue
        if n_cols is not None and not (0 <= idx < n_cols):
            continue
        out[dim] = idx
    return out


def format_classifiers(cls) -> str:
    """{维度: idx} → '维度 => 列字母' 每行。"""
    return "\n".join(
        f"{dim} => {col_letter(idx)}" for dim, idx in (cls or {}).items()
    )


def parse_skip_labels(text) -> list[str]:
    return [
        ln.strip()
        for ln in str(text or "").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def format_skip_labels(labels) -> str:
    return "\n".join(labels or [])


# ---------------------------------------------------------------- 应用前预检(普通话 + 高亮坐标)
def preflight_table(vals: dict, n_cols: int):
    """应用前对表单值做友好预检。返回 (普通话错误列表, 高亮[(kind,idx)])。

    只查可视化编辑里高频的人为失误(数据行在表头上方、数据列起≥止、标签列越界);
    不重复 validate.py 的 V1-V10(那套仍由页面在写入后跑)。
    """
    msgs: list[str] = []
    hl: list[tuple[str, int]] = []
    hr, fdr = vals.get("header_row"), vals.get("first_data_row")
    if isinstance(hr, int) and isinstance(fdr, int) and fdr <= hr:
        msgs.append(
            f"首行数据(r{fdr} · 第{fdr+1}行)应在表头(r{hr} · 第{hr+1}行)之下;"
            "当前在表头上方或同行,请下移首行数据。"
        )
        hl += [("row", hr), ("row", fdr)]
    dcs, dce = vals.get("data_col_start"), vals.get("data_col_end")
    if (isinstance(dcs, int) and dcs != UNSET and isinstance(dce, int)
            and dce != UNSET and dcs >= dce):
        msgs.append(
            f"数据起始列(c{dcs} · {col_letter(dcs)}列)必须早于终止列"
            f"(c{dce} · {col_letter(dce)}列,不含本列);当前起≥止。"
        )
        hl += [("col", dcs), ("col", max(dce - 1, 0))]
    lci = vals.get("label_col_idx")
    if isinstance(lci, int) and lci != UNSET and not (0 <= lci < n_cols):
        msgs.append(f"标签列(c{lci})超出预览列数(共 {n_cols} 列)。")
        hl += [("col", lci)]
    return msgs, hl


# ---------------------------------------------------------------- 智能建议(启发式,需用户确认)
_PERIOD_RE = re.compile(r"(?:19|20)\d{2}|\d{4}\s*[-/年]|\d{1,2}\s*月")


def _is_num(cell) -> bool:
    s = str(cell).strip()
    if not s:
        return False
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _looks_like_period(s) -> bool:
    """是否像时间/周期表头(年 2018、年 2018年、月 2026-01、3月)。用于在表头行定位数据列。"""
    return bool(_PERIOD_RE.search(str(s)))


def _period_cols(grid: list[list[str]], hr: int, n_cols: int) -> list[int]:
    """表头行 hr 上,表头像"时间/周期"的列 idx(数据列的强信号)。"""
    if hr >= len(grid):
        return []
    row = grid[hr]
    return [c for c in range(min(n_cols, len(row))) if _looks_like_period(row[c])]


def _contiguous_range(idxs: list[int]) -> tuple[int | None, int | None]:
    """把列 idx 列表压成最长连续段 [start, end_exclusive)。空→(None,None)。"""
    if not idxs:
        return (None, None)
    idxs = sorted(set(idxs))
    best = (idxs[0], idxs[0] + 1)
    cur_s = prev = idxs[0]
    for c in idxs[1:]:
        if c == prev + 1:
            prev = c
        else:
            if (prev + 1 - cur_s) > (best[1] - best[0]):
                best = (cur_s, prev + 1)
            cur_s = prev = c
    if (prev + 1 - cur_s) > (best[1] - best[0]):
        best = (cur_s, prev + 1)
    return best


def _col_numeric_ratio(grid, c: int, r0: int, r1: int) -> float:
    """列 c 在行 [r0,r1) 的数值单元格占非空单元格的比例。"""
    n = tot = 0
    for r in range(r0, r1):
        if r >= len(grid) or c >= len(grid[r]):
            continue
        cell = grid[r][c]
        if not str(cell).strip():
            continue
        tot += 1
        if _is_num(cell):
            n += 1
    return (n / tot) if tot else 0.0


def _longest_numeric_run(grid, r0: int, r1: int, n_cols: int,
                         after: int = 0, min_len: int = 2,
                         threshold: float = 0.5) -> tuple[int | None, int | None]:
    """行[r0,r1) 上、从 after 起的最长连续"高数值占比"列段 → (start, end_exclusive)。

    数据列回退信号(无周期表头时用)。阈值过滤零散数值列(如持股比例被文本列隔断)。
    """
    best = (None, None)
    cur_s = None
    cur_len = 0
    for c in range(max(after, 0), n_cols):
        if _col_numeric_ratio(grid, c, r0, r1) >= threshold:
            if cur_s is None:
                cur_s = c
            cur_len += 1
        else:
            if cur_s is not None and cur_len >= min_len:
                if best[0] is None or cur_len > (best[1] - best[0]):
                    best = (cur_s, cur_s + cur_len)
            cur_s = None
            cur_len = 0
    if cur_s is not None and cur_len >= min_len:
        if best[0] is None or cur_len > (best[1] - best[0]):
            best = (cur_s, cur_s + cur_len)
    return best


def suggest_fields(
    grid: list[list[str]], n_rows: int, n_cols: int, target: str = "row_map"
) -> dict:
    """从预览网格启发式推断 schema 核心字段。**纯建议**,用户须确认。

    策略(针对三峡这类时间序列财报/装机/发电量表调优):
    - header_row: 前 12 行里"非空格 + 周期表头加权"最高的行(年/月表头加权,压过小计行)。
    - first_data_row: 表头下首个非空行(跳过空行/小标题,如装机 row3 空 → row4)。
    - label_col_idx: 前 6 列数据区"文本(非数值)"最多的列(平手取最左)。
    - data range: **优先表头行的周期列(年/月)最长连续段**——这是区分"数据列"与
      "持股比例/一带一路等数值元数据列"的关键(后者表头非周期);无周期表头时回退到
      最长连续数值列段。
    - detail_marker_col_idx(仅 gen_detail): 数据段左侧"文本最多"的列(平手取最右,贴近数据)。
    """
    if n_rows == 0 or n_cols == 0:
        return {}

    head_zone = min(n_rows, 12)

    def row_nonempty(r):
        cells = grid[r] if r < len(grid) else []
        return sum(1 for c in cells if str(c).strip())

    # header + 数据范围:在前 head_zone 行里找"周期列连续段",选【最左】(>=2 列)的段,
    # 其所在行=header_row、该段=数据范围。最左=主表(左→右阅读);以此避开发电量这类
    # "年度表(左)+月度表(右)"并排 sheet 里更大的右表。同左取更长、再取更靠上。
    cand = []
    for r in range(head_zone):
        s, e = _contiguous_range(_period_cols(grid, r, n_cols))
        if s is None or (e - s) < 2:
            continue
        cand.append((s, -(e - s), r, s, e))
    if cand:
        cand.sort()
        _, _, hr, dcs, dce = cand[0]
    else:
        # 无周期表头:回退"非空最多行"作 header,数据范围待数值回退
        hr = max(range(head_zone), key=row_nonempty) if head_zone else 0
        dcs = dce = None

    fdr = hr + 1
    while fdr < n_rows and row_nonempty(fdr) == 0:
        fdr += 1
    fdr = min(fdr, n_rows - 1)
    r1 = min(n_rows, fdr + 15)

    label_zone = min(n_cols, 6)

    def text_count(c):
        return sum(1 for r in range(fdr, r1)
                   if r < len(grid) and c < len(grid[r])
                   and str(grid[r][c]).strip() and not _is_num(grid[r][c]))

    lci = max(range(label_zone), key=text_count) if label_zone > 0 else 0

    # 数据范围回退(无周期表头时):最长连续数值列段
    if dcs is None:
        data_after = 1 if target in ("gen_detail", "gen_subtotals") else (lci + 1)
        dcs, dce = _longest_numeric_run(grid, fdr, r1, n_cols, after=data_after)

    out: dict = {"header_row": hr, "first_data_row": fdr}
    if target in ("row_map", "gen_subtotals"):
        out["label_col_idx"] = lci
    if dcs is not None:
        out["data_col_start"] = dcs
        out["data_col_end"] = dce
    if target == "gen_detail" and dcs is not None:
        cand = [c for c in range(1, dcs)]
        if cand:
            dmc = max(cand, key=lambda c: (text_count(c), c))  # 平手取最右(贴近数据)
            if text_count(dmc) > 0:
                out["detail_marker_col_idx"] = dmc
    return out

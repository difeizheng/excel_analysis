"""LLM schema 自动发现(离线,可选依赖)。

边界:
- LLM 仅描述 Excel 的"结构",绝不暴露/产出真实数值。
- prompt 只喂骨架网格:<empty>/<number>/<text:首12字>/<date>。
- 输出经 validate.py 闸门校验;失败可 1 轮修复(把错误喂回 LLM)。
- 无 LLM 配置时不崩溃,打印引导并返回 None。
- load_grid() 查询时永远不调用本模块(LLM 仅离线 schema 编写)。
- LLM 走统一 llm_client(OpenAI 兼容),与 llm_parser 共用一套配置。

CLI 用法:
    python -m src.schema_proposer <xls-or-xlsx path> [-o out.yaml]
"""
from __future__ import annotations
import os
import sys
import json
import argparse
import datetime as _dt

import yaml
import pandas as pd

# schema_spec / validate / llm_client 均在同目录,放顶层
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import schema_spec as SS
import validate as V
import llm_client


# ============================================================ 骨架渲染(结构防火墙)
def _cell_skeleton(v) -> str:
    """把真实数值替换为类型骨架,防止 LLM 看到具体数据后影响推断。"""
    if v is None:
        return "<empty>"
    if isinstance(v, (_dt.date, _dt.datetime, pd.Timestamp)):
        return f"<date:{getattr(v, 'year', '?')}-{getattr(v, 'month', '?'):02d}>"
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return "<empty>"
    try:
        float(s)
        return "<number>"
    except (TypeError, ValueError):
        return f"<text:{s[:12]}>"


def render_skeleton(df: pd.DataFrame, nrows: int = 8, ncols: int = 12) -> str:
    """前 nrows × ncols 渲染为类型骨架网格。"""
    nrows = min(nrows, df.shape[0])
    ncols = min(ncols, df.shape[1])
    lines = []
    # 列头(0-based)
    header = "     | " + " | ".join(f"c{c:02d}" for c in range(ncols))
    lines.append(header)
    lines.append("-----" + "+-----" * ncols)
    for r in range(nrows):
        cells = [_cell_skeleton(df.iloc[r, c]) for c in range(ncols)]
        lines.append(f"r{r:02d}  | " + " | ".join(cells))
    return "\n".join(lines)


# ============================================================ LLM 调用(统一 llm_client)
# proposer 是离线重任务(喂全表骨架 → 产出完整 schema YAML),用比查询解析更长的超时。
PROPOSER_TIMEOUT = 120


def _try_call_llm(system: str, user: str, timeout: int = PROPOSER_TIMEOUT) -> str | None:
    """调统一 llm_client(OpenAI 兼容)。未配置 / 失败时返回 None。"""
    client = llm_client.get_default()
    if not client.available:
        return None
    try:
        return client.chat(system, user, json_mode=False, timeout=timeout)
    except llm_client.LLMUnavailable as e:
        print(f"[proposer] LLM 调用失败: {e}", file=sys.stderr)
        return None


# ============================================================ Prompt 构建
SYSTEM_PROMPT = """你是 Excel 结构分析助手。你的唯一任务是描述 Excel 的 STRUCTURE(结构),
不要输出任何真实数值(因为 prompt 只给你类型骨架)。

对每个 Sheet 的每个 table,输出这些字段(全部 0-indexed):
- target: row_map | gen_detail | gen_subtotals
- name: 逻辑表名
- header_row: 表头行(0-indexed)
- first_data_row: 【必填】首个数据行(0-indexed,含,通常是 header_row+1)
- last_data_row: 末个数据行(0-indexed,含);不确定时省略(默认到末尾)
- label_col_idx: 行标签列(0-indexed),row_map 必填
- data_col_start / data_col_end: 数据列起止(0-indexed,data_col_end 为 EXCLUSIVE 终止)
- skip_cols: 显式跳过的列 idx 列表
- skip_labels: row_map 专用,精确匹配的跳过标签(如 "备注")
- detail_marker_col_idx: gen_detail/gen_subtotals 专用,行类型判别列
- detail_classifier_cols: gen_detail 专用,字段名(name/方式/区域) → 列 idx
- subtotal_rules: gen_subtotals 专用,match_substring → emit_key

顶层 keys(必填):
- path: Excel 文件名(如 "xxx.xls")
- engine: "xlrd"(.xls) 或 "openpyxl"(.xlsx)
- version: "1"
- sheets[]: 每个含 name + tables[]

输出严格 YAML,只输出 YAML,不要任何解释,不要真实数值。每个 table 必须有 first_data_row。
"""


def _build_user_prompt(samples: dict[str, str], sheet_dims: dict[str, tuple]) -> str:
    parts = ["请根据以下 Excel 各 Sheet 的结构骨架,推断 schema YAML。"]
    for sheet_name, skeleton in samples.items():
        rows, cols = sheet_dims[sheet_name]
        parts.append(f"\n## Sheet: {sheet_name!r} (shape: {rows} rows × {cols} cols)")
        parts.append("```")
        parts.append(skeleton)
        parts.append("```")
    parts.append("\n仅输出 YAML,不要任何解释。不要输出真实数值。")
    return "\n".join(parts)


# ============================================================ 主流程
def propose(workbook_path: str, out_path: str | None = None) -> str | None:
    """为指定 Excel 生成 schema YAML 字符串。无 LLM 配置时返回 None + 引导。"""
    if not os.path.exists(workbook_path):
        print(f"[proposer] workbook not found: {workbook_path}", file=sys.stderr)
        return None

    client = llm_client.get_default()
    if not client.available:
        print(
            "[proposer] LLM 未配置(LLM_BASE_URL / LLM_API_KEY)。\n"
            "  (a) 在 .env 配置 LLM_BASE_URL / LLM_API_KEY 然后重跑,或\n"
            "  (b) 手写 schema(参考 schemas/三峡国际经营数据库.yaml 模板),或\n"
            "  (c) 直接用 committed schema: schemas/三峡国际经营数据库.yaml",
            file=sys.stderr,
        )
        return None

    # 采样所有 sheet
    engine = "openpyxl" if workbook_path.lower().endswith(".xlsx") else "xlrd"
    xls = pd.ExcelFile(workbook_path, engine=engine)
    samples: dict[str, str] = {}
    dims: dict[str, tuple] = {}
    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet, engine=engine, header=None, nrows=15)
        samples[sheet] = render_skeleton(df)
        dims[sheet] = df.shape

    user_prompt = _build_user_prompt(samples, dims)

    # 第 1 轮
    raw_yaml = _try_call_llm(SYSTEM_PROMPT, user_prompt)
    if not raw_yaml:
        return None

    candidate = _normalize_yaml(_clean_yaml(raw_yaml), workbook_path)
    try:
        spec = SS.workbook_from_dict(yaml.safe_load(candidate))
    except Exception as e:
        print(f"[proposer] LLM 输出解析失败: {e}", file=sys.stderr)
        return candidate  # 仍返回 raw 让用户手动调整

    errors = V.validate(spec, workbook_path)
    if errors:
        print(f"[proposer] 候选 schema 校验发现 {len(errors)} 个问题,启动修复轮...", file=sys.stderr)
        repair_user = user_prompt + "\n\n## 上轮输出校验错误:\n" + "\n".join(f"- {e}" for e in errors)
        repaired = _try_call_llm(SYSTEM_PROMPT, repair_user)
        if repaired:
            candidate = _normalize_yaml(_clean_yaml(repaired), workbook_path)
            spec = SS.workbook_from_dict(yaml.safe_load(candidate))
            errors2 = V.validate(spec, workbook_path)
            if errors2:
                print(f"[proposer] 修复后仍剩 {len(errors2)} 个错误,放弃自动修复", file=sys.stderr)
                for e in errors2:
                    print(f"  - {e}", file=sys.stderr)
            else:
                print("[proposer] 修复后通过校验", file=sys.stderr)
        else:
            print("[proposer] 修复轮调用失败", file=sys.stderr)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(candidate)
        print(f"[proposer] schema written: {out_path}")

    return candidate


def _clean_yaml(text: str) -> str:
    """剥掉 ```yaml 围栏等。"""
    t = text.strip()
    if t.startswith("```yaml"):
        t = t[7:]
    elif t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return t.strip()


def _normalize_yaml(candidate: str, workbook_path: str) -> str:
    """补全 LLM 常漏的顶层元数据(path/engine),返回规范化后的 YAML 文本。

    LLM 常把 path/engine 留空;这里按实际文件名/扩展名兜底,降低对 LLM 的要求。
    解析失败则原样返回(交由上层 workbook_from_dict 报错)。
    """
    try:
        data = yaml.safe_load(candidate)
    except Exception:
        return candidate
    if not isinstance(data, dict):
        return candidate
    if not data.get("path"):
        data["path"] = os.path.basename(workbook_path)
    if not data.get("engine"):
        data["engine"] = "openpyxl" if workbook_path.lower().endswith(".xlsx") else "xlrd"
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


# ============================================================ CLI
def main():
    parser = argparse.ArgumentParser(
        description="LLM-based Excel schema auto-discovery (offline)"
    )
    parser.add_argument("workbook", help="Path to .xls or .xlsx")
    parser.add_argument("-o", "--output", help="Output YAML path (default: stdout)")
    args = parser.parse_args()
    result = propose(args.workbook, args.output)
    if result is None:
        # propose 已在 stderr 打印原因。无配置或解析失败都返回 None。
        sys.exit(2)
    if not args.output:
        # stdout 模式:打印 yaml
        print(result)


if __name__ == "__main__":
    main()

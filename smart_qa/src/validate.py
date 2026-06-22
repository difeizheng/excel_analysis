"""schema 校验闸门:在 loader 跑之前/之后验证 schema 与实际数据匹配。

设计:收集全部失败(不短路),LLM 修复轮可一次看全问题。
调用点:
  - schema_proposer: 生成候选后必跑
  - load_grid 默认不跑(committed schema 已验证)
  - CLI --validate 或 SMART_QA_VALIDATE=1 时跑
"""
from __future__ import annotations
import os
import re
import sys
import pandas as pd
from typing import Iterable

import schema_spec as SS
import loader as L


# 公共 year/month 表头 key 正则
_KEY_RE_YEAR = re.compile(r"^\d{4}年$")
_KEY_RE_MONTH = re.compile(r"^\d{4}-\d{2}$")
_KEY_RE_OK = re.compile(r"^(\d{4}年|\d{4}-\d{2})$")


def validate(
    spec: SS.WorkbookSpec,
    wb_path: str | None = None,
    spec_path: str | None = None,
) -> list[str]:
    """返回错误列表(空表示通过)。

    wb_path 解析优先级:显式 wb_path > spec_path 推导(相对 schema 目录) > spec.path 原值。
    spec_path 用于修正相对路径 bug——当 spec.path 是相对路径时,
    应相对 schema 文件目录解析,而非进程 cwd。
    """
    errors: list[str] = []
    if wb_path is None:
        if spec_path:
            wb_path = L._resolve_workbook_path(spec_path, spec)
        else:
            wb_path = spec.path
    if not os.path.exists(wb_path):
        return [f"workbook not found: {wb_path}"]

    try:
        engine = L._pick_engine(wb_path, spec.engine)
    except ValueError as e:
        return [str(e)]

    try:
        xls = pd.ExcelFile(wb_path, engine=engine)
    except Exception as e:
        return [f"cannot open workbook: {e}"]

    sheet_names = list(xls.sheet_names)
    for sh in spec.sheets:
        errors.extend(_validate_sheet(sh, sheet_names, xls))

    return errors


def _validate_sheet(sh: SS.SheetSpec, sheet_names: list[str], xls: pd.ExcelFile) -> list[str]:
    errs: list[str] = []
    # V1: sheet 存在
    if sh.name not in sheet_names:
        errs.append(
            f"V1: sheet {sh.name!r} not found; available: {sheet_names}"
        )
        return errs  # 后续 sheet 校验依赖 sheet 存在,直接返回

    try:
        df = pd.read_excel(xls, sheet_name=sh.name, header=None)
    except Exception as e:
        errs.append(f"V?: cannot read sheet {sh.name!r}: {e}")
        return errs

    for tbl in sh.tables:
        if not tbl.enabled:
            continue
        errs.extend(_validate_table(tbl, sh.name, df))

    return errs


def _validate_table(tbl: SS.TableSpec, sheet_name: str, df: pd.DataFrame) -> list[str]:
    errs: list[str] = []
    pfx = f"[{sheet_name}/{tbl.name}]"

    # V10: header_row / first_data_row 在范围内
    if tbl.header_row >= df.shape[0]:
        errs.append(f"{pfx} V10: header_row {tbl.header_row} >= df rows {df.shape[0]}")
        return errs
    if tbl.first_data_row <= tbl.header_row:
        errs.append(f"{pfx} V10: first_data_row {tbl.first_data_row} not after header_row {tbl.header_row}")
    if tbl.first_data_row >= df.shape[0]:
        errs.append(f"{pfx} V10: first_data_row {tbl.first_data_row} >= df rows {df.shape[0]}")
        return errs

    # V9: 列 idx 全部在范围内
    indices = _all_col_indices(tbl)
    for c in indices:
        if c >= df.shape[1]:
            errs.append(f"{pfx} V9: column idx {c} out of range (df has {df.shape[1]} cols)")
    if any(c >= df.shape[1] for c in indices):
        return errs

    # V2: 表头行解析率 >= 80% (仅 data 列)
    data_cols = _data_col_indices(tbl, df.shape[1])
    if data_cols:
        parsed = sum(
            1 for c in data_cols
            if L._colkey(df.iloc[tbl.header_row, c]) is not None
        )
        ratio = parsed / len(data_cols)
        if ratio < 0.8:
            errs.append(
                f"{pfx} V2: header row {tbl.header_row}: only {parsed}/{len(data_cols)} "
                f"data columns produced a valid key"
            )

    # V3: data 列 key 形状(年/月)。允许少数"合计完成"等 YTD 总和列存在,
    #     仅当 >50% 的 data 列都不匹配年/月模式时,认为 schema 根本性错误。
    non_conforming = []
    for c in data_cols:
        k = L._colkey(df.iloc[tbl.header_row, c])
        if k is not None and not _KEY_RE_OK.match(k):
            non_conforming.append((c, k))
    if data_cols and len(non_conforming) / len(data_cols) > 0.5:
        sample = ", ".join(f"col {c}={k!r}" for c, k in non_conforming[:3])
        errs.append(
            f"{pfx} V3: >50% data columns have non year/month keys ({sample}, ...); "
            f"wrong data_col_start?"
        )

    # V4: row_map 目标,label 列文本密度 >= 0.7
    if tbl.target == "row_map" and tbl.label_col_idx is not None:
        rows_iter = _admitted_rows(df, tbl)
        if rows_iter:
            nonempty = sum(1 for r in rows_iter if L._text_at(df, r, tbl.label_col_idx))
            density = nonempty / max(len(rows_iter), 1)
            if density < 0.7:
                errs.append(
                    f"{pfx} V4: label col {tbl.label_col_idx} text density {density:.0%} < 70%"
                )

    # V5: data 列数值密度 >= 0.5
    if data_cols:
        rows_iter = _admitted_rows(df, tbl)
        if rows_iter:
            total = 0
            numeric_or_empty = 0
            for r in rows_iter:
                for c in data_cols:
                    raw = df.iloc[r, c]
                    total += 1
                    if L._num(raw) is not None or raw is None or str(raw).strip() in ("", "nan"):
                        numeric_or_empty += 1
            density = numeric_or_empty / max(total, 1)
            if density < 0.5:
                errs.append(
                    f"{pfx} V5: numeric density {density:.0%} < 50%"
                )

    # V6: row_map 无未声明重复标签。仅当 policy 不是 warn/allow 时报错
    #     (policy 显式设为 warn/allow 表示用户已知情)
    if tbl.target == "row_map" and tbl.duplicate_label_policy not in ("warn", "allow"):
        seen: dict[str, int] = {}
        for r in _admitted_rows(df, tbl):
            lbl = L._text_at(df, r, tbl.label_col_idx)
            if not lbl:
                continue
            if lbl in seen:
                errs.append(
                    f"{pfx} V6: duplicate label {lbl!r} at rows {seen[lbl]}, {r}; "
                    f"set duplicate_label_policy: warn|allow"
                )
            else:
                seen[lbl] = r

    # V7: gen_detail,marker 列至少有一行非空
    if tbl.target == "gen_detail" and tbl.detail_marker_col_idx is not None:
        rows_iter = _admitted_rows(df, tbl)
        marker_nonempty = sum(1 for r in rows_iter if L._text_at(df, r, tbl.detail_marker_col_idx))
        if marker_nonempty == 0:
            errs.append(
                f"{pfx} V7: detail marker col {tbl.detail_marker_col_idx} "
                f"empty in all data rows; detail_marker_col_idx wrong?"
            )

    # V8: gen_subtotals,marker 空但 values 非空的行必须被某个 subtotal_rule 覆盖
    if tbl.target == "gen_subtotals" and tbl.detail_marker_col_idx is not None:
        rows_iter = _admitted_rows(df, tbl)
        for r in rows_iter:
            marker = L._text_at(df, r, tbl.detail_marker_col_idx)
            if marker:
                continue
            # 该行有 values?
            has_value = any(
                L._cell(sheet_name, df.iloc[r, c], r, c) is not None
                for c in data_cols
            )
            if not has_value:
                continue
            label = L._text_at(df, r, tbl.label_col_idx)
            if not _classify_subtotal(label, tbl.subtotal_rules):
                errs.append(
                    f"{pfx} V8: subtotal-like row {r} label {label!r} "
                    f"matched no subtotal_rule; add a rule"
                )

    return errs


def _all_col_indices(tbl: SS.TableSpec) -> list[int]:
    if tbl.columns:
        return [c.idx for c in tbl.columns]
    indices = []
    if tbl.label_col_idx is not None:
        indices.append(tbl.label_col_idx)
    if tbl.detail_marker_col_idx is not None:
        indices.append(tbl.detail_marker_col_idx)
    if tbl.data_col_start is not None:
        indices.append(tbl.data_col_start)
    indices.extend(tbl.skip_cols)
    indices.extend(tbl.detail_classifier_cols.values())
    return list(set(indices))


def _data_col_indices(tbl: SS.TableSpec, df_shape1: int) -> list[int]:
    if tbl.columns:
        return [c.idx for c in tbl.columns if c.role == "data" and c.idx < df_shape1]
    if tbl.data_col_start is None:
        return []
    end = tbl.data_col_end if tbl.data_col_end is not None else df_shape1
    end = min(end, df_shape1)
    return list(range(tbl.data_col_start, end)) if tbl.data_col_start < end else []


def _admitted_rows(df: pd.DataFrame, tbl: SS.TableSpec) -> Iterable[int]:
    """返回 first_data_row..last_data_row 的索引(只过 row 范围,不做目标过滤)。"""
    last = tbl.last_data_row if tbl.last_data_row is not None else df.shape[0] - 1
    last = min(last, df.shape[0] - 1)
    return range(tbl.first_data_row, last + 1)


def _classify_subtotal(label: str, rules: list[SS.SubtotalRule]) -> str | None:
    for r in rules:
        if r.match_substring in label:
            return r.emit_key
    return None
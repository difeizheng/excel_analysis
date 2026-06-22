"""Excel 单元格地址工具：把 (sheet, 列索引, 行索引) 转成 Excel 风格地址。

列索引/行索引均为 0-based。例：col=11,row=13 -> "发电量!L14"。
与测试用例标注（L14、T5、T6、L38 等）同一套规则。
"""


def col_letter(idx: int) -> str:
    """0-based 列索引 -> Excel 列字母（0->A, 11->L, 19->T, 25->Z, 26->AA）。"""
    s = ""
    n = idx + 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def address(sheet: str, col_idx: int, row_idx: int) -> str:
    """生成物理地址，如 "发电量!L14"。row_idx 为 0-based。"""
    return f"{sheet}!{col_letter(col_idx)}{row_idx + 1}"

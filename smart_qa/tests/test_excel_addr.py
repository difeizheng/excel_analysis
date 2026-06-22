"""excel_addr.address() / col_letter() 单测。"""
import pytest
from excel_addr import address, col_letter


@pytest.mark.parametrize("idx,expected", [
    (0, "A"), (1, "B"), (10, "K"), (11, "L"), (19, "T"),
    (25, "Z"), (26, "AA"), (27, "AB"), (51, "AZ"), (52, "BA"),
])
def test_col_letter(idx, expected):
    assert col_letter(idx) == expected


@pytest.mark.parametrize("sheet,col,row,expected", [
    ("财务数据", 2, 12, "财务数据!C13"),    # 用例1 anchor
    ("装机", 19, 5, "装机!T6"),            # 用例5 可控装机 anchor
    ("发电量", 11, 13, "发电量!L14"),     # 用例6 第一个风电项目
])
def test_address(sheet, col, row, expected):
    assert address(sheet, col, row) == expected
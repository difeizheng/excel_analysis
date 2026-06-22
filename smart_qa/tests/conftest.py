"""Tests: 配置 src 路径与共用 fixture。"""
import sys
import os

# 把 src/ 加到 sys.path,让 tests 可以 import preprocess / loader / ...
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))

# 把 tests/ 加到 sys.path,让 tests 内部 helper(capture_fingerprint 等)可被 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 把项目根(smart_qa/)加到 sys.path,让 from semantic import ... 也能跑
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

# 把仓库根(excel_analysis/)加到 sys.path,让默认 XLS 相对路径解析正确
_REPO = os.path.abspath(os.path.join(_ROOT, ".."))
sys.path.insert(0, _REPO)

import pytest


@pytest.fixture(scope="session")
def workbook_path() -> str:
    """默认测试用的 Excel 文件路径(测试数据.xls,仓库根目录)。"""
    return os.path.abspath(os.path.join(_ROOT, "..", "测试数据.xls"))


@pytest.fixture(scope="session")
def grid_legacy(workbook_path):
    """当前(preprocess)加载出来的 Grid,供指纹测试对照用。"""
    import preprocess as PRE
    PRE.XLS = workbook_path
    return PRE.load_grid()


@pytest.fixture(scope="session")
def db_built():
    """构建/复用 SQLite 镜像(供 sqlite/both 后端测试)。

    走 backend.ensure_db:缺失则从 Excel 落库,已存在则直接复用。
    返回 db 绝对路径(smart_qa/data/grid.db)。
    """
    import backend as B
    return B.ensure_db()

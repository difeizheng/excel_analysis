"""task_store 单测:纯持久化层,monkeypatch TASKS_DIR/REGISTRY_PATH 隔离真实 tasks/。"""
from __future__ import annotations
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))

import task_store as TS  # noqa: E402


@pytest.fixture
def isolated_tasks(tmp_path, monkeypatch):
    """把 task_store 的 TASKS_DIR/REGISTRY_PATH 重定向到 tmp,互不影响。"""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    monkeypatch.setattr(TS, "TASKS_DIR", str(tasks_dir))
    monkeypatch.setattr(TS, "REGISTRY_PATH", str(tasks_dir / "registry.json"))
    return tasks_dir


def _make(tid_marker="t"):
    return TS.create_task(
        name=f"任务_{tid_marker}",
        excel_bytes=b"fake-excel-bytes",
        filename=f"{tid_marker}.xls",
        schema_text="# schema\npath: x.xls\n",
        semantic_texts={fn: f"# {fn}\n" for fn in TS.SEM_FILES},
    )


# ---------------- CRUD ----------------
def test_create_list_delete(isolated_tasks):
    a = _make("a")
    b = _make("b")
    tasks = TS.list_tasks()
    assert len(tasks) == 2
    # 最新在前:b 后建 → 排首位
    assert tasks[0].id == b.id
    assert tasks[1].id == a.id
    TS.delete_task(a.id)
    assert [t.id for t in TS.list_tasks()] == [b.id]
    assert TS.get_task(a.id) is None


def test_newest_and_get(isolated_tasks):
    assert TS.newest_task_id() is None
    a = _make("a")
    assert TS.newest_task_id() == a.id
    b = _make("b")
    assert TS.newest_task_id() == b.id  # 末尾追加 = 最新
    got = TS.get_task(b.id)
    assert got is not None and got.name == "任务_b"


def test_paths_absolute_and_exist(isolated_tasks):
    t = _make("a")
    for p in (t.task_dir, t.excel_path, t.schema_path, t.semantic_dir):
        assert os.path.isabs(p)
    assert os.path.isfile(t.excel_path)
    assert os.path.isfile(t.schema_path)
    assert os.path.isdir(t.semantic_dir)
    for fn in TS.SEM_FILES:
        assert os.path.isfile(os.path.join(t.semantic_dir, fn))
    # 上传文件名做 basename 防穿越
    assert t.excel_filename == "a.xls"


def test_write_task_schema_roundtrip(isolated_tasks):
    t = _make("a")
    p = TS.write_task_schema(t.id, "hello: world\n")
    assert os.path.isabs(p)
    with open(p, encoding="utf-8") as f:
        assert f.read() == "hello: world\n"
    sp = TS.write_task_semantic(t.id, "metrics.yaml", "m: 1\n")
    with open(sp, encoding="utf-8") as f:
        assert f.read() == "m: 1\n"


def test_rename_and_update_status(isolated_tasks):
    t = _make("a")
    TS.rename_task(t.id, "新名字")
    TS.update_status(t.id, "已校验")
    got = TS.get_task(t.id)
    assert got.name == "新名字"
    assert got.status == "已校验"
    # meta.json 同步
    import json
    with open(os.path.join(t.task_dir, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["name"] == "新名字" and meta["status"] == "已校验"


def test_ensure_seed_idempotent(isolated_tasks):
    # 空时种入(依赖真实 committed schema + 测试数据.xls 存在)
    seeded = TS.ensure_seed_task()
    after = TS.list_tasks()
    if seeded is not None:
        assert len(after) == 1
        assert after[0].schema_source == "seed"
    else:
        assert len(after) == 0  # 种子文件缺失时跳过
    # 再调一次:非空(或仍缺)→ 幂等,不重复种
    again = TS.ensure_seed_task()
    assert again is None
    assert len(TS.list_tasks()) == len(after)


def test_registry_corrupt_recovers(isolated_tasks):
    _make("a")
    # 写坏 registry.json
    with open(TS.REGISTRY_PATH, "w", encoding="utf-8") as f:
        f.write("{ broken json")
    # 读时容错回退空,不抛
    assert TS.list_tasks() == []
    assert TS.newest_task_id() is None

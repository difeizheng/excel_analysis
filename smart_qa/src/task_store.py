"""任务(Task)持久化存储:多任务工作区的核心模型。

一个 Task = 自包含工作单元,自带上传 Excel + schema + semantic 副本。
所有 UI 页面/首页问数都针对「当前选中任务」操作(指针在 ui_common 的 session_state)。

设计:
- 纯持久化层,不依赖 streamlit(便于单测)。
- registry.json 原子写;读时容错(损坏回退空)。
- 任务顺序 = registry 追加顺序(最新在末尾);list_tasks 倒序、newest_task_id 取末尾,
  完全确定,不受 created_at 同秒歧义影响。
- 路径全部绝对化(相对 smart_qa/),规避 Streamlit cwd 漂移。

不做的事:
- 不调用 LLM、不解析 Excel、不碰 grid。
- 不读写全局 committed schemas/semantic(仅 ensure_seed_task 复制它们作种子)。
"""
from __future__ import annotations
import os
import json
import uuid
import shutil
from dataclasses import dataclass, field
from datetime import datetime

# ---- 路径常量 ----
_HERE = os.path.dirname(os.path.abspath(__file__))      # src/
_SMART_QA = os.path.dirname(_HERE)                       # smart_qa/
_REPO = os.path.dirname(_SMART_QA)                       # excel_analysis/

TASKS_DIR = os.path.join(_SMART_QA, "tasks")
REGISTRY_PATH = os.path.join(TASKS_DIR, "registry.json")

COMMITTED_SCHEMA = os.path.join(_SMART_QA, "schemas", "三峡国际经营数据库.yaml")
COMMITTED_SEMANTIC_DIR = os.path.join(_SMART_QA, "semantic")
SEED_XLS = os.path.join(_REPO, "测试数据.xls")

SEM_FILES = ["metrics.yaml", "taxonomy.yaml", "synonyms.yaml", "rules.yaml"]

SEED_TASK_NAME = "示例·三峡国际经营数据库"


# ============================================================ Task 数据类
@dataclass
class Task:
    """单个任务元数据。路径由 id/excel_filename 派生(不入 registry)。"""
    id: str
    name: str
    created_at: str          # ISO,精度到秒(仅展示用)
    excel_filename: str
    status: str = "构建中"   # 构建中 / 已校验 / 已入库
    schema_source: str = "template"   # seed / template / llm / manual
    tags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "created_at": self.created_at,
            "excel_filename": self.excel_filename, "status": self.status,
            "schema_source": self.schema_source, "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, m: dict) -> "Task":
        return cls(
            id=m["id"], name=m.get("name", ""), created_at=m.get("created_at", ""),
            excel_filename=m.get("excel_filename", ""), status=m.get("status", "构建中"),
            schema_source=m.get("schema_source", "template"),
            tags=list(m.get("tags", [])),
        )

    # ---- 派生路径(绝对)----
    @property
    def task_dir(self) -> str:
        return _task_dir(self.id)

    @property
    def excel_path(self) -> str:
        return os.path.join(self.task_dir, "upload", self.excel_filename)

    @property
    def schema_path(self) -> str:
        return os.path.join(self.task_dir, "schema.yaml")

    @property
    def semantic_dir(self) -> str:
        return os.path.join(self.task_dir, "semantic")


# ============================================================ 路径 helper
def _task_dir(tid: str) -> str:
    return os.path.join(TASKS_DIR, tid)


def task_dir(tid: str) -> str:
    return _task_dir(tid)


def task_excel_path(tid: str, excel_filename: str) -> str:
    return os.path.join(_task_dir(tid), "upload", os.path.basename(excel_filename))


def task_schema_path(tid: str) -> str:
    return os.path.join(_task_dir(tid), "schema.yaml")


def task_semantic_dir(tid: str) -> str:
    return os.path.join(_task_dir(tid), "semantic")


def write_task_schema(tid: str, text: str) -> str:
    """把 YAML 文本写入任务的 schema.yaml,返回绝对路径。"""
    p = task_schema_path(tid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def write_task_semantic(tid: str, name: str, text: str) -> str:
    """把单个 semantic YAML 文本写入任务 semantic 目录,返回绝对路径。"""
    d = task_semantic_dir(tid)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


# ============================================================ registry 读写
def _ensure_tasks_dir() -> None:
    os.makedirs(TASKS_DIR, exist_ok=True)


def _load_registry() -> dict:
    """读 registry;缺失/损坏回退空(容错,不抛)。"""
    if not os.path.exists(REGISTRY_PATH):
        return {"version": 1, "tasks": []}
    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "tasks" not in data:
            return {"version": 1, "tasks": []}
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "tasks": []}


def _save_registry(data: dict) -> None:
    """原子写:tmp → os.replace。"""
    _ensure_tasks_dir()
    tmp = REGISTRY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, REGISTRY_PATH)


def _new_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_meta_json(task: Task) -> None:
    _ensure_tasks_dir()
    with open(os.path.join(task.task_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)


# ============================================================ CRUD
def list_tasks() -> list[Task]:
    """所有任务,最新在前(registry 追加顺序倒序,确定无歧义)。"""
    reg = _load_registry()
    return [Task.from_dict(m) for m in reversed(reg["tasks"])]


def get_task(tid: str) -> Task | None:
    reg = _load_registry()
    for m in reg["tasks"]:
        if m["id"] == tid:
            return Task.from_dict(m)
    return None


def newest_task_id() -> str | None:
    """最新创建的任务 id(= registry 末尾)。无任务返回 None。"""
    reg = _load_registry()
    if not reg["tasks"]:
        return None
    return reg["tasks"][-1]["id"]


def create_task(
    name: str,
    excel_bytes: bytes,
    filename: str,
    *,
    schema_text: str | None = None,
    schema_source: str = "template",
    semantic_texts: dict | None = None,
    status: str = "构建中",
    tags: list | None = None,
) -> Task:
    """创建一个自包含任务:落盘 excel + schema + semantic,登记 registry,返回 Task。

    schema_text/semantic_texts 为 None 时,从 committed 复制作默认(三峡通用口径)。
    filename 取 basename 防路径穿越。
    """
    _ensure_tasks_dir()
    tid = _new_id()
    safe_filename = os.path.basename(filename)
    tdir = _task_dir(tid)
    os.makedirs(os.path.join(tdir, "upload"), exist_ok=True)
    os.makedirs(os.path.join(tdir, "semantic"), exist_ok=True)

    # excel
    excel_path = os.path.join(tdir, "upload", safe_filename)
    with open(excel_path, "wb") as f:
        f.write(excel_bytes)

    # schema
    schema_path = os.path.join(tdir, "schema.yaml")
    if schema_text is not None:
        with open(schema_path, "w", encoding="utf-8") as f:
            f.write(schema_text)
    elif os.path.exists(COMMITTED_SCHEMA):
        shutil.copyfile(COMMITTED_SCHEMA, schema_path)
    else:
        with open(schema_path, "w", encoding="utf-8") as f:
            f.write("# 手写 schema\n")

    # semantic(4 文件)
    sdir = os.path.join(tdir, "semantic")
    if semantic_texts:
        for fn, txt in semantic_texts.items():
            with open(os.path.join(sdir, fn), "w", encoding="utf-8") as f:
                f.write(txt)
    else:
        for fn in SEM_FILES:
            src = os.path.join(COMMITTED_SEMANTIC_DIR, fn)
            if os.path.exists(src):
                shutil.copyfile(src, os.path.join(sdir, fn))
            else:
                with open(os.path.join(sdir, fn), "w", encoding="utf-8") as f:
                    f.write("")

    task = Task(
        id=tid, name=(name or safe_filename), created_at=_now_iso(),
        excel_filename=safe_filename, status=status,
        schema_source=schema_source, tags=list(tags or []),
    )
    _write_meta_json(task)

    reg = _load_registry()
    reg["tasks"].append(task.to_dict())
    _save_registry(reg)
    return task


def delete_task(tid: str) -> None:
    """删 registry 条目 + 任务目录。"""
    reg = _load_registry()
    reg["tasks"] = [m for m in reg["tasks"] if m["id"] != tid]
    _save_registry(reg)
    tdir = _task_dir(tid)
    if os.path.isdir(tdir):
        shutil.rmtree(tdir, ignore_errors=True)


def rename_task(tid: str, name: str) -> None:
    _mutate(tid, name=name)


def update_status(tid: str, status: str) -> None:
    _mutate(tid, status=status)


def set_schema_source(tid: str, source: str) -> None:
    """标注任务 schema 来源(seed/template/llm/manual)。"""
    _mutate(tid, schema_source=source)


def _mutate(tid: str, **changes) -> None:
    reg = _load_registry()
    changed = False
    for m in reg["tasks"]:
        if m["id"] == tid:
            m.update({k: v for k, v in changes.items() if v is not None})
            changed = True
            snapshot = dict(m)
            break
    if changed:
        _save_registry(reg)
        # 同步 meta.json
        try:
            task = Task.from_dict(snapshot)
            _write_meta_json(task)
        except Exception:
            pass


# ============================================================ 引导
def ensure_seed_task() -> Task | None:
    """registry 为空时,从 committed 文件 + 测试数据.xls 种入示例任务。

    缺种子文件(committed schema/测试数据.xls)则跳过,返回 None(不抛)。
    非空时幂等:返回 None(不重复种)。
    """
    if list_tasks():
        return None
    if not (os.path.exists(COMMITTED_SCHEMA) and os.path.exists(SEED_XLS)):
        return None
    with open(SEED_XLS, "rb") as f:
        excel_bytes = f.read()
    return create_task(
        name=SEED_TASK_NAME,
        excel_bytes=excel_bytes,
        filename="测试数据.xls",
        schema_source="seed",
        status="已校验",
        tags=["示例"],
    )

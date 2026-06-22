"""问题语料库持久化:把接地问题(target + 问法)按任务落盘,跨 grid 版本累积。

设计灵魂(闭环成立的前提):
- **永不落盘 expected/actual 数字**。只存 QuestionTarget 的结构化参数 + 问法。
  expected 每次用【当前 grid】现场重算(见 question_generator.re_derive / corpus_run)。
  若冻数字,改了语义口径后会全盘假回归——闭环就废了。
- 同一意图(target 签名相同)只建一条 entry,问法按 text 去重累积(phrasings 列表)。
  这样同一业务问题可积累模板句 + 多轮 LLM 自然句,既给闸门确定性基线,又给回归页覆盖面。

纯持久化层:不依赖 streamlit/pandas/qa。镜像 task_store 的原子写、读时容错风格。
"""
from __future__ import annotations
import os
import json
import hashlib
from dataclasses import dataclass, field

import task_store as TS


# ============================================================ CorpusEntry
@dataclass
class CorpusEntry:
    """一条语料 = 一个查询意图 + 它的若干问法。不含任何答案数字。"""
    id: str
    op: str                                  # 题型(=重算分发键)
    category: str                            # verifiable | blindspot
    target: dict                             # QuestionTarget 去 expected 后的序列化
    phrasings: list = field(default_factory=list)   # [{text, src}]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "op": self.op, "category": self.category,
            "target": self.target, "phrasings": list(self.phrasings),
        }

    @classmethod
    def from_dict(cls, m: dict) -> "CorpusEntry":
        return cls(
            id=m.get("id", ""), op=m.get("op", ""),
            category=m.get("category", "verifiable"),
            target=m.get("target") or {},
            phrasings=list(m.get("phrasings") or []),
        )


# ============================================================ 路径
def task_corpus_path(tid: str) -> str:
    return os.path.join(TS.task_dir(tid), "corpus", "questions.yaml")


# ============================================================ 签名(同意图去重键,纯 dict 计算)
def _signature(target: dict) -> str:
    """从 target dict 算确定性签名:op|metric|metrics|time_tokens|extras。"""
    key = {
        "op": target.get("operation", ""),
        "metric": target.get("metric", ""),
        "metrics": list(target.get("metrics") or []),
        "time_tokens": list(target.get("time_tokens") or []),
        "extras": dict(target.get("extras") or {}),
    }
    return json.dumps(key, sort_keys=True, ensure_ascii=False)


def _entry_id(sig: str) -> str:
    return "q_" + hashlib.md5(sig.encode("utf-8")).hexdigest()[:8]


# ============================================================ 读写
def load_corpus(tid: str) -> list[CorpusEntry]:
    """读任务语料;缺失/损坏回退 [](容错,不抛)。"""
    p = task_corpus_path(tid)
    if not os.path.exists(p):
        return []
    try:
        import yaml
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, Exception):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for m in data:
        if isinstance(m, dict) and m.get("target"):
            out.append(CorpusEntry.from_dict(m))
    return out


def save_corpus(tid: str, entries: list[CorpusEntry]) -> str:
    """原子写(tmp → os.replace),返回绝对路径。目录懒建。"""
    p = task_corpus_path(tid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    import yaml
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            [e.to_dict() for e in entries], f,
            allow_unicode=True, sort_keys=False, default_flow_style=False,
        )
    os.replace(tmp, p)
    return p


# ============================================================ 增量合并(存入语料库入口)
def upsert_phrasings(tid: str, items: list[tuple]) -> int:
    """把 [(target_dict, phrasing_text, src), ...] 合并入任务语料。

    - 按 _signature(target_dict) 找/建 entry(同意图不重复建)。
    - 每个 entry 的 phrasings 按 text 去重累积(保序,first-wins 的 src)。
    - category/op 从 target_dict 取(operation/category)。
    返回新增的 phrasing 条数(去重后实际写入的)。
    """
    entries = load_corpus(tid)
    by_sig: dict[str, CorpusEntry] = {_signature(e.target): e for e in entries}
    added = 0
    for target_dict, text, src in items:
        text = (text or "").strip()
        if not text:
            continue
        sig = _signature(target_dict)
        e = by_sig.get(sig)
        if e is None:
            e = CorpusEntry(
                id=_entry_id(sig),
                op=target_dict.get("operation", ""),
                category=target_dict.get("category", "verifiable"),
                target=target_dict,
                phrasings=[],
            )
            by_sig[sig] = e
        if not any(p.get("text") == text for p in e.phrasings):
            e.phrasings.append({"text": text, "src": src})
            added += 1
    save_corpus(tid, list(by_sig.values()))
    return added


def remove_entry(tid: str, entry_id: str) -> bool:
    """删一条 entry(语料回归页/管理用)。返回是否删到。"""
    entries = load_corpus(tid)
    left = [e for e in entries if e.id != entry_id]
    if len(left) == len(entries):
        return False
    save_corpus(tid, left)
    return True

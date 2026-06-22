"""语义层：加载 YAML 规则库，提供指标/实体/分类/规则的解析能力。

这是 LLM 意图解析与确定性引擎之间的"契约层"。
所有领域知识集中在此，可版本化、可评审——这是防幻觉的核心。
"""
from __future__ import annotations
import os
import yaml

SEM_DIR = os.path.join(os.path.dirname(__file__), "..", "semantic")


def _load(name: str):
    with open(os.path.join(SEM_DIR, name), encoding="utf-8") as f:
        return yaml.safe_load(f)


METRICS = _load("metrics.yaml")
TAXONOMY = _load("taxonomy.yaml")
SYNONYMS = _load("synonyms.yaml")
RULES = _load("rules.yaml")


# ---- 别名表构建（口语 -> 标准指标/实体），长别名优先 ----
def _build_alias_map():
    amap: dict[str, str] = {}
    for canon, info in METRICS.items():
        amap[canon] = canon
        for syn in (info or {}).get("synonyms", []) or []:
            amap[syn] = canon
    for k, v in (SYNONYMS.get("metric_aliases") or {}).items():
        amap[k] = v
    # 按长度降序，优先匹配长词（如"利润总额"优先于"利润"）
    return dict(sorted(amap.items(), key=lambda kv: len(kv[0]), reverse=True))


ALIAS_MAP = _build_alias_map()


def _build_entity_map():
    emap: dict[str, str] = {}
    for canon, info in (SYNONYMS.get("entities") or {}).items():
        emap[canon] = canon
        for a in (info or {}).get("aliases", []) or []:
            emap[a] = canon
    return emap


ENTITY_MAP = _build_entity_map()


def reload(sem_dir: str | None = None) -> None:
    """重新加载 4 个 semantic YAML 并重建别名/实体映射(供工作台热重载)。

    注意:已 ``from semantic_layer import METRICS`` 的旧引用不会更新;
    调用方应改用 ``import semantic_layer as S; S.METRICS``。
    """
    global SEM_DIR, METRICS, TAXONOMY, SYNONYMS, RULES, ALIAS_MAP, ENTITY_MAP
    if sem_dir:
        SEM_DIR = os.path.abspath(sem_dir)
    METRICS = _load("metrics.yaml")
    TAXONOMY = _load("taxonomy.yaml")
    SYNONYMS = _load("synonyms.yaml")
    RULES = _load("rules.yaml")
    ALIAS_MAP = _build_alias_map()
    ENTITY_MAP = _build_entity_map()


# ---------------------------------------------------------------- 解析接口
def resolve_metric(text: str) -> str | None:
    """从文本中识别标准指标名（最长匹配）。"""
    for alias, canon in ALIAS_MAP.items():
        if alias and alias in text:
            return canon
    return None


def resolve_metrics(text: str) -> list[str]:
    """识别文本中的多个指标（按顿号/逗号切分后逐段识别）。"""
    found: list[str] = []
    seen = set()
    for seg in text.replace("、", ",").replace("和", ",").split(","):
        seg = seg.strip()
        if not seg:
            continue
        m = resolve_metric(seg) or resolve_metric(text)  # 段内无则退回全文
        if m and m not in seen:
            found.append(m)
            seen.add(m)
    return found


def resolve_entity(text: str) -> str:
    """实体消歧：返回 '三峡国际' / '集团'。默认三峡国际(R4)。"""
    for alias, canon in ENTITY_MAP.items():
        if alias and alias in text:
            return canon
    return "三峡国际"  # 默认口径


def metric_info(metric: str) -> dict:
    return METRICS.get(metric, {})


def metric_unit(metric: str) -> str:
    return (METRICS.get(metric) or {}).get("unit", "")


def is_taxonomy_node(metric: str) -> bool:
    return bool((METRICS.get(metric) or {}).get("taxonomy_node"))


def expand_taxonomy(metric: str) -> list[str]:
    """分类节点 -> 子类列表。如 风电发电量 -> [陆上风电, 海上风电]。"""
    info = METRICS.get(metric) or {}
    node = info.get("taxonomy_node")
    if not node:
        return []
    tree = (TAXONOMY.get("发电方式") or {}).get(node) or {}
    return tree.get("includes", [])


def region_metrics() -> list[str]:
    """发电量区域小计指标(巴西/南亚/欧洲/拉美发电量)。

    纯声明(只读 metrics.yaml,不读 grid):is_subtotal 且 sheet=发电量。
    与 question_generator._region_metrics 同谓词 → rank 取数集合一致。
    数据可用性由 pipeline 的 be.lookup 过滤。
    """
    out: list[str] = []
    for m, info in METRICS.items():
        info = info or {}
        if info.get("is_subtotal") and (info.get("locator") or {}).get("sheet") == "发电量":
            out.append(m)
    return out

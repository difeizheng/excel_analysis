# -*- coding: utf-8 -*-
"""把 6 个真实问题解析成结构化意图 JSON, 直观看 4 个空怎么填。"""
import sys
import os
import json

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
import parser as P

QUESTIONS = [
    "公司2018年的利润总额是多少？",
    "三峡国际2022、2024、2025年每年的汇兑净损失是多少？",
    "24年-26年2月累计向集团分红多少？",
    "公司近三年的利润增长率是多少？",
    "三峡国际2025年的总装机、可控装机、利润总额、发电量是多少？",
    "公司2025年风电发电量是多少",
]

for i, q in enumerate(QUESTIONS, 1):
    it = P.parse(q)
    obj = {
        "question": it.question,
        "entity": it.entity,
        "metric": it.metric,
        "metrics": it.metrics,
        "time_tokens": it.time_tokens,
        "operation": it.operation,
        "notes": it.notes,
    }
    print(f"\n{'=' * 68}\n用例{i}: {q}\n{'=' * 68}")
    print(json.dumps(obj, ensure_ascii=False, indent=2))

"""回归测试:跑 40 个用例 + 评分报告。

直接调 golden_cases.score_all() 跑全部,然后:
- 数值准确率(应有 expected value 的用例)
- 溯源完整率(应含 expected addr 的用例)
- 拒绝正确率(负例)
- 单元校验通过率
"""
from __future__ import annotations
import os
import sys
import json
import pytest

# 修 Windows GBK console 编码问题(打印特殊字符 ✗ / ✓)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, ".."))

import golden_cases as GC  # noqa: E402


@pytest.fixture(scope="module")
def grid():
    import loader
    return loader.load_grid()


@pytest.fixture(scope="module")
def report(grid):
    return GC.score_all(GC.CASES, grid, use_llm=False)


# ---------------- 关键指标阈值 ----------------
PASS_RATE_MIN = 0.85       # 至少 85% 用例通过
NEG_RATE_MIN = 0.6         # 负例拒绝率 >= 60%
PROVENANCE_MIN = 0.9       # 溯源完整 >= 90%


def test_total_case_count():
    assert len(GC.CASES) >= 35, f"用例数 {len(GC.CASES)} < 35"


def test_categories_covered():
    cats = {c["category"] for c in GC.CASES}
    required = {"lookup", "multi_year", "sum_ytd", "cagr", "multi_metric",
                "taxonomy", "region", "neg"}
    missing = required - cats
    assert not missing, f"缺少分类: {missing}"


class TestReport:
    def test_overall_pass_rate(self, report):
        assert report["pass_rate"] >= PASS_RATE_MIN, \
            f"通过率 {report['pass_rate']:.1%} < {PASS_RATE_MIN:.0%}: {report['by_category']}"

    def test_neg_case_refusal_rate(self, report):
        assert report["neg_rate"] >= NEG_RATE_MIN, \
            f"负例拒绝率 {report['neg_rate']:.1%} < {NEG_RATE_MIN:.0%}"

    def test_provenance_completeness(self, report):
        """每个有 addr_contains 的用例溯源必须命中。"""
        n = 0
        hit = 0
        for r in report["results"]:
            cid = r["id"]
            case = next(c for c in GC.CASES if c["id"] == cid)
            if "addr_contains" not in case["expect"]:
                continue
            n += 1
            if not r["reasons"] or not any("溯源缺失" in x for x in r["reasons"]):
                hit += 1
        rate = hit / n if n else 1.0
        assert rate >= PROVENANCE_MIN, \
            f"溯源完整率 {rate:.1%} < {PROVENANCE_MIN:.0%} ({hit}/{n})"


class TestPerCategory:
    """按类别看,通过率应有底线(允许部分负例未拒)。"""

    @pytest.mark.parametrize("category,min_pass", [
        ("lookup", 0.7),         # 单点取数应最稳
        ("sum_ytd", 0.7),        # 累计求和
        ("cagr", 0.5),           # CAGR 至少 1/2 通过
        ("multi_metric", 0.5),
        ("taxonomy", 0.5),
        ("region", 0.5),
    ])
    def test_category_min(self, report, category, min_pass):
        cats_results = [r for r in report["results"] if r["category"] == category]
        if not cats_results:
            pytest.skip(f"无 {category} 用例")
        n = len(cats_results)
        ok = sum(1 for r in cats_results if r["ok"])
        rate = ok / n
        assert rate >= min_pass, \
            f"{category} 通过率 {rate:.1%} ({ok}/{n}) < {min_pass:.0%}\n" \
            f"失败用例:\n" + "\n".join(
                f"  #{r['id']} {r['question'][:40]}: {r['reasons']}"
                for r in cats_results if not r["ok"]
            )


def test_print_report(report, capfd):
    """完整报告打印(可在 pytest -s 看到),不参与评分。"""
    with capfd.disabled():
        print("\n" + "=" * 70)
        print("Phase 4 回归测试集报告 (40 用例)")
        print("=" * 70)
        print(f"总通过率:  {report['pass']}/{report['total']} = {report['pass_rate']:.1%}")
        print(f"负例拒绝:  {report['neg_correct']}/{report['neg_total']} = {report['neg_rate']:.1%}")
        print(f"分类: {report['by_category']}")
        print("-" * 70)
        failed = [r for r in report["results"] if not r["ok"]]
        if failed:
            print(f"FAILED cases ({len(failed)}):")
            for r in failed:
                print(f"  #{r['id']:>2} [{r['category']:>12}] {r['question'][:50]}")
                for reason in r["reasons"]:
                    print(f"     X {reason}")
        else:
            print("[OK] all pass")
        print("=" * 70)
    # 把报告也写到 tests/_last_report.json 便于人工查看
    out_path = os.path.join(_HERE, "_last_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "total": report["total"],
            "pass": report["pass"],
            "pass_rate": report["pass_rate"],
            "neg_rate": report["neg_rate"],
            "by_category": report["by_category"],
            "failed_ids": [r["id"] for r in report["results"] if not r["ok"]],
        }, f, ensure_ascii=False, indent=2)
    assert True  # 打印不影响 pass/fail


# ---------------- 三后端全量验收(SQLite 接入查询链的终极保证)----------------
@pytest.mark.parametrize("backend", ["memory", "sqlite", "both"])
def test_all_backends_pass(grid, backend):
    """40 用例在 memory / sqlite / both 三后端下全过。

    both 模式额外要求双引擎互验(C5)一致 —— 即 SQLite 取数与内存引擎
    产出 bit-identical 的 value + addr 集合。
    """
    report = GC.score_all(GC.CASES, grid, use_llm=False, backend=backend)
    failed = [r for r in report["results"] if not r["ok"]]
    assert not failed, f"[{backend}] 失败用例:\n" + "\n".join(
        f"  #{r['id']} {r['question'][:40]}: {r['reasons']}" for r in failed
    )

# 三峡国际「智能问数」MVP

对 `测试数据.xls` 进行**准确、可追溯、零幻觉**的自然语言数据查询。

## 快速开始

```bash
cd smart_qa
python run.py        # 跑通 6 个标准测试用例，输出答案+溯源+校验
```

预期结果：**6/6 用例数值正确，100% 答案附单元格地址**。

单问示例：
```python
import sys; sys.path.insert(0, "src")
import preprocess as PRE, qa
grid = PRE.load_grid()
ans = qa.ask(grid, "公司2025年风电发电量是多少")
print(ans["text"])
```

## 目录结构

```
smart_qa/
├── run.py                      # 入口：运行 6 个黄金用例
├── docs/
│   └── 技术方案文档_智能问数系统.md   # 完整 SRS/架构文档
├── semantic/                   # 语义层规则库(防幻觉核心)
│   ├── metrics.yaml            #   指标字典
│   ├── taxonomy.yaml           #   分类树(风电=陆上+海上)
│   ├── synonyms.yaml           #   同义词 + 实体消歧
│   └── rules.yaml              #   业务规则(YTD/近三年/CAGR)
├── schemas/                    # ★ Excel 结构 schema(新增)
│   └── 三峡国际经营数据库.yaml      #   描述三个 Sheet 的结构
├── src/
│   ├── excel_addr.py           #   单元格地址工具(契约锚点)
│   ├── preprocess.py           #   入口: 委托 loader.load_grid()
│   ├── loader.py               #   ★ 通用 Excel 加载器(schema 驱动)
│   ├── schema_spec.py          #   ★ SchemaSpec 数据类 + YAML 加载
│   ├── validate.py             #   ★ Schema 校验闸门(V1-V10)
│   ├── schema_proposer.py      #   ★ 离线 LLM schema 自动发现
│   ├── semantic_layer.py       #   加载 YAML,提供解析接口
│   ├── parser.py               #   NL → 意图(规则解析器,可换 LLM)
│   ├── engine.py               #   确定性运算(带溯源)
│   ├── pipeline.py             #   规划 + 执行分发(应用业务规则)
│   └── qa.py                   #   校验 + 格式化 + 顶层 ask()
└── tests/                      # ★ pytest 指纹回归 + 单元测试
    ├── golden/                 #   golden snapshot(committed)
    ├── capture_fingerprint.py  #   一次性捕获脚本(重构前先跑)
    ├── test_fingerprint.py     #   ★ 核心回归: 逐格 bit-identical
    ├── test_excel_addr.py      #   地址边界 0/A/25/Z/26/AA
    ├── test_colkey.py          #   表头归一化
    └── test_validate.py        #   校验规则
```

## 六层架构（对应源码）

| 层 | 模块 | 职责 |
|---|---|---|
| ① 预处理 | preprocess.py | Excel → 带地址的语义网格 |
| ② 语义层 | semantic/*.yaml | 指标/分类/同义词/规则（领域知识固化） |
| ③ 意图解析 | parser.py | NL → 结构化 Intent（**LLM 唯一介入处**，MVP 用规则解析器） |
| ④ 规划 | pipeline.py | 意图 → 单元格坐标 + 运算（应用 YTD/近三年/分类规则） |
| ⑤ 执行 | engine.py | 确定性取数/求和/CAGR（**LLM 碰不到**） |
| ⑥ 溯源 | qa.py | 答案 + 计算链 + 单元格地址 |
| ⑦ 校验 | qa.py | 独立重算复核 |

## 三条铁律（如何保证零幻觉）

1. **LLM 不算数** —— 只出意图 JSON，运算全在 engine.py
2. **接地或拒绝** —— 映射不到单元格则返回"无法定位"，不编造
3. **单元格地址即发票** —— 每个数值都可溯源到 `Sheet!Cell`（如 `发电量!L14`）

## 6 个用例验证结果

| # | 问题 | 结果 | 关键规则 |
|---|---|---|---|
| 1 | 2018 利润总额 | 6.50 亿 | 实体消歧(公司→三峡国际) |
| 2 | 2022/24/25 汇兑净损失 | 5300/6300/6800 | 非连续多年 |
| 3 | 24-26年2月累计分红 | 1543 亿 | **月度 YTD**（取2月列，不逐月相加） |
| 4 | 近三年利润增长率 | 5.57% | **CAGR + 近三年期初取2022底** |
| 5 | 总装机/可控/利润/发电量 | 2104.54/1294.17/10/384.40 | 跨表+单位保留 |
| 6 | 风电发电量 | 39.91 亿千瓦时 | **分类归并**(陆上+海上) |

> 用例 6 的溯源链 `L14+L16+L17+L18+L20+L24+L27+L29` 与测试文档标注**逐字一致**。
> 用例 5 可控装机：系统读真实单元格 `装机!T6 = 1294.17`，与测试文档标注 1284.17 差 10，系统以源数据为准并显式标注差异。

## Phase 2/3/4 进度

| Phase | 内容 | 状态 |
|---|---|---|
| **2** | LLM 意图解析器 (`llm_parser.py`，OpenAI 兼容 + strict JSON schema + 规则回退) | ✅ 36 单测 |
| **2** | 单位强类型 + C1-C4 四项校验 (重算/单元格回查/单位/小计) | ✅ 18 单测 |
| **3** | Schema 驱动的预处理 (`schemas/三峡国际经营数据库.yaml` + `loader.py`) | ✅ 11 等价性测试 |
| **3** | SQLite 镜像 (`to_sqlite.py`，1025 cells) | ✅ 8 等价性测试 |
| **3** | Streamlit Web 前端 + 多轮对话 (`app.py`) | ✅ 跑通 |
| **4** | 40 用例回归集 + 评分脚本 | ✅ **40/40 100%** |
| **5** | SQLite 接入查询链 + 双引擎互验 (`backend.py`，memory/sqlite/both 三后端) | ✅ **40×3 全过** |
| **总** | 单元 + 回归 + LLM(需 key) 测试 | **153 passed, 6 skipped** |

## 接入真实 LLM

把凭据填进 `.env`(`cp .env.example .env`):

```bash
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4   # 智谱/通义/DeepSeek/Ollama 都可
LLM_API_KEY=glm-xxxx
LLM_MODEL=glm-4-flash
```

启动 Streamlit:
```bash
streamlit run app.py
# 侧栏可切换 "使用 LLM 解析",看到 LLM 状态条变化
```

跑活体 LLM 测试(需 key):
```bash
pytest tests/test_llm_live.py -v
```

## 接入真实 LLM（生产化）

`parser.py` 的 `parse()` 是唯一需要替换的函数。生产环境实现 `LLMParser`：
- 输入：问题 + 语义层 schema（metrics/taxonomy/rules 摘要）
- 输出：同结构 Intent JSON（structured output / tool-use 强约束）
- 其余 ①②④⑤⑥⑦ 全部不变 → 架构对 LLM 升级免疫

## Schema 驱动的 Excel 预处理（通用化）

`schemas/三峡国际经营数据库.yaml` 把 Excel 的"长相"也变成 YAML 规则，让预处理层与具体文件解耦。**新增文件只需一份 schema，不用改代码。**

### 接入新 Excel 的两种方式

**方式 A：手写 schema**（参考 `schemas/三峡国际经营数据库.yaml` 模板）

关键字段：`header_row`、`label_col_idx`、`data_col_start`/`data_col_end`、
`target`（row_map/gen_detail/gen_subtotals）、`subtotal_rules`、`detail_marker_col_idx` 等。
手写后跑 `validate.py` 确认结构匹配。

**方式 B：LLM 自动提议**（可选）

```bash
export ANTHROPIC_API_KEY=sk-...   # 或设 SMART_QA_PROPOSER_MODEL
python src/schema_proposer.py ../新数据.xlsx -o schemas/新数据.yaml
```

提议器只看类型骨架（`<number>/<text>/<date>`），**绝不暴露真实数值**；
输出经 `validate.py` 校验闸门，失败自动 1 轮修复。
无 API key 时打印引导并退出，不崩溃。

### 回归安全网

```bash
# 一次性捕获当前 Grid 快照（重构前必须先跑）
python tests/capture_fingerprint.py

# 验证任何 loader 改动都保持逐格 bit-identical
pytest tests/test_fingerprint.py -v
```

golden snapshot 是 **engine-pinned**——换文件格式必须重生成，任何地址漂移当场暴露。

## 已知限制（当前阶段）

- 解析器为规则型，覆盖 6 类问题模式；泛化到任意问题需接 LLM
- LLM 解析需配置 `.env`(`LLM_API_KEY`)；未配置时自动回退规则解析
- Streamlit 前端尚未暴露 backend 切换(默认 memory；CLI/SDK 可用 `backend="sqlite"/"both"` 走双引擎互验)

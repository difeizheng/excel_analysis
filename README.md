# 经营数据库 · 智能问数 (`smart_qa`)

Schema 驱动的 Excel 经营报表问答系统:**确定性抽取 + 双引擎互验 + 单元格级溯源 + 语料回归闸门**。

- 引擎只看 Grid 的真实单元格,**LLM 不碰数字**;每个答案附单元格地址,可点回溯
- 同 SQL 谓词可重现(memory + SQLite 双引擎交叉验证,C5)
- Schema / 语义层支持可视化编辑与 LLM 一键生成,**闸门守护**避免误改

## 应用

主应用、代码、测试均在 [`smart_qa/`](./smart_qa/)。

## 快速开始

```bash
cd smart_qa
pip install -r requirements.txt

# (可选)配置 LLM,用于语义层自动提议 + LLM 解析:
# cp .env.example .env  后填 LLM_BASE_URL / LLM_API_KEY

streamlit run app.py
```

上传你的 Excel(任务自包含,副本可逆)→ 在「数据接入」初始化 schema → 「语义层」使用引导式编辑或一键 LLM 生成 → 问数台问句。

## 数据说明

**本仓库不含任何业务数据**。种子 Excel `测试数据.xls`、运行时 SQLite `smart_qa/data/`、每任务上传/语料 `smart_qa/tasks/` 已被 `.gitignore` 排除。运行前请通过「数据接入」页上传你自己的 Excel。

## 验证

```bash
cd smart_qa
python -X utf8 -m pytest -q --ignore=tests/test_llm_live.py   # 需要提供数据
python -X utf8 run.py                                        # 6/6 golden,需要数据
```

## 架构

- 七层架构(预处理 / 语义 / 意图 / 规划 / 执行 / 溯源 / 校验)+ 横切层(单位类型 / 持久化 / 前端)
- 后端双引擎互验(`Memory` + `Sqlite`)确保答案可由两条独立代码路径重现
- 工作台 6 页(数据接入 / Schema 编辑 / 语义层 / Grid 检视 / 问题生成 / 语料回归)

详细架构与设计见 `smart_qa/docs/技术方案文档_智能问数系统.md`。
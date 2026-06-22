# -*- coding: utf-8 -*-
"""单问题全链路追踪: 把一个问题拆到七层, 逐层打印真实中间产物。只读。"""
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import preprocess as PRE
import parser as P
import semantic_layer as S
import pipeline as PL
import backend as B
import engine as E
import qa

QUESTION = "公司2018年的利润总额是多少？"
W = 74


def hdr(t):
    print("\n" + "=" * W + f"\n {t}\n" + "=" * W)


print("#" * W)
print(f" 追踪问题: {QUESTION}")
print("#" * W)

# ① 预处理
hdr("① 预处理层  preprocess.load_grid() -> Grid")
grid = PRE.load_grid()
row = grid.fin["利润总额"]
c2018 = row["2018年"]
print(f"  Grid.fin['利润总额']['2018年'] ->")
print(f"    Cell(value={c2018.value}, addr={c2018.addr!r}, numeric={c2018.numeric},")
print(f"         row_idx={c2018.row_idx}, col_idx={c2018.col_idx})")
print("  (Grid 是离线一次性产物; 查询时直接复用, 不重读 Excel)")

# ② 语义层
hdr("② 语义层  semantic_layer  (YAML 规则库)")
ent = S.resolve_entity(QUESTION)
met = S.resolve_metric(QUESTION)
info = S.metric_info(met)
print(f"  resolve_entity({QUESTION!r})")
print(f"    -> {ent!r}     规则: synonyms.yaml 里 '公司' ∈ 三峡国际.aliases")
print(f"  resolve_metric({QUESTION!r})")
print(f"    -> {met!r}     规则: ALIAS_MAP 长词优先, '利润总额'(4字) 胜过 '利润'(2字)")
print(f"  metric_info({met!r}) ->")
print(f"    locator        = {info.get('locator')}")
print(f"    unit           = {info.get('unit')!r}")
print(f"    default_entity = {info.get('default_entity')!r}")

# ③ 意图解析
hdr("③ 意图解析层  parser.parse() -> Intent  (规则解析器, 无 LLM)")
intent = P.parse(QUESTION)
print(f"  Intent(")
print(f"    question    = {intent.question!r}")
print(f"    entity      = {intent.entity!r}")
print(f"    metric      = {intent.metric!r}")
print(f"    metrics     = {intent.metrics}")
print(f"    time_tokens = {intent.time_tokens}     # ('year', 2018)")
print(f"    operation   = {intent.operation!r}     # 单指标+单时间+非分类节点 => lookup")
print(f"    notes       = {intent.notes}")
print("  )")

# ④ 规划 + 取数后端
hdr("④ 规划层  pipeline._exec_lookup -> backend.lookup -> CellView")
be = B.make_backend(grid, "memory")
unit = S.metric_unit(intent.metric)
ck = PL._colkey(intent.time_tokens[0])
print(f"  operation={intent.operation!r} => _exec_lookup 分支")
print(f"  col_key   = _colkey({intent.time_tokens[0]}) -> {ck!r}")
print(f"  unit      = metric_unit({intent.metric!r}) -> {unit!r}")
print(f"  backend.lookup(metric={intent.metric!r}, entity={intent.entity!r}, col_key={ck!r})")
print(f"    内部: locator.sheet=财务数据 => _match_row_struct(grid.fin, '利润总额') => 取到该行")
print(f"          row.get('2018年') => 命中数值 Cell")
pair = be.lookup(intent.metric, intent.entity, ck)
cv, label = pair
print(f"    -> CellView(addr={cv.addr!r}, value={cv.value}, numeric={cv.numeric})")
print(f"    label = {label!r}")

# ⑤ 执行
hdr("⑤ 执行层  engine.lookup() -> Result  (确定性, LLM 碰不到)")
res = E.lookup(cv, label, unit)
op = res.operands[0]
print(f"  Result(")
print(f"    value     = {res.value}")
print(f"    unit      = {res.unit!r}")
print(f"    operation = {res.operation!r}")
print(f"    operands  = [Operand(addr={op.addr!r}, value={op.value}, unit={op.unit!r})]")
print(f"    formula   = {res.formula!r}")
print("  )")

# ⑥⑦ 答案合成 + 校验
hdr("⑥⑦ 答案合成 + 校验  qa.ask()")
ans = qa.ask(grid, QUESTION, backend="memory")
print(f"  verified   = {ans['verified']}")
print(f"  verify_msg = {ans['verify_msg']}")
print("  ---- 最终输出文本 ----")
print(ans["text"])

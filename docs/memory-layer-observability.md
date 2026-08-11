# 记忆层可观测性 — 已迁出

这份文档 2026-08-10 迁到了 agent workspace：

**`/data/projects/my-agent-workspace/plans/dr-strange-memory-observability.md`**

**为什么搬走**：它主体不是本仓库的操作说明，而是一份**计划**——四个阶段的推进
闸门、预先登记的判据、降级和押后的理由。计划类文档统一归集在 workspace 的
`plans/` 下，和 [`dr-strange-memory-enhancement.md`](/data/projects/my-agent-workspace/plans/dr-strange-memory-enhancement.md)
（4 点增强方案）、[`dr-strange-memory-batch1-plan.md`](/data/projects/my-agent-workspace/plans/dr-strange-memory-batch1-plan.md)
（首批落地 plan）放在一起，才看得出彼此的依赖。

**指针留在这里不删**，是因为多处代码注释和文档引用这个路径（`session_start.py`、
`session_end.py`、`memory-layer-setup.md`、`l3-llm-distillation-setup.md`、
`CLAUDE.md`）。把引用改成仓库外的绝对路径会让它们在别的机器上失效，指针不会。

## 现在最需要知道的一条

**阶段 1 积累中。在闸门到达之前不要改读路径**——召回过滤、排序、注入内容一律不动。
任何改动都会把正在积累的基线样本清零。

**进度 6 / 30（2026-08-11 实跑）。** 注意闸门是**跨全部 5 个装机点**数的：单看本仓库
的 `.drsg/recall.jsonl` 会误以为只有 1 个会话（长会话跨 compact 时 `session_id` 不变），
那不是闸门的输入。同理 `recall-verdicts.jsonl` 里没有 `verdict` 字段是设计如此——
冻结的是 `mass`。两个误读的经过记在 workspace 那份计划的「待办」一节。

不改注入、但仍在产生证据的做法已经上线：`user_prompt.py` 的 `shadow_bm25()` 每个
prompt 用中文 BM25 排一次并记进 `recall.jsonl`，注入不变。派生字段 `Fact.text` 由
`session_start.py` 的 `heal_text()` 自愈，独立修复工具是
[`scripts/memory-layer/backfill_text.py`](../scripts/memory-layer/backfill_text.py)。

进度自己查：

```bash
python3 scripts/memory-layer/analyze_recall.py     # 顺带冻结新判定
```

## 留在本仓库的记忆层文档

- [`memory-layer-setup.md`](memory-layer-setup.md) —— 搭建教程、数据模型、写记忆
  协议详解、Event 通道、排障。**要动手时看这份。**
- [`l3-llm-distillation-setup.md`](l3-llm-distillation-setup.md) —— L3 蒸馏搭建
  （**已关停**，保留备查）。

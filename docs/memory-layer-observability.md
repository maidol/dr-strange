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

**阶段 1 已过闸（49 / 30，2026-08-26），读路径解冻，现在在积累阶段 2。**
上一版这里写的「不要改读路径」已经作废，别再照着执行。

阶段 1 的三条预登记判据全部没触发关停：utilization 74%（口径见下）、foreign/local
比 0.82–0.89、L3 读取率仍为 0。**跨项目召回保留**——52% 的注入跨项目边界，而
zeus / wps / sub2api 本地 Fact 数是 0–1，它们能用上记忆全靠借。

三件当时定不下来、现在有数据的事：

- **利用率有 45% 的随机底噪**（拿没进 top-5 的 Fact 打同一段回复，旧的 6 字符阈值
  照样过）。`MIN_MATCH_CHARS` 因此 6 → 20，底噪降到 17%，分析器现在把两个数并排
  打印，关停线改用「超出随机多少」表述。**冻结的是 mass，所以这次重调没作废任何
  历史判定**（2954 条全部命中缓存）。
- **注入这个动作本身不改变这个指标**：rank 3（注入）和 rank 4（没注入）分数相邻、
  利用率相同（46% vs 43%）。所以它测的是「相关」不是「起作用」，阶段 3 的对照臂
  不能拿它当结局变量。
- **影子 BM25 输了**：355 个双排序 prompt 上 30% vs 生产的 48%，分歧部分 23% vs
  45%（底噪 16%）。排序器不换，影子继续跑。

阶段 2 已经上线：`user_prompt.py` 按 `sha1(session:prompt) % 100 < 15` 抑制一小
部分 prompt（照排照记不注入），`session_end.py` 把工具成败按 prompt 归因写成
`{"event":"tools"}` 行，两者在 `(session, prompt)` 上 join。**闸门是抑制臂满 100
会话**，在那之前分析器的 control arm 一节只是描述性的。

注意闸门是**跨全部装机点**数的：单看本仓库的 `.drsg/recall.jsonl` 会误以为只有
1 个会话（长会话跨 compact 时 `session_id` 不变），那不是闸门的输入。同理
`recall-verdicts.jsonl` 里没有 `verdict` 字段是设计如此——冻结的是 `mass`。

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

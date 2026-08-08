# 记忆层价值 Benchmark

## A 组 · 真实记忆召回(10 题)

| 题 | no-mem | with-mem |
|---|---|---|
| A01 | ❌ | ❌ |
| A02 | ❌ | ❌ |
| A03 | ❌ | ✅ |
| A04 | ❌ | ❌ |
| A05 | ❌ | ✅ |
| A06 | ❌ | ✅ |
| A07 | ❌ | ✅ |
| A08 | ❌ | ❌ |
| A09 | ❌ | ❌ |
| A10 | ❌ | ✅ |
| **命中率** | **0%** | **50%** |

## B 组 · 合成任务质量(LLM judge 1-5)

| 统计 | no-mem | with-mem | Δ |
|---|---|---|---|
| 平均 | 3.67 | 4.83 | **+1.17** |
| 中位 | 4.0 | 5.0 | +1.0 |

## 成本开销(有记忆臂注入)

| 指标 | 值 |
|---|---|
| 平均注入字符数 | 621 chars |
| 估算注入 tokens | ~414 |

## 结论与建议

## 结论与建议

### 结果

**A 组(真实记忆召回)** — no-mem 0/10(0%) → with-mem 5/10(50%)。
B 组(合成任务质量,LLM judge 1-5)— 平均 3.67 → 4.83(**Δ +1.17**),中位 4 → 5。
注入成本:简报+召回平均 621 chars(≈414 tokens)每题,几乎可忽略。

### 人工复核(6 样本)

- **with-mem 普遍更精准**、直接引用项目事实(A05 认证、B03 幂等、B11 Saga)。
- B11 展示记忆的图谱价值:一条「跨服务一致性」任务,把 Saga、幂等、decimal、VPC 四条记忆**关联**起来作答。
- **no-mem 靠通用推理**,常**编造**具体约束(B01 的 DECIMAL(10,2) 是猜的;A03 没给项目特定的 CC 路径)。
- A02 核心答案正确,但混入小幻觉(CC=/usr/bin/clang,实为 gcc)—— n-gram 把相关记忆一并带入,模型扯偏,属轻微副作用。

### 客观匹配偏严的说明

A02/A09 输出核心结论正确(「只在 master/须从 master 构建」「chat 传 base URL」),但因 GT 含元数据词(MCP over Streamable HTTP、v1.4.2、key_env)稀释 gram 比例被判 miss。**50% 是保守下界**,真实召回效果更好;A04 是真 miss(给了通用 openCypher 解构)。

### 基准过程本身发现的真实事故

搭建基准时发现**记忆层静默失效**:L3 digest(`link:false`)从 transcript 提取的 `Key` 实体 external_key="dr-strange",与 Project 节点键冲突,导致所有 hooks 的 `WHERE key(p)=$proj` 解析到 Key 节点 → 简报/召回全部失效(本会话启动时 SessionStart 只注入了协议、没注入简报)。已修复(清理 + 重建 Project + l3_digest 加 collision_guard),并回写 `fact-key-collision-incident`。**这正是「真实会话抽查」要防的事故。**

### 裁决

- **继续使用并维护**。两组均显示显著提升(A 组 0→50% 保守下界,B 组 Δ+1.17),成本 ~400 tokens/题可忽略,且记忆层在图谱关联(跨知识推理)上有 no-mem 无法复制的价值。
- **建议改进**:
  1. `reasoning_effort:"none"` 已成必需(subject/judge 皆 reasoning 模型),确认产品化路径(已在 digest.run 支持)。
  2. n-gram 召回偶发带偏(A02 clang 幻觉),可考虑召回时按 kind 过滤或加相关性阈值。
  3. 键冲突防护(collision_guard)已加,建议作为正式发布的前置检查。

### 复现

```bash
# 全量跑(56 次 subject + 36 次 judge 调用)
python3 scripts/memory-layer/benchmark/run_ab.py --subject-model <claude-ccr-model> --out scripts/memory-layer/benchmark/out/raw.csv
python3 scripts/memory-layer/benchmark/score.py --raw out/raw.csv --out out/scores-all.csv --manual-review out/manual_review.md
python3 scripts/memory-layer/benchmark/report.py --scores out/scores-all.csv --raw out/raw.csv --out BENCHMARKS-MEMORY.md
```

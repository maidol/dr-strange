# 记忆层可观测性 — 现状与待办

> 2026-08-08 存档。讨论发生在 data-safe 的会话里，工作对象是本仓库；
> 下次在 dr-strange 目录开会话接着谈。
> 相关提交：`68e3a7b feat(memory-layer): instrument the read path`

## 起因

问题是"跨项目共享记忆到底有没有提升 agent 的工作质量"。既有的
`BENCHMARKS-MEMORY.md` 是一次性的离线合成 A/B（召回 0%→50%，质量 3.67→4.83），
它能回答"记忆理论上有用吗"，回答不了"它在真实工作里起作用了吗"。

三个缺口：读路径**零遥测**、生产里**没有对照臂**、样本太小（约 19 份 transcript，
只有 2 个项目装了记忆层）。

## 盘点结果（2026-08-08，memory plane）

335 节点 / 233 边，按"谁会读它"切开：

| 层 | 节点 | 谁读 | 价值 |
|---|---|---|---|
| L2 `Fact` | 35 | 简报 + 每 prompt 召回 | 全部价值在这 |
| `Session` | 12 | 只读 `started_at`/`source` | 近乎 0 |
| `Project` | 2 | 定位 | 结构 |
| L3 蒸馏实体 | 286 | **无人读** | 0 |

- 35 条 Fact = data-safe 13 + dr-strange 22。kind 分布：setup-experience 14、
  gotcha 7、decision 6、session-summary 3、test 2、research/reference/无 各 1。
- 真正值钱的约 20 条，集中在两类：**环境陷阱**（gcc 路径、一库一进程、BM25 不支持
  中文、cypher RETURN 限制、token 必带）和**调试启发**（SameSite Lax 不解决跨源
  fetch、"源码里有这段文本"不是断言、替身保真度、E2E 要跑第二轮）。决策记录和会话
  摘要召回价值低。
- 286 个 L3 节点没有任何代码路径能读到——召回只查
  `MATCH (p:Project)<-[:ABOUT]-(f:Fact)`。标签发散到 143 种，内容是无上下文的实体桩
  （`员工账号 10087 测试员工账号。`）。代价约 4 次 LLM 调用 + 8k tokens/会话。
  其中 171/286 来自同一个被反复恢复的会话，同一段 transcript 重复蒸馏 5 次。
- 会话启动注入 1128 字符 = 简报 395 + Recent sessions 109 + **写记忆协议 620**。
  一半以上花在一段固定指令上。

## 已完成

### P0 读路径埋点（`68e3a7b`）

两个读 hook 每次决策写一行 `.drsg/recall.jsonl`。已同步到两个项目的
`.claude/hooks/`（md5 三处一致）。

- `user_prompt.py` 记**所有**结果（`injected`/`no_match`/`no_facts`/`error`）——
  召回率需要分母。记 **top-5 排名**而不是注入的 3 条——将来定阈值需要知道差一点
  被注入的是什么。
- `session_start.py` 拆开 briefing / protocol 字符数，并返回 fact 数，用来区分
  "没什么值得写"和"写路径坏了"。
- `score_facts` 保留原签名给 `benchmark/bench_lib.py`；排序逻辑移到 `rank_facts`。

### P1 分析器 `scripts/memory-layer/analyze_recall.py`

把每次注入和它后面的回复配对，输出利用率、本地/借用拆分、死记忆、成本。

"用了没有"的判定改了三版：数 gram 命中（`crea`/`eate`/`reat` 来自同一个
`created`，被当成三份证据）→ 合并重叠跨度（`一个`+`测试` 又算成两份）→
**按合并跨度的字符量算，≥6 字符**。真语料 34 条 Fact 上 **6/6 真阳性、
3/170 假阳性**；剩下三个是 `create` 和 `return`，中英文都合法，这个信号分不开。

这是**下界代理**，脚本里写明了：对"只是复述记忆"会高估，对"靠预防生效"会低估
（gcc 那条成功时看起来就是构建没失败）。不足 30 个会话时脚本拒绝下结论。

### 三件立即项

- **L3 关停**。`.drsg/env` 里 `DRSG_L3_CHAT=` 置空，原值注释保留在旁边。数据没删。
  依据不是假设：读取率为 0 是代码结构的事实。
- **Recent sessions** 只列有 summary 的会话。目前没有任何东西写
  `Session.summary`，所以整块消失（1128→987 字符）；有摘要了它自己回来。
- **test 记忆**：删 1 条探针；另一条 `fact-data-safe-mutate-with-previous-bug`
  是真见解、kind 标错，改成 `gotcha` 而非删除。

## 待办

### 建议下一步做：失败工具调用数埋点

在 `session_end.py` 记每会话的失败工具调用数（`is_error` 的 `tool_result`，
要滤掉"用户拒绝工具"这类非失败）。

理由：这是 P3 唯一客观的结局代理，且**从下个会话就开始积累**——等
`recall.jsonl` 攒到 30 个会话时它也攒够了。现在不加，将来做对照臂要从零等。

可行性已验证：transcript 里 `tool_result.is_error` 结构存在，dr-strange 那个会话
1085 次工具调用中 65 次失败（6%）。

### P2 陷阱复发率 — 降级，暂不做

原设想：给每条 setup-experience / gotcha 加 `signature`（该失败在 transcript 里的
报错串），比较 fact 记录时点前后的复发频次。

**验证后降级的理由**：signature 同时命中"讨论这个错"和"发生这个错"——gcc 那条
pattern 匹配 1182 次，绝大多数是我们在聊它。要分开只能过滤 `is_error` 的工具结果，
那就退化成上面那条。而且两个项目加起来百来次真失败，fact 又是最近一周才记的，
"之前/之后"里的"之前"几乎不存在。

等积累几个月再加 signature 层。

### P3 生产内交错对照 — 等 P0 基线

确定性抑制：`sha1(session_id + prompt_idx) % 100 < 15` → 召回照算照记但不注入，
arm 写进日志。对照的结局代理：失败工具调用数、用户纠正信号率、到首次构建/测试通过
的轮数。需要 100+ 会话。

## 预先登记的判据

**在看到数据之前定死，事后再定就是自我合理化。**

| 条件 | 动作 |
|---|---|
| 3 个月后 utilization < 20% | 召回策略失败，改 kind 过滤或加相关性阈值 |
| utilization(foreign) < 0.5 × utilization(local) | 关掉跨项目召回 |
| 陷阱复发率与记录时点无关 | 记忆没在防事故，重估 |
| L3 节点读取率仍为 0 | 保持关停 |

## 埋点当天就暴露的一个问题（先记着，别急着改）

```
prompt: "构建时 ring 编译失败,gcc 路径不对"
  exp-cc-env-var-broken            112.73   ← 对的
  exp-deepseek-reasoning-truncate   10.69   ← 无关,照样注入
  exp-single-daemon-controller      10.42   ← 无关,照样注入
```

`MAX=3` 是硬填满的，没有分数阈值。相对阈值（低于首条 20% 就丢）大概率更好，
**但按预先登记的纪律没动**——凭一条日志调参正是这套纪律要防的事。`ranked` 字段
已在记 top-5，攒够数据再定。

## 恢复讨论时先跑这个

```bash
python3 scripts/memory-layer/analyze_recall.py            # 全部项目
python3 scripts/memory-layer/analyze_recall.py --since 14
```

`.drsg/recall.jsonl` 目前是空的（探针数据已清），从下个会话开始积累。

## 仓库状态

- 本文档与 `68e3a7b` 都在 `claude/project-code-analysis-wt83lo`（个人工作分支）。
- `stash@{0}` 存着 `feat/mcp-over-http` 上未提交的 ROADMAP §10 shipped 状态改动，
  切回那个分支后 `git stash pop`。
- 五个抽取出来的 PR 分支已推未开，等 review。

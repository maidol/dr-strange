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

### P3 前置：失败工具调用数埋点（本次）

`session_end.py` 的 `mine()` 现在同时扫 `user` 消息里的 `tool_result`，在 Session 节点上
落四个属性：`tool_calls` / `tool_errors` / `tool_rejected` / `tool_errors_top`。

- **不落单个数字，落原始分子分母**。判据是"什么算失败"——把它写死在 hook 里，等于事后
  没法改口径。所以存总调用数（分母，率比计数可比）、按工具拆的失败分布，让分析侧决定。
- **用户拒绝工具**（`is_error` 但内容是 "The user doesn't want…" / "tool use was
  rejected" / "interrupted by user"）单独计 `tool_rejected`，不算失败也不静默丢掉。
- **按工具拆分是必需的，不是锦上添花**。真语料上一个会话的 65 次 `is_error` 里 22 次是
  模型供应商 "temporarily unavailable"——基础设施抖动，不是工作质量。拆开才分得清；
  实测另一个会话是 `Bash×23, mcp__drsg__cypher×7, Read×1`，语义完全不同。
- `tool_use_id → 工具名` 的映射靠同一遍扫描顺带建（`tool_result` 只带 id 不带名字）。
- **计数受 `MAX_LINES=4000` 截断**，长会话拿到的是窗口样本不是全量；分子分母同窗口，
  所以率仍然成立，计数不成立。
- 成本：最大一份 79MB transcript 上 `mine()` 137ms（本来就在读同一批行），SessionEnd
  1.5s 共享预算内。三处副本 md5 一致（`ca71a936`）。

`analyze_recall.py` **暂不读这些属性**——现在只有基线在积累，没有对照臂可比。

### 证据保鲜：判据不能活得比证据长（本次）

跑数据时发现的阻断项:判"这条记忆用了没有"要读 transcript 里的回复,而 Claude Code
默认 **30 天**清理 transcript(`cleanupPeriodDays` 没设)。磁盘上最老的一批正好停在
`07-09`,当天是 `08-08`——不多不少整 30 天,已经在删了。三个月的判据到期时,前两个月的
注入全变成不可判。两层修:

1. `~/.claude/settings.json` 加 `"cleanupPeriodDays": 180`(纯插入一行)。
2. **分析器增量化**。每条判定第一次算出来就冻进 `.drsg/recall-verdicts.jsonl`,
   之后从那里读。只要在保留期内跑过一次,判定就比 transcript 活得久。
   - 冻的是**匹配字符 mass 而不是 yes/no**——`MIN_MATCH_CHARS` 将来还能在全部历史上
     重新调。DF 过滤没法这样复用,所以改 gram 逻辑必须 bump `VERDICT_VERSION`:能重算的
     重算,不能的报 stale,而不是把两套口径静默混在一起。
   - 文件只追加不重写;`--no-cache` 强制重算且不冻结。
   - 验证:两次运行 48→0 新算 / 0→48 命中缓存,结果都是 36/48 (75%),文件没翻倍;
     `--no-cache` 重算得到同一个 36/48;**把 `TRANSCRIPT_ROOT` 指向不存在的目录
     (模拟 30 天后)仍然输出 36/48** —— 这条才是要证的。

### 扩大样本面（本次）

原来只有 2 个项目装了记忆层,而最近 30 天全局 19 个会话里最活跃的 `wps` 占 10 个、
`zeus` 占 3 个,都没装。现已装上,4 个项目全部指向同一个守护进程:

```
data-safe  dr-strange  wps  zeus     # memory plane 里 4 个 Project 节点
```

五处 hook 副本 md5 一致(`ca71a936`),所以 `tool_*` 埋点同时在四个项目生效。L3 保持关停。

顺带修了 `install.sh` 的一个真空:它写 `.drsg/env`(含 API token)却从不碰
`.gitignore`。装进这两个仓库前先补上——`chmod 600` 挡得住别的用户,挡不住 `git add .`。
现在装到任何 git 仓库都会自动忽略 `.drsg/`。

### 三件立即项

- **L3 关停**。`.drsg/env` 里 `DRSG_L3_CHAT=` 置空，原值注释保留在旁边。数据没删。
  依据不是假设：读取率为 0 是代码结构的事实。
- **Recent sessions** 只列有 summary 的会话。目前没有任何东西写
  `Session.summary`，所以整块消失（1128→987 字符）；有摘要了它自己回来。
- **test 记忆**：删 1 条探针；另一条 `fact-data-safe-mutate-with-previous-bug`
  是真见解、kind 标错，改成 `gotcha` 而非删除。

## 待办 — 四个阶段，按触发条件推进（不按日期）

前置修补已完成(保留期 180 天 + 判定冻结 + 4 个项目),**阶段 1 现在开始积累**。

| 阶段 | 触发条件 | 动作 |
|---|---|---|
| **1 基线** | `recall.jsonl` 满 30 会话 | 脚本自己解禁下结论。判 utilization 与 foreign/local 比;同时看 `tool_errors` 的**基线分布**(还没有对照臂,只建立"正常失败率长什么样")。顺带处理从没被召回的 Fact 和 `MAX=3` 无阈值 |
| **2 开对照臂** | 阶段 1 判据都没触发关停 | 上 P3 的确定性抑制,开始积累 arm 对比 |
| **3 判决** | 抑制臂满 100 会话 | 对照失败率与 `tool_errors_top` 分布。这才是"跨项目共享记忆有没有提升工作质量"的正面回答 |
| **4 P2** | 3 个月以上 | 加 signature 层做陷阱复发率 |

**阶段 1 不下最终结论**。它只回答"召回策略本身合不合格",不回答"有没有让工作变好"
——后者必须有对照臂,那是阶段 3。所有调参冲动都压到阶段 1 之后。

唯一的日常义务:**保留期内至少跑一次分析器**,把判定冻下来。180 天的窗口很宽,但
跑一次的成本是 0,别赌。

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
python3 scripts/memory-layer/analyze_recall.py            # 全部项目（顺带冻结新判定）
python3 scripts/memory-layer/analyze_recall.py --since 14
python3 scripts/memory-layer/analyze_recall.py --no-cache  # 只在怀疑冻结值时用
```

`verdicts : N computed now, M from frozen` 那一行是健康度:M 涨说明冻结在起作用,
出现 `stale` 说明 `VERDICT_VERSION` 变过而 transcript 已经没了。

积累进度（2026-08-08 晚，装了记忆层的项目 2 个 → 4 个后）：2 会话 / 19 prompt /
48 次注入，utilization 75%，foreign/local 0.67 : 0.75。**离 30 会话的门槛还很远，
脚本也确实拒绝下结论**——这几个数只用来确认管道通了。同一批数据还暴露 34 条 Fact 里
15 条从未被召回过，多数是措辞对不上真实提问（`exp-cc-env-var-broken` 这种明显有用的
也在里面），按阶段 1 的安排到样本量够时一并处理。

`tool_*` 属性从下个会话结束时开始落到 Session 节点。

## 仓库状态

- 本文档与 `68e3a7b` 都在 `claude/project-code-analysis-wt83lo`（个人工作分支）。
- `stash@{0}` 存着 `feat/mcp-over-http` 上未提交的 ROADMAP §10 shipped 状态改动，
  切回那个分支后 `git stash pop`。
- 五个抽取出来的 PR 分支已推未开，等 review。

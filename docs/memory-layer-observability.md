# 记忆层可观测性 — 现状与待办

> 2026-08-08 起档，末次更新 2026-08-10。工作对象是本仓库的记忆层。
> 相关提交：`68e3a7b`（读路径埋点）、`ca61c3a`/`6bc5767`（注入链路两个洞）、
> `c3f2166`/`50b093f`（首批增强，写侧先行）。
>
> **当前状态：阶段 1 积累中，`sessions : 4 / 30`。在闸门到达之前不要改读路径**
> ——召回过滤、排序、注入内容一律不动，理由见「待办」一节。

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

### 复盘（2026-08-10，两天后）

**358 节点 / 251 边**。上表按同一口径重算：

| 层 | 08-08 | 08-10 | 变化说明 |
|---|---|---|---|
| L2 `Fact` | 35 | **52** | 两天 +17，全部经写记忆协议由模型自己写 |
| `Session` | 12 | 14 | |
| `Project` | 2 | **5** | 装机点从 2 → 5（+ wps / zeus / my-agent-workspace） |
| `Event` | — | **2** | 新通道，见「首批增强落地」 |
| L3 蒸馏实体 | 286 | 285 | 关停后不再增长（差的 1 个是清测试数据时顺带删的） |

L3 的 285 个残留仍占**总节点的 80%**，143 种标签也全部来自它们。数据没删是刻意的
（关停的依据是读取率为 0，不是数据有害），但每次盘点都要记得把它们从分母里剔掉，
否则"记忆层有 358 个节点"是个假象——真正有读路径的是 73 个。

写侧两天涨 17 条 Fact，说明**协议这条通道是活的**——这是关停 L3 时最大的不确定性
（"没了 L3 还有没有东西在写"），现在有答案了。

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

### 注入链路的两个洞（2026-08-09）

盘点时只量了注入**花多少**，没量它**还在不在**。两个都是静默失效：

- **压缩会把注入吃掉**（`ca61c3a`）。SessionStart 的 matcher 原是 `startup|resume`，
  不含 `compact`。一次 `/compact` 之后简报和写记忆协议就掉出上下文，会话继续跑但
  记忆层实际已经停了——越长的会话越受影响，而长会话正是最需要它的。matcher 加上
  `compact`；那一路不能重建 Session 节点（session_id 不变，`node.create` 会撞 key），
  改成只补 `compacted_at`。遥测加 `source` 字段，否则同一会话两条 briefing 记录分不清。
- **协议指的坐标和读侧不是同一个**（`6bc5767`）。协议让模型把 Fact 连到
  `Project (key=<slug>)`，而 `project_id()` / `all_facts()` 一律按 `p.path` 查——那次
  key 被同名节点遮蔽的事故之后就改了，协议文本没跟着改。后果不是报错，是**边连到影子
  节点上、Fact 永久隐形**。协议改成 `p.path = "<绝对路径>"`。顺带删掉每会话一条
  `session-summary` Fact 的要求：没有任何代码读它（简报里的 Recent sessions 读的是
  `Session.summary`，另一个属性，也没人写）。

协议因此从 620 → 639 字符（绝对路径比 `key=dr-strange` 长）。上面 1128 字符那次盘点
是改动前的数，不回填。

`tool_*` 埋点**尚未在真实会话里验证过**——从加上到现在还没有会话结束过。第一个
带 `tool_calls` 的 Session 节点出现时才算这条通了。

### 文档同步（2026-08-09）

`memory-layer-setup.md` 落后代码好几处，一并修了，并补上最大的一块空白:

- 新增 §3.6.3「记忆的**写**」——三条通道对照 + **写记忆协议详解**:它只是一段提示词、
  每条格式约束对应读侧哪行代码、写歪了为什么不报错只是读不出来、以及它的弱点
  (零强制零校验)。此前全仓库关于协议只有表格里的一格。
- **L1/L2 命名撞车**:§3.6.2 用 L1/L2 指「读」的两层,§7 第 10 条用 L1/L2/L3 指
  「写」的三层,同一份文档同名不同义。L 编号现在只用于写这一侧,读侧改用 ①/②。
- 修过期描述:`WHERE key(p)=$proj` → `p.path`、session_end「挖 2 样」→ 挖 3 类、
  注入内容不再是「最近 Fact」而是简报、Project 存在性按 path 判定、compact 那一路。
- L3 关停状态补进 §3.8 和 `l3-llm-distillation-setup.md` 抬头(两份文档此前仍把它
  当活的);`CLAUDE.md` 记忆层小节同步。

### 首批增强落地：按「碰不碰读路径」切两半（2026-08-10）

外部提了一份 4 点增强方案（时间有效事实窗 / agent 协调通道 / L3 实体消歧 /
verbatim 原文回查）。**其中 3 点都改读路径**，而下面那份预登记计划正在积累阶段 1
基线——现在改召回过滤或注入内容，样本清零重来。

处理办法不是全押后，是**按污染与否切开**：

| 现在做（不碰读路径） | 押到阶段 1 之后 |
|---|---|
| Fact 的 `supersedes` / `valid_to`：只改写记忆协议的措辞，字段先攒 | 召回/简报按 `valid_to` 过滤、失效标记 |
| Event 协调通道：**独立注入块**，不参与 Fact 排序，遥测口径不变 | Event 的 `acked` 流转、`/ws` 实时推送 |
| — | verbatim Chunk（`session_end.py` 侧写入也一并押后，因为它没有读路径，会重演 L3 的死节点模式） |
| — | L3 实体消歧（前提已失效：L3 已关停，先回答"谁读它"再谈"怎么救"） |

落地的两条（`c3f2166` / `50b093f`）+ 文档（`f7ac1f8`）。核实过的三件事：

- **写侧先行是可行的**，因为读侧代码一行没动——验证方式是 `git diff` 里
  `all_facts` / `build_briefing` / `short_tag` / `ensure_briefing` / `project_id`
  被触碰行数为 **0**，而不是靠"我觉得没影响"。
- **Event 不需要新 RPC**。方案原本要在 `rpc.rs` 加 `event.append`；实际
  `node.create` / `edge.create` / `plane.cypher` 就够，收方由 `NOTIFY` 边的目标
  决定而不是属性。唯一一个"跨语言栈"的增强点其实是纯 hook 的。
- **时间必须是 epoch 整数**。plane 里现存 `created_at` 全是整数，而引擎的
  `Int` 与 `Str` 比较返回 `None`（`compute/expr.rs:344-352`）→ 谓词恒假、
  `ORDER BY` 按类型分层，**不报错**。协议顺带把 `created_at` 从"current time"
  钉成"current Unix time (integer seconds)"。

遥测新增 `events` / `event_chars` 两个字段，`brief_chars` / `proto_chars` 语义不动
（`analyze_recall.py` 用 `.get(k, 0)` 读，加字段安全，已跑通验证）。协议长度
**639 → 809 字符**，涨 27%——固定成本，记在这里备查。

跨项目通道已在真实会话里闭环：dr-strange 侧 post，收方 my-agent-workspace 的
SessionStart 遥测记到 `events=1, event_chars=189`，`done` 之后收方
`open_events()` 返回 0。

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

**2026-08-10 进度：`sessions : 4` / 30**，判定已冻结 278 行
（dr-strange 112 + my-agent-workspace 99 + data-safe 67）。42 条 Fact 里 4 条从未
被召回、1 条注入 ≥5 次从未被用（`fact-bench-memory-layer-v1`），仍按阶段 1 的安排
不动。

**闸门为什么走得慢，查清楚了**：装机点 5 个，但 **wps 和 zeus 一次会话都没开过**。
两者的 Project 节点只有 `path`、没有 `briefing` 属性（说明是 `install.sh` 建的而不是
会话建的），`.drsg/` 下只有 `env` 没有 `recall.jsonl`。排除了坏掉的可能：hooks 在
`settings.local.json` 里注册正常，token 打 `db.stats` 通。而本文档 08-09 记的是
"最近 30 天全局 19 个会话里 wps 占 10 个、zeus 占 3 个"——**样本增速的杠杆全在这两个
项目身上**，不在 dr-strange。

### 这份文档自己的盲点：静默早退不留痕

上面那个排查暴露了埋点的一个洞。`session_start.py` 有两条早退路径：

```python
if not token:            hook_out(); return      # 没写遥测
try: rpc("db.stats", ...)
except: hook_out(); return                       # 守护进程连不上，也没写遥测
```

两条都**先于 `telemetry()`**。后果是"token 配错了""守护进程挂了""这个项目根本
没人用"三种情况在磁盘上长得一模一样：`recall.jsonl` 不存在。

这正是本文档反复在防的那类错误——当初把 `brief_chars` / `proto_chars` 拆开、
`tool_*` 存原始计数，都是为了让"没发生"和"坏了"分得开，而入口本身漏了同一件事。

**没有立刻改**，理由和 `MAX=3` 那条一样：改埋点会改变正在积累的样本口径。记在这里，
和阶段 1 的调参一起处理。届时的改法很小——早退前写一行
`{"event":"briefing","status":"no_token"|"daemon_down"}`，`analyze_recall.py`
已经在按 `event` 字段过滤，多一种 status 不影响现有统计。

### `tool_*` 埋点已验证（2026-08-10）

08-09 那条"尚未在真实会话里验证过"可以关掉了。14 个 Session 里 **4 个带
`tool_calls`**（其余是埋点上线前的），实测基线分布：

| session | calls | errors | rejected | 失败率 | `tool_errors_top` |
|---|---|---|---|---|---|
| 0b4c8f38 | 400 | 56 | 0 | **14.0%** | `Bash×32, mcp__drsg__cypher×19, Edit×2` |
| 4631e7b0 | 87 | 5 | 0 | 5.7% | `Bash×4, Agent×1` |
| 68bcb51c | 540 | 14 | 0 | 2.6% | `Bash×13, Agent×1` |
| 133a8256 | 597 | 31 | 4 | 5.2% | `Bash×23, mcp__drsg__cypher×7, Read×1` |

**这就是阶段 1 要的"正常失败率长什么样"**：2.6%–14%，跨度 5 倍。分子分母和按工具
拆分都拿到了，说明当初"不落单个数字、落原始计数"的决定是对的——如果当时只存一个
失败率，14% 那个会话就没法判断是工作质量差还是 `Bash` 在刷错误。

三点提醒：

- **`tool_rejected` 首次非零**（133a8256 的 4 次），证明"用户拒绝"确实和失败分得开。
- **跨度 5 倍意味着单会话对比没有意义**。阶段 3 的对照必须按臂聚合，不能拿两个会话
  比。
- 计数受 `MAX_LINES=4000` 截断，率成立、计数不成立——上表的 calls 是窗口内的。

## 仓库状态（2026-08-10 核过）

- 本文档、`68e3a7b` 与首批增强（`c3f2166` / `50b093f` / `f7ac1f8`）都在
  `claude/project-code-analysis-wt83lo`（个人工作分支）。按铁律**不提 PR**。
- hook 有 **6 份副本**（模板 1 + 安装点 5），每次改动必须同步全部并核 md5
  收敛成一行。当前 `3c5e03d1…`（`session_start.py`）。
- `stash@{0}` 仍存着 `feat/mcp-over-http` 上未提交的 ROADMAP §10 shipped 状态改动。
  **该分支已被上游合并**（PR #3，2026-08-08），所以这个 stash 多半已经是重复品，
  下次处理时先和上游的 ROADMAP 比对再决定 pop 还是 drop。
- 抽取的 PR 分支：`feat/mcp-over-http` 已合并（领先 upstream/master 0）；
  `fix/order-by-total-order` 已确认被上游自行修复并删除；余下四个各领先 1 个 commit，
  已推未开。本地 `master` 落后 upstream/master 11 个（上游已发 v1.6.0）。

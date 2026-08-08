# dr-strange 作为 Claude Code 智能体长期记忆层 — 搭建教程

> 记录 2026-08-04 在 `/data/projects/maidol/dr-strange` 完成的实际搭建。目标:让 Claude Code 会话具备**跨会话持久记忆**——会话启动自动注入历史记忆,会话中通过 MCP 工具读写,会话结束自动收尾。

## 1. 架构

```
                    ┌─────────────────────────────────────────────┐
                    │  本项目 .claude/ (已 gitignore)              │
   Claude Code 会话 ─┤  settings.local.json ── SessionStart/End    │
        │           │     │                    hooks  ↓  curl      │
        │ MCP tools │     └── session_start.py / session_end.py ──┤
        │(13 个工具)│                    │                          │
        ▼           │                    ▼                          │
  /mcp (Streamable  │             POST /rpc (JSON-RPC)              │
  HTTP)             │                    │                          │
        │           │                    ▼                          │
        └───────────┼─────►  drsg serve 守护进程 (唯一进程)          │
                    │        持有 ~/.drsg-memory/memory.drsg 库     │
                    └─────────────────────────────────────────────┘
```

### 三条铁律(来自代码)

1. **native 后端同一数据库只允许一个进程打开**(commit `24f24e5`)。不能用「每会话一个 `drsg-mcp` stdio」——并发会话会锁冲突。必须**单个 `drsg serve` 共享进程**,会话经 Streamable HTTP 连它的 `/mcp`。
2. **无 token 时 native 客户端连读都被拒**(`crates/dr-strange-web/src/auth.rs`)。必须设 `DRSG_TOKEN`,hooks 的 curl 和 MCP 都要带 `Authorization: Bearer`。
3. **hooks 只能跑命令,不能调 MCP 工具**。所以 hooks 走 JSON-RPC `POST /rpc`,不走 CLI(CLI 会直接开库,与 serve 抢锁)。

## 2. 前置条件

- **Rust 工具链**:本项目从 master 构建,需要 MSRV 1.85+。若机器没有,`curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable`,然后 `source ~/.cargo/env`。
- **⚠️ 本机坑**:环境变量 `CC=/usr/local/bin/gcc` 指向不存在的路径,真实 gcc 在 `/usr/bin/gcc`。构建必须显式:
  ```bash
  source ~/.cargo/env && CC=/usr/bin/gcc CXX=/usr/bin/g++ cargo build --release -p dr-strange-cli
  ```
- **为什么必须本地构建**:`/mcp`(MCP over Streamable HTTP)特性在 commit `db57bc4`(2026-08-04)才合入,**晚于 v1.4.2 发布**(2026-08-03)。GitHub 上的发布版二进制不含 `/mcp`。要获得它只能从 master 构建。只需 `drsg` 一个二进制(serve 自己挂 `/mcp`),不需要 `drsg-mcp`,也不需要构建 SPA(`build.rs` 在无 `dist/` 时嵌入占位页)。

## 3. 步骤

### 3.1 建目录、生成 token、写 env

```bash
mkdir -p .drsg .claude/hooks
openssl rand -hex 16        # ← 生成 token
```

`.drsg/env`(权限 600,已被 gitignore):

```
DRSG_TOKEN=<token>
DRSG_API=http://127.0.0.1:7700/rpc
DRSG_PLANE=memory
DRSG_L3_CHAT=http://127.0.0.1:3456/v1      # 可选,L3 蒸馏
DRSG_L3_KEY_ENV=CCR_API_KEY                 # 只存 key 的【名字】,值在 daemon 侧
DRSG_L3_MODEL=DeepSeek/deepseek-v4-flash
DRSG_L3_REASONING=none
```

> L3 的 LLM key **值**只存 daemon 侧(`~/.drsg-memory/env`,install.sh 写入),项目 `.drsg/env` 只有名字。`digest.run` 传 key 名,daemon 从自身 env 读值(见 §3.8)。

`.gitignore` 追加一行 `*.drsg` 已覆盖数据库目录,再补 `.drsg/`(env/日志)。

### 3.2 全局守护脚本(唯一控制器)

记忆层由**一个全局共享 daemon** 服务(数据库在 `~/.drsg-memory/memory.drsg`,所有项目同一库,按 Project 节点隔离)。控制脚本是全局唯一的 `scripts/memory-layer/serve.sh`,项目内**不再有** `.drsg/serve.sh`(已删,避免双控制器):

```bash
scripts/memory-layer/serve.sh start|stop|restart|status
# 参数缺省时从 ~/.drsg-memory/env 回落(BIN/ADDR/token/key);start 会把该文件
# 的 KEY=VALUE 导出进 daemon 进程 env(L3 鉴权需要)。
```

### 3.3 启动并验证 API

```bash
scripts/memory-layer/serve.sh start && scripts/memory-layer/serve.sh status   # → running + {"status":"ok"}
source .drsg/env
curl -sf -X POST http://127.0.0.1:7700/rpc \
  -H "Authorization: Bearer $DRSG_TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"db.stats","params":{}}'
```

### 3.4 建 memory plane

```bash
curl -sf -X POST http://127.0.0.1:7700/rpc -H "Authorization: Bearer $DRSG_TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"plane.create","params":{"name":"memory"}}'
```

> 必须走 RPC,不能 `drsg plane create`(会直接开库,与 serve 抢锁)。

### 3.5 注册 MCP(仅本项目)

```bash
source .drsg/env
claude mcp add --scope local drsg --transport http http://127.0.0.1:7700/mcp \
  -H "Authorization: Bearer $DRSG_TOKEN"
```

- **⚠️ 写入位置**:`--scope local` 实际写入 `~/.claude.json` 的 `projects["<本项目>"]` 条目(**不是** `.claude/settings.local.json`)。仍是项目级生效,token 在 home 目录、不进 git。
- 验证:`claude mcp list` → `drsg: http://127.0.0.1:7700/mcp (HTTP) - ✔ Connected`。
- **改完必须重启 Claude 会话**才能看到 13 个工具。

#### 3.5.1 MCP 工具如何起作用

**一句话**:MCP 工具是「会话内、由模型主动调用的读写记忆入口」,与 hooks 的「自动记录/注入」形成两条互补通道。

**完整链路**:

```
Claude Code 会话(模型)
   │
   │ ① 会话启动时,客户端读 ~/.claude.json 的 project 条目
   ▼
  MCP 客户端 ── Streamable HTTP (POST http://127.0.0.1:7700/mcp) ──► drsg serve
   │ ② initialize 握手 → tools/list → 拿到 13 个工具名 + 描述
   │ ③ 模型想读写记忆 → tools/call {name:"cypher", args:{...}}
   ▼
  mcp_auth 中间件(auth.rs: Write 级鉴权,Bearer token)
   │ ④ 通过后 → rmcp 路由 → #[tool] 方法 → blocking() → 核心逻辑
   ▼
  DrStrange { db: Arc<Database> } ──► 操作 memory plane
```

**4 个环节的机制**:

1. **注册**:`claude mcp add` 写入 `~/.claude.json` 的 `projects["<项目>"]` 条目。Claude Code 启动会话时读它,才知道「这个项目有个叫 drsg 的 MCP 服务器」。
2. **握手 + 工具清单**:客户端 `initialize` → 服务端返回 `capabilities:{tools:{}}`、`serverInfo`;再 `tools/list` 把工具描述喂给模型。**这就是为什么重启会话后模型才「看得见」工具**——描述在会话启动时加载进上下文。
3. **模型调用**:模型判断需要时主动发起 `tools/call`,例如 `{"method":"tools/call","params":{"name":"cypher","arguments":{"plane":"memory","query":"MATCH ..."}}}`。
4. **服务端执行**(`crates/dr-strange-mcp/src/lib.rs`):每个工具是 `#[tool]` 方法,一行包一个纯函数(如 `cypher_logic`);`self.blocking()` 用 `tokio::task::spawn_blocking` 把同步 DB 操作甩出异步 runtime(长查询不卡 HTTP 流);结果包成 `CallToolResult` 返回,核心错误变成**工具级错误**(模型能在对话里看到报错)。同一套 `DrStrange` 工具集,stdio(`drsg-mcp`)与 HTTP(`/mcp`)共用——只换传输,不换工具代码。

**与 hooks 的分工**:

| | hooks (SessionStart/End) | MCP 工具 |
|---|---|---|
| 触发者 | 事件自动触发,模型不参与 | 模型在对话中按需主动调用 |
| 传输 | 命令 → curl → `POST /rpc` | MCP 协议 → `POST /mcp` |
| 做啥 | 自动:记录会话、注入历史、盖 ended_at | 按需:查询/写入/检索/digest 文档 |
| 时效 | 启动/结束那一刻 | 整个会话随时 |

两条通道打同一个 `Database`(共享 serve 进程持有),数据互通:session_start 注入的记忆就是模型后来用 MCP 查到的内容。

**13 个工具怎么用**:读 `list_planes`/`describe_plane`(先摸 schema)→ `cypher`(结构化查询)→ `search`/`hybrid`(向量+BM25+图近邻)→ `get_node`/`traverse`;写 `write_nodes`/`write_edges`(幂等)或 `cypher` 的 `MERGE`/`SET`;分析 `algo`(pagerank/components/shortest_path/louvain);AI 增强 `digest`(文档→实体关系,`apply:false` 先审再落)、`ask`(自然语言问图)。

#### 3.5.2 在交互终端使用

**关键认知**:MCP 工具**不是给人敲的命令,是给模型(agent)调用的接口**。你在终端里的用法,是用自然语言让模型去调——不需要记任何参数,工具描述和 schema 在会话启动时已加载进模型上下文。

**三种使用方式**:

1. **你打自然语言,模型调工具**(主要方式)——直接说需求,模型判断用哪个工具、拼什么参数:
   ```
   你: 查一下 memory plane 里最近写了哪些关于构建的经验
   我: [自动调 cypher → 返回结果 → 整理给你]

   你: 帮我把「结论:本地构建必须 CC=/usr/bin/gcc」记到记忆里
   我: [调 write_nodes + write_edges,以 external_key 幂等写入]

   你: 这个项目有哪些重要的记忆概念?聚类分析一下
   我: [调 algo 跑 louvain/pagerank]
   ```
2. **`/mcp` 命令**(交互终端内)——列出所有 MCP 服务器及连接状态,会看到 `drsg: http://127.0.0.1:7700/mcp`。
3. **没有「人直接敲工具」的方式**——MCP 工具是 agent 专用接口。想在 shell 里直调库,走 `POST /rpc`(就是 hook 脚本干的事),不是 MCP。

**你要怎么说话**(工具速查):

| 你想干嘛 | 对模型说 | 模型用哪个工具 |
|---|---|---|
| 摸库结构 | 「看看 memory plane 里有什么」 | `list_planes` / `describe_plane` |
| 结构化查记忆 | 「最近 5 条事实」「2026-08 的会话」 | `cypher` |
| 语义检索 | 「找和『构建坑』相关的记忆」 | `search`(向量)/ `hybrid`(向量+关键词+图近邻) |
| 沿图展开 | 「从 Project 节点往外看有哪些边」 | `traverse` / `get_node` |
| 写一条结论 | 「把这句记成 Fact」 | `write_nodes` + `write_edges`(幂等) |
| 更新已有 | 「给 session x 补上 ended_at」 | `cypher` 的 `MERGE`/`SET` |
| 重要性分析 | 「哪些概念最重要」 | `algo` pagerank |
| 聚类 | 「记忆里有哪些主题簇」 | `algo` louvain |
| 文档入库 | 「把这份文档抽取成实体关系」 | `digest`(默认 dry-run,先审再 `apply:true`) |
| 自然语言问图 | 「记忆里和 MCP 相关的内容」 | `ask`(内部把问题转成查询) |

**和 hooks 的区别(别混淆)**:hooks 自动跑——会话一开/一关,记忆自动记录、自动注入(会话顶部那段 `# dr-strange 记忆(...)` 就是 hook 注入的);MCP 工具要你开口——主动读/写/分析记忆时才触发。

### 3.6 hook 脚本

`.claude/hooks/session_start.py` — 记录会话 + 注入历史记忆。要点:

- stdout **必须只输出一个 JSON**(SessionStart exit 0 的 stdout 会注入上下文,坏了会打断会话)。
- 失败一律软降级:服务没起就只输出空 hook output,不阻塞会话。
- 数据模型:`Project`(key=目录名,幂等)←`BELONGS_TO`←`Session`(key=session_id);`Project`←`ABOUT`←`Fact`。
- 注入内容:`# dr-strange 记忆(<slug>)` + 最近会话 + 最近 Fact。

`.claude/hooks/session_end.py` — 盖 `ended_at`。**务必快**:SessionEnd hooks 共享 1.5s 预算(settings 里配 `timeout` 可抬高)。

```bash
chmod +x .claude/hooks/*.py
```

#### 3.6.1 session_start / session_end 读写清单

**session_start.py(会话开始时)** —— 写 3 样 + 查 2 样 + 注入协议:

| 方向 | 内容 | 关键点 |
|---|---|---|
| 写 | `Project` 节点(key=目录名) | 幂等,已存在则跳过 |
| 写 | `Session` 节点(key=`session_id`) | `started_at` / `source` / `cwd` / `project` |
| 写 | `BELONGS_TO` 边:Session ─► Project | |
| 查 | 该项目**最近 3 个会话**(按 `started_at` 倒序) | `LIMIT 3`,`WHERE key(p)=$proj` |
| 查/建 | 全部 Fact → 聚合 `Project.briefing` | Fact 数变才重建(缓存) |
| 注入 | 简报 + 最近会话 + **【写记忆协议】** | 让模型自动写有价值记忆 |

查询结果打包成 `additionalContext` 注入对话开头(会话顶部那段 `# dr-strange 记忆(...)`)。失败一律软降级:服务没起就只输出空 hook output,不阻塞会话。

**session_end.py(会话结束时)** —— 写 1 样 + 挖 2 样(L1 结构事实):

| 方向 | 内容 | 关键点 |
|---|---|---|
| 写 | 给本次会话 `Session` 补 `ended_at` | `node.update` + `set` |
| 挖 | `files_touched`(Read/Write/Edit 过的文件×次数) | 读 `transcript_path`,流式扫描 |
| 挖 | `commands_run`(Bash 命令×次数) | 正则清洗,前几个 |

**务必快**:SessionEnd hooks 共享 1.5s 预算(settings 配 `timeout` 可抬到上限 60s)。一次读取 + 一次 RPC,`MAX_LINES=4000` 封顶,挖掘 best-effort、失败无害。

**为什么会话可能没有 `ended_at`**:只有真正跑到 session_end 的会话才盖上。若只跑了 start(如 crash、测试),Session 节点存在但 `ended_at` 缺失——这在会话清单里表现为「进行中/未收尾」,不是 bug。

#### 3.6.2 记忆加载层级:简报(L1)+ 按需召回(L2)

为了让记忆价值体现、又**不占过大上下文**,加载分两层,互不冲突:

**L1 — SessionStart 注入压缩简报(常驻成本 ~1 段话,恒定)**
- 不再是「逐条注入最近 Fact」,而是把项目全部 Fact 聚合成 `Project.briefing`(按 `kind` 分组,每条压缩成 ≤18 字标签)。
- 缓存:`Project.briefing_count` 记录已聚合的 Fact 数;SessionStart 发现 Fact 数变化才重建,否则复用——**新 Fact 写得越多,简报保持 ~200 tokens**。
- 注入内容:简报 + 最近 3 会话。

**L2 — UserPromptSubmit 按当前任务召回(按需,命中才注入,≤3 条)**
- 新增 `.claude/hooks/user_prompt.py`,在**每次用户输入**时拿 prompt 去召回相关 Fact,只注入最相关的 3 条。
- **跨项目召回**:recall 查询**不加** `WHERE key(p)=$proj` 过滤,覆盖**所有项目**的 Fact(同一 daemon/同一 plane)。靠 n-gram+IDF 打分天然按 prompt 定向——在 A 项目问的问题若恰与 B 项目的经验相关,会自动带过来;无关项目的 Fact 得分 ~0 不会混入。例:在 data-safe 问「L3 key 鉴权」自动召回 dr-strange 的 `exp-l3-key-server-side`。L1 简报仍按项目隔离(常驻预算)。
- 匹配方式:**字符 n-gram 反向匹配 + IDF 加权**。为什么不是 BM25/`plane.find`:
  - dr-strange 的 BM25 分析器**只支持英文**(`Language::English`,Snowball 分词);中文整句被当一个 token,`plane.hybrid` keyword 通道查「构建」返回 **0 命中**(实测)。
  - `plane.find` 是 substring 扫描,要求 **query 是文档子串**——整句中文 prompt 命中不到。
  - 替代:脚本读一次全部项目 Facts,把 prompt 的 2–4 字片段拿去打分,罕见片段(如「环境变量」)权重大,常见填充词(「这个」「问题」)分散低贡献。**对中文/英文/混合都有效、零依赖**。
- 无关输入 → 空输出不打扰;服务挂 → 空输出不阻塞。

**上下文预算**:L1 恒定 ~200 tokens;L2 只在命中时注入 ≤3 条(每条 ~60 字)。核心靠「加载准」而非「加载少」——记忆量增长不影响常驻成本,明细永远可用 MCP 工具按需深挖。

**settings 注册**(在 3.7 的 `d['hooks']` 里再加):

```python
d['hooks']['UserPromptSubmit'] = [
  {"hooks": [{"type": "command",
              "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/user_prompt.py",
              "timeout": 10}]}
]
```

### 3.7 合并 hooks 到 settings

`.claude/settings.local.json` 已存在(含 env + permissions.allow 302 条),**用 Python 合并**保留原有内容:

```python
import json
p = '.claude/settings.local.json'
d = json.load(open(p))
d['hooks'] = {
  "SessionStart": [{"matcher": "startup|resume",
     "hooks": [{"type": "command",
                "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/session_start.py",
                "timeout": 15}]}],
  "SessionEnd": [{"matcher": "*",
     "hooks": [{"type": "command",
                "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/session_end.py",
                "timeout": 15}]}]
}
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=2)
```

### 3.8 L3 LLM 蒸馏(走自建 ccr 代理)

会话结束时把 transcript 尾部经 `digest.run` + `digest.write` 蒸馏成实体写回 memory plane。**detached 后台进程**运行,不阻塞会话结束。

**为什么要改 dr-strange 代码**:`digest.run` 原本的 `chat` 只认 preset 名,不接受 base URL。改动:
- `methods.rs`:新增 `build_provider_flexible`,`chat`/`embed` 支持「preset 名 **或** base URL」;新增 `key_env`/`embed_key_env` 参数(指定环境变量名,key 本身仍只在服务端)。
- `openai.rs`:`MAX_OUTPUT_TOKENS` 8192→2048;`complete()` 加 `reasoning_effort:"none"`。

**为什么这两个改动缺一不可**(踩坑):
- `chat` 传 URL 后才能连 ccr(`http://127.0.0.1:3456/v1`)。
- 你的 ccr 上唯一可用模型是 **DeepSeek-v4-flash**(reasoning 模型)。它的 `reasoning_content` 会填满 8192 输出上限 → digest 报 `the model's reply hit the N-token output limit`,单次耗时 300-346s。
- 加 `reasoning_effort:"none"` 后 DeepSeek 不产 thinking,输出纯 JSON,digest **从 346s 降到 ~11s**,稳定落库 17 实体。

**L3 脚本**:`.claude/hooks/l3_digest.py`,由 session_end 派生(`start_new_session=True` 脱离进程组)。config:
```python
CHAT = "http://127.0.0.1:3456/v1"     # ccr OpenAI 端点
KEY_ENV = "CCR_API_KEY"               # serve 环境里的 key 变量名
MODEL = "DeepSeek/deepseek-v4-flash"
```
key **值**只在**一处**:**全局 `~/.drsg-memory/env`**(install.sh 写入;`serve.sh start` 把该文件导出进 daemon 进程 env)。`digest.run` 只传 key **名**(`key_env`),值由 daemon 从自身 env 读(`openai.rs build_provider`);项目 `.drsg/env` 只有名字,不存值。hooks 不做本地 key 检查(L3 触发只看 `DRSG_L3_CHAT` 非空 + transcript ≥40KB),daemon 缺 key 时 `digest.run` 报 401 "Invalid API key" 落在 `l3.log`。改 key 后必须 `scripts/memory-layer/serve.sh restart`。

**验证**:`digest.run` → `{chat_requests:3, entities:17, relations:19}`,`digest.write` → `{nodes_written:17, edges_written:19}`,节点带 `_model`/`_source`/`_run` provenance。

完整 L3 搭建教程(含缺陷修复、ccr 探测、排障)见 [`l3-llm-distillation-setup.md`](l3-llm-distillation-setup.md)。

## 4. 数据模型与读写方式

| 标签 | key | 含义 |
|---|---|---|
| `Project` | 项目目录名 | 每仓库一个 |
| `Session` | `session_id` | 每次 Claude Code 会话 |
| `Fact` | 自定义 | 跨会话值得保留的结论 |

**读**:会话启动自动注入;MCP `cypher` / `search` / `hybrid` / `ask` 查询 `plane:"memory"`。

**写 Fact**(推荐幂等写法,避免 `CREATE` 对已存在 key 报 `-32000 conflict`):

```bash
# node.create + edge.create(幂等路径,与 MCP write_nodes/write_edges 一致)
curl -sf -X POST http://127.0.0.1:7700/rpc -H "Authorization: Bearer $DRSG_TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"node.create","params":{"plane":"memory","key":"<fact-key>","labels":["Fact"],"properties":{"summary":"<内容>","created_at":<ts>}}}'
curl -sf -X POST http://127.0.0.1:7700/rpc -H "Authorization: Bearer $DRSG_TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"edge.create","params":{"plane":"memory","src":"<fact-key>","dst":"dr-strange","type":"ABOUT"}}'
```

## 5. 端到端验证清单

```bash
# 1) 守护进程
scripts/memory-layer/serve.sh status            # running + ok
# 2) RPC + token
curl ... db.stats                                # 200, result.persistent=true
# 3) MCP
claude mcp list                                  # drsg Connected
# 4) SessionStart 注入
echo '{"session_id":"t1","source":"startup","cwd":"<pwd>"}' | \
  CLAUDE_PROJECT_DIR=<pwd> .claude/hooks/session_start.py
#    → 输出含 additionalContext(有历史记忆时)
# 5) SessionEnd
echo '{"session_id":"t1","reason":"clear"}' | CLAUDE_PROJECT_DIR=<pwd> .claude/hooks/session_end.py
#    → node.get 该 session 应含 ended_at
```

## 6. 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| 会话启动慢 / hook 报错 | serve 没起 | `scripts/memory-layer/serve.sh status`;脚本已软降级,不阻塞 |
| curl 401 | token 不匹配 | 检查 `.drsg/env` 与 MCP headers 一致 |
| 新会话没有 `drsg` 工具 | MCP 会话启动时加载 | 重启会话;`claude mcp list` |
| "database is locked" | 第二个进程开了库 | 只留 serve;hook 一律 RPC 别用 CLI |
| 端口占用 | 7700 被占 | `--addr 127.0.0.1:7701`,同步脚本和 MCP url |
| 发布版二进制无 `/mcp` | 特性晚于 release | 从 master 本地构建(见 §2) |

## 7. 关键经验小结(已同步写入 memory plane)

1. `/mcp` 只在 master,发布版 v1.4.2 没有 → 必须本地构建。
2. native 后端一进程一库 → 共享 serve,hooks 走 RPC 不走 CLI。
3. 本机 `CC=/usr/local/bin/gcc` 无效 → 构建时 `CC=/usr/bin/gcc CXX=/usr/bin/g++`。
4. openCypher 子集:RETURN 必须命名模式**最后一个**变量 → 反向模式 `(p)<-[:T]-(s) RETURN s`。
5. 无 token 连读都拒 → hooks 与 MCP 全链路带 Bearer。
6. `claude mcp add --scope local` 写 `~/.claude.json` 项目条目(不进 git)。
7. 写 Fact 用 `node.create`/`edge.create`(幂等),别用裸 `CREATE`(key 冲突 -32000)。
8. SessionEnd hooks 共享 1.5s 预算 → 单次 RPC、2s 超时、失败无害。
9. BM25 分析器只支持英文,中文记忆召回用**字符 n-gram 反向匹配 + IDF**(见 3.6.2)。
10. 「写有价值记忆」分三层:L1 结构事实(脚本挖 transcript,无需 key)、L2 语义结论(模型经写记忆协议自动写)、L3 LLM 蒸馏(需 provider key)。
11. `digest.run` 的 `chat` 原本只认 preset 名,已改支持 base URL(连 ccr);新增 `key_env` 指定环境变量名,key 永不过 params。
12. DeepSeek-v4-flash 是 reasoning 模型,`reasoning_content` 填满 8192 输出上限 → 报 truncate、耗时 300s+。已在 `complete()` 加 `reasoning_effort:"none"`,digest 从 346s 降到 ~11s。

## 8. 开源方案对比与裁决(2026-08)

调研结论已同步 memory plane(`research-open-source-memory-2026`)。

**核心评判维度:跨 agent 共享记忆**(这是本项目最看重的需求)。结论先行:**社区主流方案在"多 agent 并发共享同一记忆"上都有明显短板,本方案的"单 serve 进程 + MCP over HTTP"恰好是少数天然支持共享的架构。**

### 8.1 社区主流方案(含共享能力)

| 方案 | 类型 | 捕获方式 | 存储/检索 | **跨 agent 共享** | 成本 | 成熟度 |
|---|---|---|---|---|---|---|
| **claude-mem** | 插件+worker | PostToolUse 每工具 + 5 hooks,AI 压缩 | SQLite FTS5 + Chroma | ❌ **并发缺陷**:每会话一个 stdio 进程,全打同一 SQLite → 10+ 会话 `database is locked`/损坏/每周重建(issue #752);无 HTTP 传输 | 免费;每次压缩调 Claude API | 极成熟(v13) |
| **官方 auto-memory** | 内置 | Claude 自动抽学习 | `~/.claude/projects/<proj>/memory/` | ❌ **单机单用户**:明确 per-machine、不跨机器 | 免费 | 官方 |
| **claude-mem-lite** | 插件 | 同 claude-mem | SQLite FTS5+TF-IDF | ❌ 同 claude-mem(stdio 多进程) | 免费 | 新 |
| **mcp-memory-graph / MeMesh** | MCP | hooks + 本地 MiniLM | SQLite + 知识图 | ⚠️ 单机;有 project/team scope 但无 HTTP 多客户端 | 免费 | 较新 |
| **mem0** | MCP/云 | 半自动,存偏好/事实 | 云 pgvector / Qdrant | ⚠️ **默认强隔离**(per-agent 命名空间),共享需刻意设 `user_id=project_id`;跨工具靠云 | 云 $99/月起 | 极知名(~58K stars) |

> **⚠️ 本机现状**:已装 **claude-mem v12.1.5** 且运行中(`~/.claude-mem/` 206MB,worker pid 3318)。它记"每工具操作日志";但按上文,它不适合多 agent 并发共享。

### 8.2 跨 agent 共享:各方案的真实能力

**这是需求的核心,值得单列。** 要实现"多个 agent(Claude Code 会话/其他 MCP 客户端)读写同一记忆",必须满足:

1. **并发安全**:多进程同时写不锁死、不损坏 → 需要单写者序列化(单进程)或强事务。
2. **统一入口**:多个客户端连同一个端点(MCP over HTTP),而非每个客户端各开一个进程。
3. **鉴权 + 变更通知**:token 控制访问,实时感知他人写入。

对照:

| 能力 | claude-mem | mem0 | 官方 auto-memory | **本方案** |
|---|---|---|---|---|
| 并发写安全 | ❌ stdio 多进程打 SQLite,10+ 锁 | ✅ 云托管 | ✅(但单机) | ✅ **单 serve 进程 + native MVCC 事务** |
| 统一端点 | ❌ 每会话一进程 | ✅ HTTP(云) | ❌ 文件系统 | ✅ **MCP over HTTP(127.0.0.1:7700/mcp)** |
| 多客户端 | ❌ 依赖 CLI 每会话 | ✅ | ❌ | ✅ 任意 MCP 客户端 |
| 鉴权 | ❌(无 token) | ✅ 云 key | ❌ | ✅ **Bearer token + Origin guard** |
| 变更通知 | ❌ | ❌ | ❌ | ✅ **WebSocket change feed** |
| 数据归属 | 本地 | 云(不在你手) | 本地 | **本地** |

**结论:在"跨 agent 共享记忆"这一点上,本方案的"单守护进程 + HTTP/MCP + token + change feed"是唯一同时满足全部 5 项能力的本地方案。** claude-mem 的架构(stdio 多进程)与共享天然冲突;mem0 能共享但靠云、且默认隔离;官方 auto-memory 根本不跨进程。

### 8.3 裁决:能否替代本方案?

**诚实回答:部分能,但没有任何方案能在"跨 agent 共享 + 知识图谱"上替代我们。**

**claude-mem 更成熟、可替代我们 L1 + 会话连续性**(如果只要单机单 agent 的工作日志):
- 每工具捕获粒度更细、全自动、渐进披露;已在本机运行。
- **但:并发共享是它的已知缺陷,且存扁平 observation 不建图。**

**本方案不可替代的差异点**:
- **跨 agent 共享**(§8.2):单 serve + HTTP/MCP + token + change feed,社区本地方案无出其右。
- **知识图谱**:Fact + 关系 + provenance + 图算法 + 时间旅行,社区不建"实体关联图"。
- **中文召回**:n-gram+IDF 对中文有效;社区多为英文 FTS5。
- **零 API 成本**:L3 走自建 ccr/DeepSeek;claude-mem 每次压缩调 Claude API。
- **数据本地**:不依赖云。

**官方 auto-memory**:零成本基线,记"约定",单机,互补非替代。

### 8.4 分层建议(以跨 agent 共享为第一优先)

1. **跨 agent 共享记忆 + 知识图谱**(本项目核心) → **本方案是唯一满足的**,保留。
2. 想要"单机单 agent 自动工作日志" → claude-mem 可作补充(L1 增强),但**别指望它做多 agent 共享**。
3. 想要"Claude 自动学习项目约定" → 官方 auto-memory 已默认开启,让它跑。

三者互补:claude-mem 记"过程"(单机)、auto-memory 记"约定"(单机)、**我们的图记"知识结构"且天然多 agent 共享**。若只留一个,**保留我们的方案**——它是唯一满足跨 agent 共享需求的。

> ⚠️ **自维护负担**:为接 ccr 改了两处核心(`digest.run` URL 支持 + `reasoning_effort:none`),是 fork 上游之外的自维护点,上游更新可能冲突。若未来想减少负担,可考虑把这些改动 PR 回上游(它们是通用改进)。

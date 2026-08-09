# L3 LLM 蒸馏搭建教程 — 会话自动浓缩成知识图谱

> 记录 2026-08-04 在 `/data/projects/maidol/dr-strange` 完成。目标:让 dr-strange 记忆层的 **L3 层**工作——会话结束时,后台把 transcript 经 LLM 蒸馏成实体/关系,自动写入 memory plane。走的是**自建 ccr 代理**(OpenAI 兼容端点)+ DeepSeek 模型,**无需额外申请 key**。

> ⚠️ **L3 已于 2026-08-08 在本项目关停**:它写的实体不挂 `ABOUT` 边,召回查不到,286/335 个节点一次没被读过。本文保留为完整的可复现记录(改回 `.drsg/env` 的 `DRSG_L3_CHAT` 即可重新启用),但**重新启用前应先解决「写进去的东西怎么被读到」**,否则只是重新开始烧 token。见 [`memory-layer-observability.md`](memory-layer-observability.md)。

## 1. 目标与架构

记忆层三层(见 `memory-layer-setup.md` §3.6.2):
- **L1 结构事实**:session_end 挖 transcript 得 `files_touched`/`commands_run`,脚本自动、无需 key。✅ 已就绪
- **L2 语义结论**:SessionStart 注入写记忆协议,模型自动写 Fact。✅ 已就绪
- **L3 LLM 蒸馏**:会话结束,后台把 transcript 浓缩成实体/关系落库。← **本文档**

```
Claude Code 会话结束
   │ SessionEnd hook(session_end.py,<1.5s 预算)
   ▼
 检测:transcript ≥ 40KB 且配置了 DRSG_L3_CHAT
   │ 派生(l3_digest.py,start_new_session=True 脱离进程组)
   ▼
 读 transcript 尾部(~4K 字符,去 tool 噪声)
   │
   ▼ digest.run(LLM 抽取,dry-run)
  ┌─────────────── ccr 代理 ───────────────┐
  │ POST http://127.0.0.1:3456/v1/chat/   │
  │   completions                          │
  │ model: DeepSeek/deepseek-v4-flash      │
  │ reasoning_effort: none                 │
  └───────────────────────────────────────┘
   │ 返回 {nodes:[{key,label,properties}], edges:[...]}
   ▼ digest.write(落库,无 LLM 调用)
  memory plane:实体带 _model/_source/_run provenance
```

## 2. 前置:为什么必须改 dr-strange 代码

### 缺陷 1:`digest.run` 连不上 ccr

`digest_run` 把 `chat` 参数直接当 **preset 名**传给 `build_provider`,而 `build_provider` 只认预设名(`openai`/`deepseek`/`qwen`/`ollama`)——传 URL 报 `unknown provider 'http://...'`。

**修复**(`crates/dr-strange-web/src/methods.rs`):
- 新增 helper `build_provider_flexible(spec, model, key_env, embed)`:`spec` 含 `://` 就当 base URL,否则当 preset 名。
- `DigestRun` 新增 `key_env`/`embed_key_env` 参数:指定**环境变量名**读 key(key 本身永不过 params,只在服务端)。
- `digest_run` 改用 helper。

```rust
fn build_provider_flexible(spec, model, key_env, embed, reasoning_effort) -> Result<OpenAiProvider, RpcError> {
    let provider = if spec.contains("://") {
        dr_strange_llm::build_provider("", model, Some(spec), key_env, embed)  // 空 preset + 显式 url
    } else {
        dr_strange_llm::build_provider(spec, model, None, key_env, embed)
    }?;
    Ok(match reasoning_effort {
        Some(re) => provider.with_reasoning_effort(re),
        None => provider,
    })
}
```

### 缺陷 2:DeepSeek reasoning 截断 + 极慢

连上后 digest 报 `the model's reply hit the 8192-token output limit and was cut off`,且单次耗时 **300-346 秒**。

**根因**:ccr 上唯一可用模型是 `DeepSeek/deepseek-v4-flash`——它是 **reasoning 模型**。digest 的完整系统提示 + 长文档让它产大量 `reasoning_content`(思考链),把 `MAX_OUTPUT_TOKENS=8192` 填满 → 输出被截断成无法解析的 JSON → digest 报错。

**真正的修复是关思考链,不是缩上限**。期间试过两版,最终落定:
- ❌ 尝试 1:缩 `MAX_OUTPUT_TOKENS` 8192→2048 → **仍截断**(还是思考链填满),说明问题是 reasoning 而非上限。
- ✅ 尝试 2:`reasoning_effort:"none"` 关思考链 → 输出纯 JSON(实测 out≈500-1700 tokens,远低于任何上限)→ 从 346s 降到 **~11s**。
- `MAX_OUTPUT_TOKENS` **保持 8192 未动**(还原了尝试 1 的改动)。

**实现**:`reasoning_effort` 做成 **provider 级可配置**,不硬编码:
- `openai.rs`:`OpenAiProvider` 加 `reasoning_effort: Option<String>` 字段(默认 `None`)+ `with_reasoning_effort()` builder。`complete()` 只在字段有值时发送该参数——未设置则行为完全不变。
- `methods.rs`:`DigestRun` 加 `reasoning_effort` 字段,`build_provider_flexible` 加参数透传。`build_provider` 签名未动(19 个调用方零波及)。
- 调用时配置:`digest.run` 请求传 `reasoning_effort:"none"` 即生效。

```rust
// openai.rs — provider 级字段,builder 设置
let mut body = json!({ "model": self.model, "temperature": 0, "max_tokens": MAX_OUTPUT_TOKENS, "messages": [...] });
if let Some(effort) = &self.reasoning_effort {
    body["reasoning_effort"] = Value::String(effort.clone());
}
```

**效果**:`digest.run` 传 `reasoning_effort:"none"` → 346s → **~11s**,稳定成功落库;不传则走 provider 默认,其他调用零影响。

## 3. 验证 ccr 是否可用(关键探测)

搭 L3 前,先确认你的自建代理能不能被 dr-strange 用:

```bash
# 1) 找 ccr 的 OpenAI 端点(不是 Claude 协议那个端口)
for p in 3456 3457 3459; do
  curl -s -o /dev/null -w ":$p /v1/models → %{http_code}\n" http://127.0.0.1:$p/v1/models -H "Authorization: Bearer $KEY"
done
# → 返回 401(Invalid API key)说明是 OpenAI 兼容端点;404 不是

# 2) 看可用模型(必须用代理认可的确切名字,如 sub2api/gemini-3.6-flash)
curl -s http://127.0.0.1:3456/v1/models -H "Authorization: Bearer $KEY" | python3 -m json.tool

# 3) 逐模型测能否短输出(很多模型会 403/502/不可达)
for m in <model1> <model2>; do
  curl -s http://127.0.0.1:3456/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"say OK\"}],\"max_tokens\":10}"
done
```

**经验**:
- 模型名必须用代理列出的**确切 id**(如 `DeepSeek/deepseek-v4-flash`),别猜 `gemini-2.0-flash`(会 400)。
- 常见坑:`wbgpt/*` 仅限特定客户端(403)、`uniin/*` 上游不可达(502)。挑一个能稳定短输出的。
- **reasoning 模型**要测 `reasoning_content` 是否撑爆输出上限;用 `reasoning_effort:"none"` 可关。

## 4. 步骤

### 4.1 建 L3 脚本

`.claude/hooks/l3_digest.py` — 读 transcript 尾部 → digest.run → digest.write。要点:
- **detached**:由 session_end 派生(`start_new_session=True` 脱离 Claude 进程组),自己写 `.drsg/l3.log`。
- **config**:
  ```python
  CHAT            = "http://127.0.0.1:3456/v1"   # ccr OpenAI 端点
  KEY_ENV          = "CCR_API_KEY"                # serve 环境里的 key 变量名
  MODEL            = "DeepSeek/deepseek-v4-flash"
  REASONING_EFFORT = "none"   # 关思考链;设 "" 恢复 provider 默认推理
  ```
- 调用 `digest.run` 时传:`chat=CHAT, model=MODEL, key_env=KEY_ENV, reasoning_effort=REASONING_EFFORT, no_embed=true, link=false, mode="coarse"`。
  - `no_embed:true` — ccr 无 embeddings(实测 `/v1/embeddings` 失败)。
  - `link:false` — 跳过向量链接候选。
  - `reasoning_effort:"none"` — 关 DeepSeek 思考链(见 §2 缺陷 2),可配置、可空。
- 失败一律软降级(写 log、exit 0)。

### 4.2 session_end 派生 L3

`session_end.py` 末尾(盖完 `ended_at` + L1 挖完后):

```python
if l3_chat and transcript_path \
        and os.path.exists(transcript_path) \
        and os.path.getsize(transcript_path) >= L3_MIN_TRANSCRIPT:   # 40KB
    subprocess.Popen([sys.executable, script, sid, transcript_path],
        start_new_session=True, cwd=proj_dir,
        stdout=logf, stderr=subprocess.STDOUT, close_fds=True)
```

> 不再检查本地 key 值:`digest.run` 只传 key 名,值由 daemon 从自身 env 读;daemon 缺 key 时 `digest.run` 报 401 落在 `l3.log`(可见)。

**永远不阻塞会话结束**:只是 Popen 一下就返回。

### 4.3 配置 key 并重启 serve

key **值**只写**全局 daemon env**(`~/.drsg-memory/env`,install.sh 自动写入;`serve.sh start` 会把该文件导出进 daemon 进程 env)。项目 `.drsg/env` **不存 key 值**,只有名字 `DRSG_L3_KEY_ENV=CCR_API_KEY`。

```
# ~/.drsg-memory/env 追加(唯一存值处,权限 600)
CCR_API_KEY=<你的 ccr key>
```

**必须重启 serve**(`scripts/memory-layer/serve.sh restart`)让新 key 和新二进制生效——daemon 只在自己启动时读一次 env。

### 4.4 验证

```bash
# 1) digest.run 直测(短文本,应快速成功)
curl -X POST http://127.0.0.1:7700/rpc -H "Authorization: Bearer $DRSG_TOKEN" -H 'Content-Type: application/json' \
  -d '{"method":"digest.run","params":{"plane":"memory","text":"<一段话>","chat":"http://127.0.0.1:3456/v1","model":"DeepSeek/deepseek-v4-flash","key_env":"CCR_API_KEY","reasoning_effort":"none","no_embed":true,"link":false,"mode":"coarse"}}'
# → report.chat_requests>0, nodes/edges 非空

# 2) 完整脚本(配置从 .drsg/env 读;key 在 daemon 侧,无需传)
python3 .claude/hooks/l3_digest.py <session_id> <transcript_path>
tail .drsg/l3.log   # → digest.write done: {nodes_written, edges_written}

# 3) 验证落库(带 provenance)
curl -X POST ... -d '{"method":"plane.cypher","params":{"plane":"memory","query":"MATCH (f) WHERE f._source = '\''session:<id>'\'' RETURN f"}}'
```

## 5. 排障

| 现象 | 根因 | 处理 |
|---|---|---|
| `unknown provider 'http://...'` | digest chat 不认 URL | 用改过的二进制(§2 缺陷 1) |
| `reply hit the N-token output limit` | reasoning 模型思考链撑爆 | `digest.run` 传 `reasoning_effort:"none"`(§2 缺陷 2) |
| 缩 2048 后仍截断 | 问题是 reasoning 非上限 | 还原 8192,改关思考链(§2 缺陷 2) |
| digest.run 耗时 300s+ | reasoning 思考链 | 传 `reasoning_effort:"none"`,降到 ~11s |
| `digest.run` 401 "Invalid API key" | daemon 进程 env 缺 key(旧 env 启动) | key 写入 `~/.drsg-memory/env` 后 `serve.sh restart` |
| 脚本找不到 `.drsg/l3.log` | DRSG_DIR 路径算错 | 项目根 = `dirname(dirname(dirname(__file__)))` + `.drsg` |
| ccr 上无可用模型 | 多数 403/502/不可达 | 逐模型测,选能短输出的(§3) |
| 落库 key 冲突 | 实体 key 撞已有 | 调 `mode:"coarse"` 或 `link:false` 降低复用 |

## 6. 关键经验(已写入 memory plane)

1. `digest.run` 的 `chat` 原本只认 preset 名 → 已改支持 base URL;新增 `key_env` 指定环境变量名。
2. DeepSeek-v4-flash 是 reasoning 模型,`reasoning_content` 填满 8192 → 截断 + 300s。**截断的真解是关思考链(`reasoning_effort:"none"`,降到 ~11s),不是缩上限**——缩到 2048 仍截断,已还原 8192。`reasoning_effort` 做成 provider 级可配置(`with_reasoning_effort`,默认不发)。
3. ccr 上模型名必须用确切 id(`DeepSeek/deepseek-v4-flash`),`wbgpt/*` 403、`uniin/*` 502 不可用。
4. ccr 无 embeddings → digest 传 `no_embed:true, link:false`。

## 7. 相关文件

- `crates/dr-strange-web/src/methods.rs` — digest URL + `key_env`/`reasoning_effort` 支持
- `crates/dr-strange-llm/src/openai.rs` — `with_reasoning_effort` builder(可选,默认不发);`MAX_OUTPUT_TOKENS` 保持 8192
- `.claude/hooks/l3_digest.py`、`.claude/hooks/session_end.py`
- `~/.drsg-memory/env` — `CCR_API_KEY`(key 值唯一存处,daemon env);项目 `.drsg/env` 只存名字 `DRSG_L3_KEY_ENV`

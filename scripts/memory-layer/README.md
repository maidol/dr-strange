# Long-term memory layer for Claude Code

A one-command install that turns Dr Strange into persistent, cross-session
memory for [Claude Code](https://claude.com/claude-code) projects: the graph
remembers what earlier sessions concluded, and every new session starts with a
briefing instead of a blank slate.

The whole thing is four Python hooks, an installer, and a daemon control
script. No plugin, no service to sign up for, no data leaving the machine.

## Architecture

```
any Claude Code project                one shared daemon (~/.drsg-memory/)
┌────────────────────────────┐        ┌──────────────────────────────┐
│ .claude/hooks/ (4 scripts) │──RPC──▶│ a single `drsg serve`        │
│   session_start / end      │        │  holding memory.drsg         │
│   user_prompt / l3_digest  │        │  one Project node per repo   │
│ .drsg/env (points at it)   │        │  addr 127.0.0.1:7700         │
│ settings.local.json hooks  │        └──────────────────────────────┘
│ MCP: drsg @ /mcp           │──HTTP──▶ (several agents, one database)
└────────────────────────────┘
```

- **Shared, not per-project.** One daemon owns every project's memory. The
  native backend allows one process per database, so a daemon is the only way
  several agents — or several editors — can read and write one graph at once.
- **Separated, not mixed.** Each project is a `Project` node; Facts hang off it
  with `ABOUT` edges. Recall filters on the project's `path`.
- **Three ways in.** L1: structural facts the hooks mine from the transcript.
  L2: conclusions the model writes itself, following a protocol injected at
  session start. L3: LLM distillation of the transcript tail (optional).
- **Two ways out.** SessionStart injects a compressed briefing for this
  project; UserPromptSubmit recalls Facts matching what you just typed, across
  *all* projects, labelled with where they came from.

## Prerequisites

- A `drsg` binary. `serve.sh` starts it; pass `--bin` or set `$DRSG_MEM_BIN`.
- **The `/mcp` endpoint on `drsg serve`**, for the MCP registration step. The
  hooks themselves only need `/rpc` and work without it.
- Python 3, `curl`, and `openssl` (token generation).

## Install

```bash
# from this directory
./install.sh /path/to/project --bin /path/to/drsg
```

**Restart the Claude Code session afterwards** — hooks and MCP servers are read
at startup.

L3 distillation is off unless you name a chat provider:

```bash
./install.sh /path/to/project --bin /path/to/drsg \
  --l3-chat openai            # or deepseek / qwen / ollama
```

`--l3-chat` also takes a raw OpenAI-compatible base URL — a self-hosted proxy,
say — but only against a daemon whose `digest.run` accepts one; with the preset
names above, any daemon will do.

## Joining an existing daemon

If something already answers `/health` at the target address, `install.sh`
**joins** it rather than starting a second one. Every project then lands in the
same `memory` plane, separated by its `Project` node, and recall can reach
across them. Joining requires that daemon's token:

```bash
./install.sh /path/to/other-project --bin /path/to/drsg \
  --addr 127.0.0.1:7700 --token <the daemon's token>
```

To move a project that was installed against its *own* daemon, migrate its
memory first:

```bash
# 1. export from the old daemon
python3 migrate.py dump --api http://127.0.0.1:7701/rpc --token <old token> \
  --plane memory --out project-memory.json
# 2. import into the shared one (deduplicates by external key, never overwrites)
python3 migrate.py load --api http://127.0.0.1:7700/rpc --token <new token> \
  --plane memory --in project-memory.json
# 3. re-run the installer against the shared address (idempotent)
./install.sh /path/to/other-project --bin /path/to/drsg \
  --addr 127.0.0.1:7700 --token <new token>
# 4. once you've confirmed it, stop the old daemon
./serve.sh stop
```

Back up both databases first — stop the daemon, then copy the directory.
`migrate.py` warns about and skips nodes with no external key; a `dump` will
tell you whether you have any.

## Options

| Option | Default | Meaning |
|---|---|---|
| `--bin <path>` | `$DRSG_MEM_BIN` or `drsg` | the binary to run |
| `--addr <host:port>` | `127.0.0.1:7700` | daemon listen address |
| `--token <t>` | reused or generated | shared API token; **required when joining** an existing daemon |
| `--l3-chat <name\|url>` | empty (L3 off) | preset name or OpenAI-compatible base URL |
| `--l3-key-env <v>` | the preset's own | **name** of the env var holding the LLM key (see below) |
| `--l3-model <m>` | the provider's own | model id, exactly as the endpoint lists it |
| `--l3-reasoning <e>` | unset | `reasoning_effort`; `none` stops a reasoning model truncating the JSON |
| `--restart-daemon` | off | stop an existing daemon first (new token or address) |

## Running the daemon

```bash
./serve.sh start|stop|restart|status
# overrides: DRSG_MEM_DIR / DRSG_MEM_BIN / DRSG_MEM_ADDR / DRSG_MEM_TOKEN
# BIN and ADDR fall back to the values persisted in the env file, so a bare
# `restart` works with no arguments.
```

State lives in `~/.drsg-memory/`: `memory.drsg`, `env`, `serve.log`,
`serve.pid`.

**Changing the L3 key needs a daemon restart.** `start` exports the env file's
`KEY=VALUE` pairs into the daemon's own process environment; a daemon already
running under the old environment will keep sending an empty key and
`digest.run` will come back 401. Run `./serve.sh restart` after editing it.

## What an install actually does

1. Ensures the shared daemon is running, generating or reusing a token. With
   L3 enabled, the key's **value** is written to `~/.drsg-memory/env` *before*
   the daemon starts, so `start` can export it.
2. Copies the four hooks into `<project>/.claude/hooks/`.
3. Writes `<project>/.drsg/env` (`chmod 600`) — daemon address, token, and the
   L3 settings, with the key's **name** only.
4. Merges the SessionStart / UserPromptSubmit / SessionEnd entries into
   `<project>/.claude/settings.local.json`, keeping whatever is already there.
5. Registers the `drsg` MCP server (project scope) against the daemon's `/mcp`.
6. **Self-checks**: daemon reachable, plane present, the project key resolving
   to a `Project` node, and a temporary Fact readable through the hooks' own
   recall query. Any failure exits non-zero and says so, rather than reporting
   a successful install of something that will silently do nothing.

Add `.drsg/` to the target project's `.gitignore`.

## Notes

- **The LLM key never leaves the server.** `digest.run` is passed the *name* of
  an environment variable; the daemon reads the value from its own process
  environment. The project's `.drsg/env` holds the name, never the value.
- **L3 is opt-in and asynchronous.** With `--l3-chat` set, `session_end`
  spawns the distillation detached, so ending a session never waits on an LLM
  call. Failures land in the project's `.drsg/l3.log`.
- **Hooks are overwritten, not merged.** A project with its own SessionStart
  hook will lose it. Back it up first.
- **One config per project.** Each `.drsg/env` is independent: different
  projects can enable L3 differently, or point at different daemons.

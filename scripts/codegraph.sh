#!/usr/bin/env bash
# Code-graph daemon control: `drsg serve watch` over a repository's graph.drsg,
# folding every commit into that repo's plane and exposing the seven verbs on
# /mcp. Works for any repository, not just this one — `--dir` (default: the
# git repository containing $PWD) picks the target.
#
# One daemon per repository, deliberately: `serve watch` takes a single --dir,
# and the native backend allows one process per database. So each repo gets its
# own db, its own port and its own token, all recorded in its own .mcp.json.
#
# NOT the memory layer. That is a separate global daemon on 7700 with its own
# controller (scripts/memory-layer/serve.sh) and its own db in ~/.drsg-memory.
# No database is ever shared, so restarting either leaves the other alone.
#
# The bootstrap (minting a token, writing .mcp.json and the editor configs)
# belongs to `drsg init` and is not duplicated here: this script reads the
# address and token back out of .mcp.json, which is what MCP clients read too,
# so the daemon and the clients cannot drift apart.
#
# Env (defaults shown):
#   DRSG_CODE_BIN    the watched repo's target/release/drsg, else this
#                    checkout's, else `drsg` on PATH
#   DRSG_CODE_DB     <repo>/graph.drsg        the db *directory* (native backend)
#   DRSG_CODE_PLANE  the repo's own name      plane to keep in sync
#
# Usage: codegraph.sh {start|stop|restart|status|logs} [--dir PATH] [--port N] [--force]
#
#   --dir PATH  repository to serve (default: the one $PWD is in)
#   --port N    listen on 127.0.0.1:N instead of the port .mcp.json records.
#               `start`/`restart` persist the new port into .mcp.json, because
#               a daemon the clients cannot reach is worse than no daemon.
#   --force     rebuild the plane from the whole tree before serving (drop,
#               re-create, fold every file). Only needed after changing which
#               plugins are installed; a normal start catches up incrementally
#               from the plane's synced_commit, which takes about a second.
set -euo pipefail

SELF_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cmd="${1:-status}"
shift || true
target="$PWD"
force=""
port=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dir)   target="${2:?--dir needs a path}"; shift 2 ;;
    --port)  port="${2:?--port needs a number}"; shift 2 ;;
    --force) force="--force"; shift ;;
    *) echo "unknown argument '$1'" >&2; exit 1 ;;
  esac
done
case "$port" in
  ''|*[!0-9]*) [ -z "$port" ] || { echo "ERROR: --port wants a number, got '$port'" >&2; exit 1; } ;;
esac

# The repository root, not whatever subdirectory the caller happened to be in:
# the plane's name and the db's location both hang off it.
REPO="$(git -C "$target" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO" ]; then
  echo "ERROR: '$target' is not inside a git repository — 'serve watch' follows commits, so it needs one." >&2
  exit 1
fi

# Where drsg is, in the order that stays true wherever this script is run from.
# `$SELF_REPO` assumes the script sits in a checkout's `scripts/` — which stops
# being true the moment a copy is kept outside the repository (a copy exists
# precisely because a branch that does not track this file deletes it). The
# target repo is tried first for the same reason: with `--dir`, the repo being
# watched is the one the caller named, and its own build is the binary they
# most likely meant. Falls back to PATH for an installed one.
if [ -n "${DRSG_CODE_BIN:-}" ]; then
  BIN="$DRSG_CODE_BIN"
elif [ -x "$REPO/target/release/drsg" ]; then
  BIN="$REPO/target/release/drsg"
elif [ -x "$SELF_REPO/target/release/drsg" ]; then
  BIN="$SELF_REPO/target/release/drsg"
else
  BIN="drsg"
fi
DB="${DRSG_CODE_DB:-$REPO/graph.drsg}"
# Matches drsg's own default_plane(): the source directory's name.
PLANE="${DRSG_CODE_PLANE:-$(basename "$REPO")}"
MCP_JSON="$REPO/.mcp.json"

# Runtime files live outside the target repository. `drsg init` teaches its
# .gitignore about *.drsg, logs/ and .mcp.json but nothing else, so a pid file
# dropped inside would show up as untracked noise in someone else's checkout.
# The path is hashed because two repos may share a basename.
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/drsg/codegraph/$(basename "$REPO")-$(printf %s "$REPO" | sha256sum | cut -c1-8)"
PID="$STATE/pid"
LOG="$STATE/log"

# Address and token both come from .mcp.json, the file the MCP clients read.
# Regenerating a token here would silently invalidate every client config that
# `drsg init` wrote (Cursor, Codex, ...), so a missing entry is an error with
# the one command that fixes it, not something to paper over.
#
# Sets CFG_ADDR (what the clients currently believe) and ADDR (where we are
# about to act). `--port` is the only thing that makes them differ.
read_config() {
  if [ ! -f "$MCP_JSON" ]; then
    echo "ERROR: no $MCP_JSON — run \`$BIN init\` in $REPO first (it mints the token and writes the client configs)." >&2
    exit 1
  fi
  eval "$(python3 - "$MCP_JSON" <<'PY'
import json, sys, urllib.parse
try:
    entry = json.load(open(sys.argv[1]))["mcpServers"]["drsg-watch"]
    u = urllib.parse.urlparse(entry["url"])
    print("CFG_ADDR=%s:%d" % (u.hostname, u.port))
    print("TOKEN=%s" % entry["headers"]["Authorization"].split()[-1])
except Exception as e:
    print("CFG_ADDR=; TOKEN=; CFG_ERR=%r" % (str(e),))
PY
)"
  if [ -z "${CFG_ADDR:-}" ]; then
    echo "ERROR: $MCP_JSON has no usable 'drsg-watch' entry (${CFG_ERR:-}) — run \`$BIN init\` in $REPO." >&2
    exit 1
  fi
  # Loopback stays loopback: the token is the only guard, so a port override
  # must not quietly widen the bind address.
  if [ -n "$port" ]; then ADDR="127.0.0.1:$port"; else ADDR="$CFG_ADDR"; fi
}

# Rewrite only the `drsg-watch` URL, leaving the token and every other server
# entry alone. Called after a successful start on a port the file did not name.
persist_addr() {
  python3 - "$MCP_JSON" "$1" <<'PY'
import json, sys, urllib.parse
path, addr = sys.argv[1], sys.argv[2]
doc = json.load(open(path))
entry = doc["mcpServers"]["drsg-watch"]
u = urllib.parse.urlparse(entry["url"])
entry["url"] = u._replace(netloc=addr).geturl()
with open(path, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
PY
}

# The pid of the daemon serving THIS repository, whatever port it is on.
#
# Identity is the database's LOCK file, held open for the process's lifetime by
# the native backend. Two weaker identities were tried and are wrong:
#
#   - the port: `restart --port N` must stop a daemon that is by definition on
#     the *old* address, and an occupied port may be occupied by anything;
#   - the command line: `drsg init` spawns its daemon with `--dir .` and
#     `--db ./graph.drsg`, relative to a cwd the pattern never sees, so
#     matching on the repository path misses exactly the daemons init started
#     — and then a start opens a database that is already open, which is the
#     one error this lookup exists to prevent.
#
# The lock is also the resource that actually matters: one process per db.
db_holder_pid() {
  local lock
  lock="$(readlink -f "$DB/LOCK" 2>/dev/null || true)"
  [ -n "$lock" ] || return 0
  # One `find` rather than a readlink per fd: /proc has a few thousand of them.
  find /proc/[0-9]*/fd -maxdepth 1 -lname "$lock" -printf '%h\n' 2>/dev/null |
    sed -n 's|/proc/\([0-9]*\)/fd|\1|p' | head -1
  # Found nothing is a normal answer, not a failure: callers test for empty.
  return 0
}

# What a pid is listening on, and who holds an address — the two directions of
# the same `ss` table.
pid_addr()     { ss -ltnp 2>/dev/null | awk -v p="pid=$1," '$0 ~ p {print $4; exit}'; }
addr_holder()  { ss -ltnp 2>/dev/null | awk -v a="$1" '$4 == a {sub(/.*pid=/, ""); sub(/,.*/, ""); print; exit}'; }

healthy() { curl -sf -m 2 "http://$ADDR/health" >/dev/null 2>&1; }

start() {
  read_config
  if [ ! -x "$BIN" ] && ! command -v "$BIN" >/dev/null 2>&1; then
    echo "ERROR: no drsg binary at '$BIN' — build it (\`cargo build --release -p dr-strange-cli\` in $SELF_REPO) or set DRSG_CODE_BIN." >&2
    exit 1
  fi
  local mine; mine="$(db_holder_pid)"
  if [ -n "$mine" ]; then
    echo "already running (pid $mine) on $(pid_addr "$mine") — use restart${port:+ --port $port} to move it"
    return 0
  fi
  # Someone else's process on the port we want: say so, rather than failing
  # later with a bind error buried in the log.
  local squatter; squatter="$(addr_holder "$ADDR")"
  if [ -n "$squatter" ]; then
    echo "ERROR: $ADDR is already taken by pid $squatter ($(tr '\0' ' ' < "/proc/$squatter/cmdline" 2>/dev/null | cut -c1-90)) — pick another --port." >&2
    exit 1
  fi
  mkdir -p "$STATE"
  # setsid so the daemon outlives the shell that ran this script.
  DRSG_TOKEN="$TOKEN" setsid nohup "$BIN" --db "$DB" serve --addr "$ADDR" \
      watch --dir "$REPO" --plane "$PLANE" ${force:+$force} \
      >> "$LOG" 2>&1 < /dev/null &
  for _ in $(seq 1 40); do healthy && break; sleep 0.5; done
  local pid; pid="$(db_holder_pid)"
  if [ -z "$pid" ] || ! healthy; then
    echo "ERROR: never started listening on $ADDR. Last lines of $LOG:" >&2
    tail -20 "$LOG" >&2
    exit 1
  fi
  echo "$pid" > "$PID"
  echo "started pid $pid — http://$ADDR/mcp, repo=$REPO, db=$DB, plane=$PLANE${force:+ (rebuilt)}"
  if [ "$ADDR" != "$CFG_ADDR" ]; then
    persist_addr "$ADDR"
    echo "  .mcp.json: $CFG_ADDR → $ADDR (token unchanged)"
    # The other client configs `drsg init` may have written are not rewritten
    # here — they are four different file formats — so name the ones that
    # exist and still point at the old port.
    local f stale=()
    for f in .cursor/mcp.json .opencode.json .gemini/settings.json .codex/config.toml; do
      if [ -f "$REPO/$f" ] && grep -qF "${CFG_ADDR##*:}" "$REPO/$f" 2>/dev/null; then
        stale+=("$f")
      fi
    done
    if [ ${#stale[@]} -gt 0 ]; then
      echo "  still on the old port: ${stale[*]} — \`$BIN init --addr $ADDR --token <the same token>\` rewrites them all (it also force-rebuilds the plane)"
    fi
  fi
  # The one line worth reading: whether the plane actually caught up to HEAD.
  grep -aE 'in sync|folded|watching repository' "$LOG" | tail -2 || true
}

stop() {
  read_config
  # By repository, not by port: `restart --port N` must stop the daemon that is
  # running now, which is by definition on the *old* address.
  local pid; pid="$(db_holder_pid)"
  [ -z "$pid" ] && [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null && pid="$(cat "$PID")"
  if [ -z "$pid" ]; then
    rm -f "$PID"
    echo "not running"
    return 0
  fi
  local was; was="$(pid_addr "$pid")"
  kill "$pid" 2>/dev/null || true
  # Graceful first, but an open MCP stream keeps the server draining while it
  # still holds the db lock, which would make the next start fail. Escalate;
  # the WAL replays on the next open, so a hard kill loses nothing committed.
  for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
  if kill -0 "$pid" 2>/dev/null; then
    echo "   (force-killing pid $pid — still draining)" >&2
    kill -9 "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
  fi
  rm -f "$PID"
  echo "stopped (was pid $pid${was:+ on $was})"
}

status() {
  read_config
  local pid; pid="$(db_holder_pid)"
  if [ -z "$pid" ]; then
    echo "not running ($REPO: nothing holds its database; .mcp.json says $CFG_ADDR)"
    return 1
  fi
  ADDR="$(pid_addr "$pid")"
  echo "running (pid $pid) on http://$ADDR/mcp — $REPO"
  if [ "$ADDR" != "$CFG_ADDR" ]; then
    echo "  WARNING: .mcp.json points at $CFG_ADDR — clients are looking at the wrong port"
  fi
  healthy && echo "  health: ok" || echo "  health: FAILING"
  # Ask the server itself what the plane knows, rather than trusting the log:
  # `synced` names the commit the graph was folded up to.
  curl -sf -m 5 -X POST "http://$ADDR/rpc" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"plane.list","params":{}}' |
    python3 -c 'import json,sys; r=json.load(sys.stdin).get("result",{}); print("  planes:", json.dumps(r, ensure_ascii=False)[:400])' 2>/dev/null || true
  echo "  repo HEAD: $(git -C "$REPO" rev-parse --short HEAD)"
}

case "$cmd" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  logs)    tail -f "$LOG" ;;
  *) echo "usage: $0 {start|stop|restart|status|logs} [--dir PATH] [--port N] [--force]"; exit 1 ;;
esac

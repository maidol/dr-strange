#!/usr/bin/env bash
# install.sh — one-click install of the dr-strange long-term memory layer into
# a Claude Code project. Installs a GLOBAL shared daemon + this project's hooks.
#
# Usage:
#   ./install.sh [project-dir] [options]
#
#   project-dir   the target project (default: current directory)
#
# Options:
#   --bin <path|name>   drsg binary (patched; default: DRSG_MEM_BIN or `drsg`)
#   --addr <host:port>  daemon listen address (default 127.0.0.1:7700)
#   --token <t>         shared API token (default: reuse or generate)
#   --l3-chat <url>     OpenAI-compatible chat endpoint for L3 distillation
#                       (omit/empty to disable L3)
#   --l3-key-env <v>    env var name holding the LLM key (default: the
#                       preset's own, e.g. OPENAI_API_KEY). Its VALUE is
#                       persisted into the daemon's env file only — the
#                       project .drsg/env holds just the NAME, and the var
#                       must be exported in the install environment. Leave
#                       unset with a preset: the daemon already has its key.
#   --l3-model <m>      model id (default: the provider's own)
#   --l3-reasoning <e>  reasoning_effort to send, e.g. "none" to stop a
#                       reasoning model truncating the JSON (default: unset)
#   --restart-daemon    stop any existing global daemon first (new token/addr)
#   -h, --help
#
# What it does:
#   1. Ensure the global daemon is running (start if not).
#   2. Copy parameterized hook templates into <proj>/.claude/hooks/.
#   3. Write <proj>/.drsg/env (points at the daemon, project-agnostic).
#   4. Merge SessionStart/UserPromptSubmit/SessionEnd hooks into
#      <proj>/.claude/settings.local.json (preserves existing content).
#   5. Register the drsg MCP server (project scope) at the daemon's /mcp.
#
# After install: restart Claude Code so hooks + MCP load.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATES="$SCRIPT_DIR/templates"
SERVE="$SCRIPT_DIR/serve.sh"

# First arg is the target project dir unless it's an option.
if [ "$#" -gt 0 ] && [[ "$1" != -* ]]; then
  mkdir -p "$1"
  PROJECT_DIR="$(cd "$1" && pwd)"; shift
else
  PROJECT_DIR="$(pwd)"
fi
# Project slug = dirname; used as the Project node's external key. Recall
# addresses the project by `path`, but the key still has to resolve to a
# Project node — key-addressed writes (node.create, edge.create) go through it.
SLUG="$(basename "$PROJECT_DIR")"

BIN="${DRSG_MEM_BIN:-drsg}"
ADDR="${DRSG_MEM_ADDR:-127.0.0.1:7700}"
PLANE="memory"
TOKEN=""
L3_CHAT=""
L3_KEY_ENV=""
L3_MODEL=""
L3_REASONING=""
RESTART_DAEMON=0

# Anchored on the last option rather than a line number: the header grows, and
# a stale number truncates the help text silently instead of failing.
usage() { sed -n '2,/^#   -h, --help$/p' "${BASH_SOURCE[0]}"; }

# ---- arg parsing -----------------------------------------------------------
while [ "$#" -gt 0 ]; do
  case "$1" in
    --bin) BIN="$2"; shift 2 ;;
    --addr) ADDR="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --l3-chat) L3_CHAT="$2"; shift 2 ;;
    --l3-key-env) L3_KEY_ENV="$2"; shift 2 ;;
    --l3-model) L3_MODEL="$2"; shift 2 ;;
    --l3-reasoning) L3_REASONING="$2"; shift 2 ;;
    --restart-daemon) RESTART_DAEMON=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

# ---- 0. resolve + persist the L3 LLM key --------------------------------------
# L3 sends `key_env` (a NAME) to the daemon, and the daemon reads the key VALUE
# from ITS OWN environment (openai.rs build_provider). So the key value is
# persisted ONLY to the global daemon env file — BEFORE
# starting the daemon, so serve.sh exports it into the daemon process on launch.
# It is NOT written into the project .drsg/env (the hooks only pass the key NAME).
# Resolved the same way serve.sh resolves it: an install run with
# DRSG_MEM_DIR set would otherwise write the key to one file and read the
# token from another, and the self-check would fail on a working daemon.
DAEMON_ENV="${DRSG_MEM_DIR:-$HOME/.drsg-memory}/env"
L3_KEY_VALUE=""
if [ -n "$L3_CHAT" ] && [ -z "$L3_KEY_ENV" ]; then
  # A preset resolves its own key env (OPENAI_API_KEY for `openai`, and so on)
  # inside the daemon. Nothing for us to persist — but say so, because a daemon
  # started without that key fails at digest.run time, not here.
  echo "== L3 enabled with no --l3-key-env: the daemon must already carry the"
  echo "   provider's own key in its environment (see its preset)."
elif [ -n "$L3_CHAT" ]; then
  L3_KEY_VALUE="${!L3_KEY_ENV:-}"
  if [ -z "$L3_KEY_VALUE" ]; then
    echo "ERROR: --l3-chat given but '$L3_KEY_ENV' is not set in this environment." >&2
    echo "       Export it first, e.g.:  export $L3_KEY_ENV=sk-…" >&2
    echo "       or drop --l3-chat to disable L3." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$DAEMON_ENV")"
  python3 - "$DAEMON_ENV" "$L3_KEY_ENV" "$L3_KEY_VALUE" <<'PYEOF'
import sys, os
path, k, v = sys.argv[1], sys.argv[2], sys.argv[3]
lines = [ln.rstrip("\n") for ln in open(path, encoding="utf-8")] if os.path.exists(path) else []
lines = [ln for ln in lines if not ln.startswith(k + "=")]
lines.append(f"{k}={v}")
with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
os.chmod(path, 0o600)
PYEOF
  echo "== persisted L3 key '$L3_KEY_ENV' into $DAEMON_ENV (daemon reads it server-side)"
fi

# ---- 1. ensure the target daemon --------------------------------------------
# Two modes:
#   * fresh daemon  — nothing answers /health at $ADDR → start one via serve.sh.
#   * join existing — something already serves $ADDR → REUSE it (this is how
#                     several projects share one daemon / one db — the whole
#                     point of "cross-project query"). We never spawn a second
#                     process on a port that's already up (native backend is
#                     one process per db; a second one would fail to lock).
#                     Joining REQUIRES --token to be the RUNNING daemon's
#                     token — that can't be guessed, so it must be passed.
HTTP_BASE="$(echo "$ADDR" | sed 's|^https\?://||')"
JOINED=0
if curl -sf -m 2 "http://$HTTP_BASE/health" >/dev/null 2>&1; then
  JOINED=1
  echo "== daemon already running at $ADDR — joining (no new process started)"
  if [ -z "$TOKEN" ]; then
    echo "ERROR: joining a running daemon requires --token <its token>." >&2
    echo "       (the hooks/MCP must authenticate with the RUNNING daemon's" >&2
    echo "       token, which cannot be guessed from this script)" >&2
    exit 1
  fi
else
  if [ "$RESTART_DAEMON" = "1" ]; then
    "$SERVE" stop >/dev/null 2>&1 || true
  fi
  export DRSG_MEM_BIN="$BIN" DRSG_MEM_ADDR="$ADDR"
  if [ -n "$TOKEN" ]; then export DRSG_MEM_TOKEN="$TOKEN"; fi
  echo "== ensuring global daemon ($ADDR)…"
  "$SERVE" start
  # Discover the daemon's token (serve.sh persisted it).
  DAEMON_ENV="${DRSG_MEM_DIR:-$HOME/.drsg-memory}/env"
  if [ -f "$DAEMON_ENV" ] && grep -q '^DRSG_TOKEN=' "$DAEMON_ENV"; then
    TOKEN="$(grep '^DRSG_TOKEN=' "$DAEMON_ENV" | head -1 | cut -d= -f2-)"
  fi
  if [ -z "$TOKEN" ]; then echo "ERROR: could not determine daemon token" >&2; exit 1; fi
fi
API="http://$HTTP_BASE/rpc"
MCP_URL="http://$HTTP_BASE/mcp"
# A daemon that was already running when we persisted the key was launched with
# its OLD env — the new key only reaches it on restart.
if [ "$JOINED" = "1" ] && [ -n "$L3_KEY_VALUE" ]; then
  echo ""
  echo "   NOTE: the daemon at $ADDR was already running when '$L3_KEY_ENV' was"
  echo "         persisted. If it was started before the key existed, restart it"
  echo "         so digest.run can authenticate:  scripts/memory-layer/serve.sh restart"
fi

# ---- 1b. ensure the memory plane exists -------------------------------------
# A fresh daemon db has only the default 'startup' plane; hooks write to
# 'memory'. Create it idempotently (fails harmlessly if it already exists).
echo "== ensuring 'memory' plane…"
curl -sf -m 5 -X POST "$API" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"plane.create","params":{"name":"memory"}}' >/dev/null 2>&1 \
  || true

# ---- 2. copy hooks ----------------------------------------------------------
echo "== installing hooks into $PROJECT_DIR/.claude/hooks/"
mkdir -p "$PROJECT_DIR/.claude/hooks"
cp "$TEMPLATES/hooks/"*.py "$PROJECT_DIR/.claude/hooks/"
chmod +x "$PROJECT_DIR/.claude/hooks/"*.py

# ---- 3. write .drsg/env -----------------------------------------------------
echo "== writing $PROJECT_DIR/.drsg/env"
mkdir -p "$PROJECT_DIR/.drsg"
cat > "$PROJECT_DIR/.drsg/env" <<EOF
DRSG_TOKEN=$TOKEN
DRSG_API=$API
DRSG_PLANE=memory
DRSG_L3_CHAT=$L3_CHAT
DRSG_L3_KEY_ENV=$L3_KEY_ENV
DRSG_L3_MODEL=$L3_MODEL
DRSG_L3_REASONING=$L3_REASONING
EOF
chmod 600 "$PROJECT_DIR/.drsg/env"

# .drsg/env holds the shared API token, and .drsg/ collects telemetry besides.
# chmod 600 keeps other users out; it does nothing about `git add .`, so ignore
# the directory in any repo we install into.
if [ -d "$PROJECT_DIR/.git" ] && ! git -C "$PROJECT_DIR" check-ignore -q .drsg/env 2>/dev/null; then
  echo "== adding .drsg/ to $PROJECT_DIR/.gitignore (it holds the API token)"
  printf '\n# dr-strange memory layer (API token + local telemetry)\n.drsg/\n' \
    >> "$PROJECT_DIR/.gitignore"
fi

# ---- 4. merge hooks into settings.local.json --------------------------------
echo "== registering hooks in $PROJECT_DIR/.claude/settings.local.json"
SETTINGS="$PROJECT_DIR/.claude/settings.local.json"
mkdir -p "$PROJECT_DIR/.claude"
HOOKS_JSON="$TEMPLATES/settings-hooks.json"
python3 - "$SETTINGS" "$HOOKS_JSON" <<'PYEOF'
import json, sys
settings_path, hooks_path = sys.argv[1], sys.argv[2]
import os
if os.path.exists(settings_path):
    with open(settings_path) as f: d = json.load(f)
else:
    d = {}
with open(hooks_path) as f: hooks = json.load(f)
d['hooks'] = {**(d.get('hooks') or {}), **hooks}  # our hooks win on conflicts
with open(settings_path, 'w') as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
    f.write('\n')
print("   merged hooks:", ", ".join(d['hooks'].keys()))
PYEOF

# ---- 5. register MCP ---------------------------------------------------------
echo "== registering MCP server 'drsg' (project scope) at $MCP_URL"
# `--scope local` binds to the CURRENT cwd's project — so run the add from
# inside the target project, or it lands on the caller's project instead.
# HTTP transport uses `--header` (the `-H` short flag is WebSocket-only in
# claude 2.x); `--header` is also the canonical form the docs show for HTTP.
if command -v claude >/dev/null 2>&1; then
  # A previous install may have registered 'drsg' pointing at an OLD daemon;
  # claude mcp add fails if the name already exists. Remove first (ignore if
  # absent), then add — this makes re-install (e.g. re-pointing at a shared
  # daemon) idempotent.
  (cd "$PROJECT_DIR" && claude mcp remove drsg --scope local) >/dev/null 2>&1 || true
  (cd "$PROJECT_DIR" && claude mcp add --scope local --transport http drsg "$MCP_URL" \
    --header "Authorization: Bearer $TOKEN") >/dev/null 2>&1 \
    && echo "   MCP registered (claude mcp add)" \
    || echo "   WARN: claude mcp add failed — run it manually:"
  echo "     (cd $PROJECT_DIR && claude mcp add --scope local --transport http drsg $MCP_URL --header 'Authorization: Bearer <token>')"
else
  echo "   WARN: 'claude' not found — register MCP manually:"
  echo "     (cd $PROJECT_DIR && claude mcp add --scope local --transport http drsg $MCP_URL --header 'Authorization: Bearer $TOKEN')"
fi

# ---- 6. post-install self-check -----------------------------------------------
# Catches SILENT failures — the very thing that broke a production install once:
# a L3-digest `Key` node squatting the Project key made every hooks' WHERE
# key(p)=$proj query return nothing while install.sh still printed success.
# Verifies: daemon reachable · memory plane present · key=SLUG resolves to a
# Project node · a temp Fact is readable through the exact hook query.
echo "== post-install self-check…"
SELFCHECK="$(python3 - "$API" "$TOKEN" "$PLANE" "$SLUG" "$PROJECT_DIR" <<'PYEOF'
import json, sys, time, urllib.request
api, token, plane, slug, proj_dir = sys.argv[1:6]
def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(api, data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=5) as r:
        res = json.load(r)
    if "result" not in res:
        # Without this the caller sees KeyError('result') and has nothing to
        # act on; the daemon's own message names the problem (a bad token, say).
        raise RuntimeError((res.get("error") or {}).get("message") or res)
    return res["result"]
ok = True
out = []
# 1. daemon + plane
try:
    rpc("db.stats", {}); out.append("   ✓ daemon reachable")
except Exception as e:
    out.append("   ✗ daemon unreachable: %s" % e); ok = False
try:
    def plane_names():
        p = rpc("plane.list", {})
        return [x.get("name") for x in p] if isinstance(p, list) else []
    if plane not in plane_names():
        try:
            rpc("plane.create", {"name": plane})
        except Exception:
            pass
    if plane in plane_names():
        out.append("   ✓ plane '%s' present" % plane)
    else:
        out.append("   ✗ plane '%s' missing — hooks will write into nowhere" % plane); ok = False
except Exception as e:
    out.append("   ✗ plane check failed: %s" % e); ok = False
# 2. key collision guard: key=SLUG must resolve to a Project node
try:
    rpc("node.create", {"plane": plane, "key": slug, "labels": ["Project"],
                        "properties": {"path": proj_dir}})
except Exception:
    pass  # already exists — resolved below
try:
    n = rpc("node.get", {"plane": plane, "key": slug}) or {}
except Exception:
    n = {}
if "Project" in n.get("labels", []):
    out.append("   ✓ key '%s' → Project node (no collision)" % slug)
elif n:
    out.append("   ✗ key '%s' is squat by %s — some node already owns it. Rename or "
               "delete it: the key-addressed writes (node.create, edge.create by key) "
               "land on the wrong node." % (slug, n.get("labels")))
    ok = False
else:
    out.append("   ✗ key '%s' not found after create — node.create failed?" % slug); ok = False
# 3. end-to-end: write a temp Fact, read it via the exact hook query, delete it
try:
    key = "install-selfcheck-%d" % int(time.time())
    rpc("node.create", {"plane": plane, "key": key, "labels": ["Fact"],
                        "properties": {"kind": "self-check", "summary": "install self-check, deleted immediately"}})
    rpc("edge.create", {"plane": plane, "src": key, "dst": slug, "type": "ABOUT"})
    res = rpc("plane.cypher", {"plane": plane,
        "query": "MATCH (p:Project)<-[:ABOUT]-(f:Fact) WHERE p.path = $path RETURN f LIMIT 500",
        "params": {"path": proj_dir}})
    hit = any(n.get("external_key") == key for n in res.get("nodes", []))
    rpc("node.delete", {"plane": plane, "key": key})  # cascade removes its ABOUT edge
    if hit:
        out.append("   ✓ recall: temp Fact readable via the hooks' own query")
    else:
        out.append("   ✗ recall: temp Fact NOT visible — hooks will recall no memory"); ok = False
except Exception as e:
    out.append("   ✗ recall roundtrip failed: %s" % e); ok = False
print("\n".join(out))
print("RESULT=%s" % ("PASS" if ok else "FAIL"))
PYEOF
)"
echo "$SELFCHECK" | grep -v '^RESULT='
if echo "$SELFCHECK" | grep -q '^RESULT=FAIL'; then
  echo ""
  echo "⚠️  SELF-CHECK FAILED — memory layer installed but NOT working." >&2
  echo "    The hooks will silently fail (key collision / daemon down / plane missing)." >&2
  echo "    Fix the items above, then re-run: $0 $PROJECT_DIR" >&2
  echo "    See scripts/memory-layer/README.md for troubleshooting." >&2
  exit 1
else
  echo "   ✓ self-check PASSED — memory layer verified end-to-end"
fi

# ---- done --------------------------------------------------------------------
cat <<EOF

✅ dr-strange memory layer installed into $PROJECT_DIR
   daemon: $ADDR (token in $DAEMON_ENV)
   hooks:  $PROJECT_DIR/.claude/hooks/ (SessionStart/UserPromptSubmit/SessionEnd)
   config: $PROJECT_DIR/.drsg/env
   MCP:    drsg @ $MCP_URL (project scope)
   L3:     $(if [ -n "$L3_CHAT" ]; then echo "enabled via $L3_CHAT"; else echo "DISABLED (no --l3-chat)"; fi)

NEXT: restart Claude Code in this project. SessionStart will inject the memory
briefing + protocol; you'll get the 'drsg' MCP tools (plane: memory); sessions
auto-record on start/end and distill via L3 when configured.
EOF

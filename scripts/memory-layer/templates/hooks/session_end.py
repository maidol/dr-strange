#!/usr/bin/env python3
"""SessionEnd hook: stamp ended_at + mine structural facts from the transcript.

L1 (structural): read `transcript_path` and extract facts that need no value
judgment but are genuinely useful — files touched (Read/Write/Edit), Bash
commands run, session duration. Written onto the Session node so a later
SessionStart briefing shows "what this project was recently working on".

Also counts tool outcomes (`tool_calls` / `tool_errors` / `tool_rejected` /
`tool_errors_top`). That is telemetry, not briefing material: it is the one
objective outcome proxy available for judging whether recalled memory actually
improved the work (docs/memory-layer-observability.md, P3). Raw counts only — the
denominator and the per-tool split are stored so the analysis can decide later
what counts as a failure, instead of that judgment being baked in here.

Must stay fast — SessionEnd hooks share a tight budget (settings `timeout`
raises it, default 1.5s shared). One read + one RPC; the transcript is streamed
(no full-file load), errors are swallowed. Never blocks session end.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections import Counter

# --- Configuration (overridable via .drsg/env) -----------------------------
API = "http://127.0.0.1:7700/rpc"
PLANE = "memory"
# Cap for how much of the transcript we scan — keep SessionEnd cheap even on
# huge sessions. ~4k assistant lines is far beyond any single turn.
MAX_LINES = 4000
# Minimum transcript size before we bother spawning L3 distillation.
L3_MIN_TRANSCRIPT = 40_000
# `is_error` tool results that are not failures: the user declined the call or
# interrupted it. The agent proposed something reasonable, so these must not
# land in tool_errors — they are counted separately, never silently dropped.
NOT_A_FAILURE = ("the user doesn't want", "tool use was rejected", "interrupted by user")


def rpc(method, params, token):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        API, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.load(r)["result"]


def load_env(proj_dir):
    p = os.path.join(proj_dir, ".drsg", "env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _result_text(block):
    """A tool_result's content is either a string or a list of text blocks."""
    c = block.get("content")
    if isinstance(c, list):
        c = " ".join(i.get("text", "") for i in c if isinstance(i, dict))
    return (c if isinstance(c, str) else "").lower()


def mine(transcript_path):
    """Scan the transcript JSONL for files touched, commands run, tool outcomes."""
    files = Counter()
    commands = Counter()
    tools = Counter()          # calls / errors / rejected
    failed_by = Counter()      # which tool failed, so infra noise stays separable
    names = {}                 # tool_use_id -> tool name (results carry only the id)
    if not transcript_path or not os.path.exists(transcript_path):
        return files, commands, tools, failed_by
    n = 0
    with open(transcript_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if n >= MAX_LINES:
                break
            n += 1
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            kind = d.get("type")
            if kind not in ("assistant", "user"):
                continue
            for t in d.get("message", {}).get("content") or []:
                if not isinstance(t, dict):
                    continue
                if t.get("type") == "tool_use":
                    name = t.get("name")
                    if t.get("id"):
                        names[t["id"]] = name
                    if name in ("Read", "Write", "Edit"):
                        fp = t.get("input", {}).get("file_path") or t.get("input", {}).get("path")
                        if fp:
                            files[os.path.basename(fp)] += 1
                    elif name == "Bash":
                        cmd = (t.get("input", {}).get("command") or "")[:40]
                        if cmd:
                            commands[re.sub(r"\\s+", " ", cmd)] += 1
                elif t.get("type") == "tool_result":
                    tools["calls"] += 1
                    if not t.get("is_error"):
                        continue
                    text = _result_text(t)
                    if any(p in text for p in NOT_A_FAILURE):
                        tools["rejected"] += 1
                    else:
                        tools["errors"] += 1
                        failed_by[names.get(t.get("tool_use_id"), "?")] += 1
    return files, commands, tools, failed_by


def main():
    data = json.load(sys.stdin)
    proj_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    load_env(proj_dir)
    # Re-read config after load_env populated os.environ (install.sh's .drsg/env).
    global API, PLANE
    API = os.environ.get("DRSG_API", API)
    PLANE = os.environ.get("DRSG_PLANE", PLANE)
    l3_chat = os.environ.get("DRSG_L3_CHAT", "")
    token = os.environ.get("DRSG_TOKEN", "")
    if not token:
        return

    sid = data.get("session_id", "")
    if not sid:
        return
    props = {"ended_at": int(time.time())}

    # L1 structural mining (best-effort).
    try:
        files, commands, tools, failed_by = mine(data.get("transcript_path", ""))
        if files:
            props["files_touched"] = ",".join(f"{k}×{v}" for k, v in files.most_common(6))
        if commands:
            props["commands_run"] = ",".join(f"{k}×{v}" for k, v in commands.most_common(4))
        if tools["calls"]:
            # Always written when there were any calls, zeros included — an
            # absent property and a genuine zero must stay distinguishable.
            # Both numerator and denominator come from the same MAX_LINES
            # window, so the ratio holds even where the count is truncated.
            props["tool_calls"] = tools["calls"]
            props["tool_errors"] = tools["errors"]
            props["tool_rejected"] = tools["rejected"]
            if failed_by:
                props["tool_errors_top"] = ",".join(
                    f"{k}×{v}" for k, v in failed_by.most_common(4))
    except Exception:
        pass  # mining is best-effort

    try:
        rpc("node.update", {"plane": PLANE, "key": sid, "set": props}, token)
    except Exception:
        pass  # failure is harmless — we never block session end

    # L3 LLM distillation (detached, never blocks session end). Only when an
    # L3 chat endpoint is configured and the transcript is non-trivial. Empty
    # DRSG_L3_CHAT disables L3 entirely. No local key check — digest.run passes
    # the key NAME (key_env) and the daemon reads the VALUE from its own env; a
    # missing key surfaces as a visible digest.run error in l3.log.
    try:
        transcript_path = data.get("transcript_path", "")
        if l3_chat and transcript_path \
                and os.path.exists(transcript_path) \
                and os.path.getsize(transcript_path) >= L3_MIN_TRANSCRIPT:
            hook_dir = os.path.dirname(os.path.abspath(__file__))
            script = os.path.join(hook_dir, "l3_digest.py")
            drsg_dir = os.path.join(proj_dir, ".drsg")
            os.makedirs(drsg_dir, exist_ok=True)
            logf = open(os.path.join(drsg_dir, "l3-spawn.log"), "a", encoding="utf-8")
            subprocess.Popen(
                [sys.executable, script, sid, transcript_path],
                start_new_session=True,      # detach from Claude's process group
                cwd=proj_dir,
                stdout=logf, stderr=subprocess.STDOUT,
                close_fds=True,
            )
            logf.close()
    except Exception:
        pass  # spawning L3 is best-effort; never block session end


if __name__ == "__main__":
    main()

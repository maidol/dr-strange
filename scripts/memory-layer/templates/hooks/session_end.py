#!/usr/bin/env python3
"""SessionEnd hook: stamp ended_at + mine structural facts from the transcript.

L1 (structural): read `transcript_path` and extract facts that need no value
judgment but are genuinely useful — files touched (Read/Write/Edit), Bash
commands run, session duration. Written onto the Session node so a later
SessionStart briefing shows "what this project was recently working on".

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


def mine(transcript_path):
    """Scan the transcript JSONL for files touched + commands run."""
    files = Counter()
    commands = Counter()
    if not transcript_path or not os.path.exists(transcript_path):
        return files, commands
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
            if d.get("type") != "assistant":
                continue
            for t in d.get("message", {}).get("content") or []:
                if not isinstance(t, dict):
                    continue
                if t.get("type") == "tool_use":
                    name = t.get("name")
                    if name in ("Read", "Write", "Edit"):
                        fp = t.get("input", {}).get("file_path") or t.get("input", {}).get("path")
                        if fp:
                            files[os.path.basename(fp)] += 1
                    elif name == "Bash":
                        cmd = (t.get("input", {}).get("command") or "")[:40]
                        if cmd:
                            commands[re.sub(r"\\s+", " ", cmd)] += 1
    return files, commands


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
        files, commands = mine(data.get("transcript_path", ""))
        if files:
            props["files_touched"] = ",".join(f"{k}×{v}" for k, v in files.most_common(6))
        if commands:
            props["commands_run"] = ",".join(f"{k}×{v}" for k, v in commands.most_common(4))
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

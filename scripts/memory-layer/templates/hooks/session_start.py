#!/usr/bin/env python3
"""SessionStart hook: record this session in dr-strange and inject the
project's compressed memory briefing into Claude's context.

Design (see scripts/memory-layer/README.md):
  * Dr-strange native backend allows ONE process per database, so hooks never
    open the DB themselves — they talk to the shared `drsg serve` daemon over
    JSON-RPC (POST /rpc), never the CLI.
  * The API requires `Authorization: Bearer $DRSG_TOKEN` for native clients.
  * Any failure is a soft downgrade: if the daemon is down we emit a bare
    hook output (SessionStart stdout at exit 0 is injected as context, and a
    malformed JSON would break the session). We never block.
  * Memory growth vs context: the injected payload is a *briefing* — a rule
    -compressed, per-kind summary of the project's Facts stored in
    Project.briefing — not the full Fact list. The full Facts stay queryable
    via the MCP tools / per-prompt recall (user_prompt.py), so the constant
    startup cost stays ~one short paragraph no matter how many Facts exist.
  * The .drsg/env file (gitignored) holds DRSG_TOKEN/DRSG_API/DRSG_PLANE.
  * Runs on `startup|resume|compact`. A compaction drops the injected briefing
    and protocol out of context while the session continues, so they have to be
    re-injected; only the node bookkeeping differs (see `source == "compact"`).
"""
import json
import os
import sys
import time
import urllib.request

# --- Configuration (overridable via .drsg/env) -----------------------------
# install.sh writes .drsg/env; values here are project-agnostic defaults that
# get re-read from os.environ after load_env in main().
API = "http://127.0.0.1:7700/rpc"
PLANE = "memory"
# How many Facts to consider when building the briefing. Far beyond current
# usage; the briefing itself is compressed regardless of this cap.
BRIEFING_FACT_CAP = 1000

# L2 write-memory protocol: injected with the briefing so the model
# automatically persists valuable conclusions without any user action.
def protocol(slug, plane):
    return (
        f"[write-memory protocol] Persist this session's durable conclusions, "
        f"gotchas and decisions into the `{plane}` plane yourself (MCP tools "
        f"cypher / write_nodes / write_edges, plane=\"{plane}\"):\n"
        f"- one `Fact` node each, with an idempotent `external_key` (e.g. "
        f"fact-{slug}-<topic>), a `kind` you choose (setup-experience / "
        "decision / gotcha / session-summary), `summary` as a one-line "
        "conclusion, `detail` for the rest, `created_at` as the current time;\n"
        f"- linked to the `Project` (key={slug}) with an `ABOUT` edge;\n"
        "- before the session ends, one `kind:\"session-summary\"` Fact "
        "recording what of this session is worth keeping."
    )


def rpc(method, params, token):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        API, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)["result"]


def load_env(proj_dir):
    p = os.path.join(proj_dir, ".drsg", "env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def hook_out(**extra):
    # stdout must contain exactly one JSON object.
    out = {"hookSpecificOutput": {"hookEventName": "SessionStart"}}
    out["hookSpecificOutput"].update(extra)
    print(json.dumps(out))


def telemetry(proj_dir, record):
    """Append one JSON line to .drsg/recall.jsonl. Same file and same silence
    as user_prompt.py's: one log, so a session's startup cost and its
    per-prompt recalls can be read on one timeline."""
    try:
        d = os.path.join(proj_dir, ".drsg")
        os.makedirs(d, exist_ok=True)
        record["ts"] = int(time.time())
        with open(os.path.join(d, "recall.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def project_id(proj_dir, token):
    """The Project node for this working directory, addressed by `path`.

    Not by `key(p)`: digest.write bypasses the uniqueness check that
    node.create enforces, so a distillation run can write a second node under
    an existing key and shadow the real one — and deleting that shadow does not
    hand the key back, it just leaves it resolving to nothing. Either way every
    key-filtered read silently returns empty while the data is untouched.
    That has happened, and it took the whole briefing out with it.
    `path` is ours, written here at line ~1 of every session, and no distiller
    invents it. Returns the node id, or None when this project is new."""
    res = rpc("plane.cypher", {"plane": PLANE,
        "query": "MATCH (p:Project) RETURN p", "params": {}}, token)
    for n in res.get("nodes", []):
        if (n.get("properties") or {}).get("path") == proj_dir:
            return n.get("id")
    return None


def all_facts(proj_dir, token):
    """All Facts ABOUT this project, newest first."""
    res = rpc("plane.cypher", {"plane": PLANE,
        "query": ("MATCH (p:Project)<-[:ABOUT]-(f:Fact) "
                  "WHERE p.path = $path "
                  "RETURN f ORDER BY f.created_at DESC LIMIT %d") % BRIEFING_FACT_CAP,
        "params": {"path": proj_dir}}, token)
    return res.get("nodes", [])


def short_tag(s, n=18):
    """One compressed label per Fact summary: prefer the conclusion side of
    an arrow, cut to n chars. Zero-dependency rule compression."""
    s = (s or "").strip()
    for sep in ("→", " -> ", " => "):
        if sep in s:
            s = s.split(sep, 1)[1].strip()
            break
    # First sentence only, either script's full stop.
    s = s.split("\u3002")[0].split(". ")[0]
    return s if len(s) <= n else s[:n - 1] + "…"


def build_briefing(facts):
    """Group Facts by kind, one line each: `• <kind> ×n: tag; tag; ...`."""
    by_kind = {}
    for n in facts:
        pr = n.get("properties", {})
        kind = pr.get("kind") or "general"
        by_kind.setdefault(kind, []).append(short_tag(pr.get("summary", "")))
    lines = []
    for kind in sorted(by_kind):
        tags = by_kind[kind]
        lines.append("• %s ×%d: %s" % (kind, len(tags), "; ".join(t for t in tags if t)))
    return "\n".join(lines)


def ensure_briefing(proj_dir, pid, token):
    """Rebuild Project.briefing when the Fact count changed; else reuse it.
    Returns (briefing text, fact count). Addressed by node id (see project_id).

    The count comes back for the telemetry line: a briefing that stops growing
    is the difference between "nothing worth writing happened" and "the write
    path broke", and the text alone cannot tell those apart."""
    try:
        proj = rpc("node.get", {"plane": PLANE, "id": pid}, token)
        stored = (proj or {}).get("properties", {}).get("briefing_count")
        stale = True
        try:
            facts = all_facts(proj_dir, token)
            if stored == len(facts):
                stale = False
        except Exception:
            facts = None  # read failed — not the same as "no facts"
        if stale:
            brief = build_briefing(facts) if facts else ""
            rpc("node.update", {"plane": PLANE, "id": pid, "set": {
                "briefing": brief, "briefing_count": len(facts or []),
                "briefing_at": int(time.time())}}, token)
        else:
            brief = (proj.get("properties", {}).get("briefing", "") or "")
        return brief, len(facts) if facts is not None else stored
    except Exception as e:
        print(f"[drsg-memory] ensure_briefing failed: {e}", file=sys.stderr)
        return "", None


def main():
    ts_start = time.time()
    data = json.load(sys.stdin)
    proj_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    load_env(proj_dir)
    # Re-read config after load_env populated os.environ (install.sh's .drsg/env).
    global API, PLANE
    API = os.environ.get("DRSG_API", API)
    PLANE = os.environ.get("DRSG_PLANE", PLANE)
    token = os.environ.get("DRSG_TOKEN", "")
    if not token:
        hook_out()
        return
    try:
        rpc("db.stats", {}, token)  # liveness probe
    except Exception:
        hook_out()  # daemon down → don't block the session
        return

    sid = data.get("session_id", "")
    slug = os.path.basename(os.path.normpath(proj_dir))
    ts = int(time.time())

    # 1. Idempotently record Project + a fresh Session for this session.
    #    Existence is decided by `path`, not by whether the key is free: after a
    #    key-index accident the key can be unowned while the node is right there
    #    with every Fact still attached, and creating "because the key was free"
    #    is what turns one damaged project into two.
    pid = project_id(proj_dir, token)
    if pid is None:
        try:
            created = rpc("node.create", {"plane": PLANE, "key": slug, "labels": ["Project"],
                                          "properties": {"path": proj_dir}}, token)
            pid = created.get("id")
        except Exception as e:
            print(f"[drsg-memory] project create failed: {e}", file=sys.stderr)
    source = data.get("source", "startup")
    if source == "compact":
        # A compaction keeps the SAME session_id, so the Session node and its
        # BELONGS_TO edge already exist — creating again would only collide.
        # We still run (matcher includes `compact`) because the point of this
        # hook on a compaction is re-injecting the briefing and the protocol,
        # which the compaction summary drops. Stamp the event and move on.
        try:
            rpc("node.update", {"plane": PLANE, "key": sid,
                                "set": {"compacted_at": ts}}, token)
        except Exception as e:
            print(f"[drsg-memory] compact stamp failed: {e}", file=sys.stderr)
    else:
        try:
            rpc("node.create", {"plane": PLANE, "key": sid, "labels": ["Session"],
                "properties": {"started_at": ts,
                               "source": source,
                               "cwd": data.get("cwd", ""),
                               "project": slug}}, token)
            # Link by id (NodeRef is untagged: a number is an id, a string a key).
            # A dangling key makes edge.create fail outright — the session record
            # would be lost for exactly as long as the key stayed broken.
            rpc("edge.create", {"plane": PLANE, "src": sid, "dst": pid,
                                "type": "BELONGS_TO"}, token)
        except Exception as e:
            print(f"[drsg-memory] record failed: {e}", file=sys.stderr)

    # 2. Inject: the compressed briefing + recent sessions (NOT full Facts).
    parts = []
    brief, n_facts = ensure_briefing(proj_dir, pid, token) if pid else ("", None)
    if brief:
        parts.append("Briefing:\n" + brief)
    try:
        res = rpc("plane.cypher", {"plane": PLANE,
            "query": ("MATCH (p:Project)<-[:BELONGS_TO]-(s:Session) "
                      "WHERE p.path = $path "
                      "RETURN s ORDER BY s.started_at DESC LIMIT 3"),
            "params": {"path": proj_dir}}, token)
        # Only sessions that actually say something. Nothing writes
        # Session.summary today, so listing every recent session spent ~10% of
        # the startup injection on three lines of bare timestamps — context the
        # model cannot act on. When a summary does get written the line earns
        # its place again, and the block comes back on its own.
        sess_lines = []
        for n in res.get("nodes", []):
            pr = n.get("properties", {})
            if not pr.get("summary"):
                continue
            sess_lines.append(f"- session {pr.get('started_at')} "
                              f"[{pr.get('source', '?')}]: {pr['summary']}")
        if sess_lines:
            parts.append("Recent sessions:\n" + "\n".join(sess_lines))
    except Exception as e:
        print(f"[drsg-memory] recall sessions: {e}", file=sys.stderr)

    # L2: the write-memory protocol — makes the model the value-judge for what
    # deserves persisting, every turn, automatically (no user action needed).
    proto = protocol(slug, PLANE)
    parts.append(proto)
    ctx = "# dr-strange memory (%s)\n%s" % (slug, "\n\n".join(parts))
    # Split the cost the way it is spent. The protocol is a fixed instruction,
    # not memory: counted together with the briefing it hides that most of the
    # startup budget buys no recall at all.
    # `source` distinguishes the startup injection from a re-injection after a
    # compaction — without it one session's two briefing lines are unreadable.
    telemetry(proj_dir, {"event": "briefing", "session": sid, "project": slug,
                         "source": source, "facts": n_facts,
                         "brief_chars": len(brief), "proto_chars": len(proto),
                         "total_chars": len(ctx),
                         "ms": int((time.time() - ts_start) * 1000)})
    hook_out(additionalContext=ctx)


if __name__ == "__main__":
    main()

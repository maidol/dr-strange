#!/usr/bin/env python3
"""Cross-agent coordination events in the memory plane.

A Fact is what an agent *learned*; an Event is what an agent wants another
agent to *do*. Both live in the memory plane, but they are read on different
paths, and deliberately so:

  * Facts go through the briefing and the per-prompt recall — ranked, lossy,
    compressed to ~18 chars. Right for knowledge, wrong for a task: a to-do
    that loses its ranking contest simply never arrives.
  * Events go through a small dedicated block at SessionStart that is bounded,
    uncompressed, and disappears the moment they are closed.

The recipient is the NOTIFY edge's target rather than a property: the graph
already models "who is this for", and the only question we ever ask is "what
is still open for this project?", which the edge answers directly.

Usage:
  event.py post <recipient-project-dir> <summary> [--kind handoff|notice] [--ref R]
  event.py list [<project-dir>]
  event.py done <event-key>

Config comes from the CWD's .drsg/env (DRSG_API / DRSG_PLANE / DRSG_TOKEN),
the same file the hooks read.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request

API = "http://127.0.0.1:7700/rpc"
PLANE = "memory"
# What a SessionStart is willing to look at. Kept here as well so `list` and
# the hook agree on how much is "too many to still be a to-do list".
LIST_CAP = 20


def load_env(proj_dir):
    p = os.path.join(proj_dir, ".drsg", "env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def rpc(method, params, token):
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "method": method, "params": params}).encode()
    req = urllib.request.Request(
        API, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        out = json.load(r)
    if "error" in out:
        raise RuntimeError(out["error"])
    return out["result"]


def project_id(path, token):
    """The Project node for `path`, by property — never by key.

    Same reason as the hooks: digest.run has written a second node under an
    existing project's key, and every key-filtered read then silently returns
    nothing while the data sits untouched."""
    res = rpc("plane.cypher", {"plane": PLANE,
                               "query": "MATCH (p:Project) RETURN p",
                               "params": {}}, token)
    for n in res.get("nodes", []):
        if (n.get("properties") or {}).get("path") == path:
            return n.get("id")
    return None


def fetch(path, token):
    """Every Event addressed to `path`, newest first. Status is filtered by
    the caller: one predicate on the pattern's first variable is the shape
    every other query in this layer uses and is known to work."""
    res = rpc("plane.cypher", {"plane": PLANE,
        "query": ("MATCH (p:Project)<-[:NOTIFY]-(e:Event) "
                  "WHERE p.path = $path "
                  "RETURN e ORDER BY e.created_at DESC LIMIT %d") % LIST_CAP,
        "params": {"path": path}}, token)
    return res.get("nodes", [])


def cmd_post(args, token):
    target = os.path.abspath(os.path.normpath(args.recipient))
    pid = project_id(target, token)
    if pid is None:
        sys.exit(f"no Project node with path {target} — has a session ever "
                 f"started there with the memory layer installed?")
    ts = int(time.time())
    slug = os.path.basename(target)
    h = hashlib.sha1(args.summary.encode("utf-8")).hexdigest()[:6]
    key = f"evt-{slug}-{ts}-{h}"
    props = {"kind": args.kind, "status": "open", "summary": args.summary,
             "from_project": os.path.basename(os.path.normpath(os.getcwd())),
             "from_session": os.environ.get("CLAUDE_SESSION_ID", ""),
             "created_at": ts}
    if args.ref:
        props["ref"] = args.ref
    rpc("node.create", {"plane": PLANE, "key": key,
                        "labels": ["Event"], "properties": props}, token)
    # By id, not key: a dangling key makes edge.create fail outright, and an
    # Event with no NOTIFY edge is invisible exactly like an unlinked Fact.
    rpc("edge.create", {"plane": PLANE, "src": key, "dst": pid,
                        "type": "NOTIFY"}, token)
    print(key)


def cmd_list(args, token):
    path = os.path.abspath(os.path.normpath(args.project or os.getcwd()))
    for n in fetch(path, token):
        pr = n.get("properties", {})
        print("%-9s %-8s %s  %s" % (pr.get("status", "?"), pr.get("kind", "?"),
                                    n.get("external_key", "?"),
                                    pr.get("summary", "")))


def cmd_done(args, token):
    rpc("node.update", {"plane": PLANE, "key": args.key,
                        "set": {"status": "done", "done_at": int(time.time())}},
        token)
    print(f"{args.key} done")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("post", help="leave an event for another project")
    p.add_argument("recipient")
    p.add_argument("summary")
    p.add_argument("--kind", default="handoff", choices=["handoff", "notice"])
    p.add_argument("--ref", default="")
    p.set_defaults(fn=cmd_post)

    p = sub.add_parser("list", help="events addressed to a project")
    p.add_argument("project", nargs="?")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("done", help="close an event by key")
    p.add_argument("key")
    p.set_defaults(fn=cmd_done)

    args = ap.parse_args()
    load_env(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    global API, PLANE
    API = os.environ.get("DRSG_API", API)
    PLANE = os.environ.get("DRSG_PLANE", PLANE)
    token = os.environ.get("DRSG_TOKEN", "")
    if not token:
        sys.exit("DRSG_TOKEN missing — run from a project with .drsg/env")
    args.fn(args, token)


if __name__ == "__main__":
    main()

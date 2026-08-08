#!/usr/bin/env python3
"""Read the memory layer's telemetry and say whether recall is earning its keep.

Answers, from records rather than impressions:

  * **Utilization** — of the facts that were injected, how many did the model
    visibly use in the reply? Injection is free to measure and means nothing on
    its own; a fact that is injected every day and never used is a tax.
  * **Cross-project** — is utilization for facts borrowed from another project
    comparable to local ones, or is sharing just noise with a token bill?
  * **Dead and dumb facts** — injected often but never used (demote), or never
    injected at all (its wording matches no real prompt; rewrite or delete).
  * **Cost** — the injected characters and hook latency the above is bought with.

Usage:
    python3 analyze_recall.py                 # every project the daemon knows
    python3 analyze_recall.py --project DIR   # just this one, repeatable
    python3 analyze_recall.py --since 14      # last 14 days

## How "used" is decided, and what it is worth

An injected line shows the model one compressed tag (~60 chars), nothing more.
So usage can only mean: the reply contains discriminative material from that
tag. "Discriminative" does two jobs —

  * grams that also appear in the user's own prompt are dropped, or the metric
    would measure the prompt echoing itself;
  * grams that are common across the whole fact corpus are dropped by document
    frequency, so "配置" and "文件" cannot carry a match on their own.

This is a proxy and it is stated as one. It over-counts a reply that merely
acknowledges the memory, and it under-counts a memory that changed what the
model *chose not to do* — the gcc fact working perfectly looks like a build
that simply didn't fail. Read it as a floor on a per-fact basis and a trend in
aggregate; it is not a quality score, and P2 (trap recurrence) is the metric
that answers "did it prevent the accident".
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict

TRANSCRIPT_ROOT = os.path.expanduser("~/.claude/projects")
# Assistant turns after the prompt that count as "the reply". More than one
# because the answer often lands after a couple of tool calls.
REPLY_TURNS = 3
# A gram must appear in at most this share of facts to count as discriminative.
DF_SHARE = 0.15
# Matched characters, after overlapping grams are merged into spans, before a
# reply counts as using the fact. Mass, not count: the false positives left
# after span-merging were all pairs of generic two-character words (一个, 索引,
# 测试) while every true positive matched a run of 15-25 characters, so length
# separates them and a hit count does not. Measured on the 34-fact corpus:
# 6/6 true positives, 3/170 false (1.8%). The three are `create` and `return`
# — English keywords that belong to the fact and to unrelated prose equally,
# which no threshold on this signal can separate.
MIN_MATCH_CHARS = 6


# --- shared with the hooks (kept in step deliberately) ----------------------

def clean(s):
    import re
    return re.sub(r"[^\w一-鿿]+", "", (s or "").lower())


def grams(s, lo=2, hi=4):
    out = []
    for n in range(lo, hi + 1):
        for i in range(len(s) - n + 1):
            out.append(s[i:i + n])
    return out


def prompt_id(prompt):
    return hashlib.sha1(prompt.encode("utf-8", "replace")).hexdigest()[:16]


# --- daemon ----------------------------------------------------------------

def load_env(proj_dir):
    env = {}
    p = os.path.join(proj_dir, ".drsg", "env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def rpc(api, method, params, token):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(api, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        res = json.load(r)
    if "result" not in res:
        raise RuntimeError((res.get("error") or {}).get("message") or res)
    return res["result"]


def prop(v):
    """Unwrap the daemon's described-property form ({$desc,$value})."""
    return v.get("$value") if isinstance(v, dict) and "$value" in v else v


def fetch_facts(api, plane, token):
    """{key: {summary, kind, origin}} for every project."""
    res = rpc(api, "plane.cypher", {"plane": plane,
                                    "query": "MATCH (p:Project) RETURN p", "params": {}}, token)
    paths = [prop((n.get("properties") or {}).get("path")) for n in res.get("nodes", [])]
    facts = {}
    for path in [p for p in paths if p]:
        origin = os.path.basename(os.path.normpath(path))
        res = rpc(api, "plane.cypher", {"plane": plane,
            "query": ("MATCH (p:Project)<-[:ABOUT]-(f:Fact) WHERE p.path = $path "
                      "RETURN f LIMIT 500"), "params": {"path": path}}, token)
        for n in res.get("nodes", []):
            pr = n.get("properties", {})
            summary = prop(pr.get("summary")) or ""
            if not summary:
                continue
            facts.setdefault(n.get("external_key", "?"), {
                "summary": summary, "kind": prop(pr.get("kind")) or "?", "origin": origin})
    return facts


# --- transcripts -----------------------------------------------------------

def transcript_dir(proj_dir):
    return os.path.join(TRANSCRIPT_ROOT, proj_dir.replace("/", "-"))


def load_turns(proj_dir):
    """{prompt_id: [assistant text following it]} across this project's
    transcripts. Keyed by digest so the log never has to store the prompt."""
    out = defaultdict(list)
    d = transcript_dir(proj_dir)
    if not os.path.isdir(d):
        return out
    for name in os.listdir(d):
        if not name.endswith(".jsonl"):
            continue
        pending, seen = None, 0
        with open(os.path.join(d, name), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                t, content = rec.get("type"), rec.get("message", {}).get("content")
                if t == "user":
                    text = content if isinstance(content, str) else "".join(
                        str(i.get("text", "")) for i in (content or [])
                        if isinstance(i, dict) and i.get("type") == "text")
                    text = text.strip()
                    # A tool result arrives typed as "user" with no text part;
                    # treating it as a new prompt would cut the reply window
                    # short and undercount every multi-tool answer.
                    if text:
                        pending, seen = prompt_id(text), 0
                elif t == "assistant" and pending and seen < REPLY_TURNS:
                    chunk = "".join(str(i.get("text", "")) for i in (content or [])
                                    if isinstance(i, dict) and i.get("type") == "text")
                    if chunk.strip():
                        out[pending].append(chunk)
                        seen += 1
    return out


# --- utilization -----------------------------------------------------------

def informative(g):
    """A gram that can carry a match on its own.

    Two CJK characters are close to a word and worth ranking on. Two latin ones
    are a fragment: `cc`, `bi` and `ca` all fired on unrelated replies during
    development — `ca` came from the word "Cargo" and nearly matched a fact
    about gcc. Latin grams therefore have to be long enough to mean something.
    """
    return len(g) >= 4 or any(ch > "⺀" for ch in g)


def discriminative(facts):
    """{key: set(grams)} — each fact's grams, minus the uninformative ones and
    minus those common across the corpus."""
    df = defaultdict(int)
    per = {}
    for k, f in facts.items():
        g = {x for x in grams(clean(f["summary"])) if informative(x)}
        per[k] = g
        for x in g:
            df[x] += 1
    cap = max(1, int(len(facts) * DF_SHARE))
    return {k: {x for x in g if df[x] <= cap} for k, g in per.items()}


def used(fact_grams, prompt_grams, reply):
    """Did the reply carry the fact's own material, rather than the prompt's?

    Evidence is measured in matched characters over merged spans, not in grams.
    Overlapping grams are one piece of evidence, not several: `created` yields
    `crea`, `eate` and `reat`, which under a raw gram count reads as three
    independent matches and was enough to fire three unrelated facts on a
    sentence about a SQL index. And spans are weighed rather than counted,
    because two generic two-character words (一个 + 测试) are two spans and
    still no evidence at all.
    """
    r = clean(reply)
    spans = []
    for g in fact_grams - prompt_grams:
        start = r.find(g)
        while start != -1:
            spans.append((start, start + len(g)))
            start = r.find(g, start + 1)
    spans.sort()
    merged = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    mass = sum(hi - lo for lo, hi in merged)
    return mass >= MIN_MATCH_CHARS, {r[lo:hi] for lo, hi in merged}


# --- report ----------------------------------------------------------------

def pct(a, b):
    return "n/a" if not b else f"{100.0 * a / b:.0f}%"


def percentile(xs, p):
    if not xs:
        return 0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p / 100.0))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", action="append", default=[],
                    help="project dir; repeatable. Default: ask the daemon.")
    ap.add_argument("--since", type=int, default=0, help="only the last N days")
    ap.add_argument("--api", default="")
    ap.add_argument("--plane", default="")
    args = ap.parse_args()

    projects = [os.path.abspath(p) for p in args.project] or [os.getcwd()]
    env = load_env(projects[0])
    api = args.api or env.get("DRSG_API", "http://127.0.0.1:7700/rpc")
    plane = args.plane or env.get("DRSG_PLANE", "memory")
    token = env.get("DRSG_TOKEN", "")
    if not token:
        sys.exit(f"no DRSG_TOKEN in {projects[0]}/.drsg/env")

    try:
        facts = fetch_facts(api, plane, token)
    except Exception as e:
        sys.exit(f"cannot read facts from {api}: {e}")

    if not args.project:
        res = rpc(api, "plane.cypher", {"plane": plane,
                                        "query": "MATCH (p:Project) RETURN p", "params": {}}, token)
        found = [prop((n.get("properties") or {}).get("path")) for n in res.get("nodes", [])]
        projects = [p for p in found if p and os.path.isdir(p)] or projects

    cutoff = time.time() - args.since * 86400 if args.since else 0
    records = []
    for p in projects:
        log = os.path.join(p, ".drsg", "recall.jsonl")
        if not os.path.exists(log):
            continue
        for line in open(log, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("ts", 0) >= cutoff:
                r["_proj_dir"] = p
                records.append(r)

    if not records:
        print("no telemetry yet — .drsg/recall.jsonl is empty in: "
              + ", ".join(projects))
        print("\nThe hooks write it from their next run; come back after a few sessions.")
        return

    recalls = [r for r in records if r.get("event") == "recall"]
    briefs = [r for r in records if r.get("event") == "briefing"]
    disc = discriminative(facts)
    turns = {p: load_turns(p) for p in projects}

    # --- utilization ---
    inj = defaultdict(int)
    use = defaultdict(int)
    by_origin = defaultdict(lambda: [0, 0])  # origin -> [injected, used]
    unmatched = 0
    for r in recalls:
        if r.get("status") != "injected":
            continue
        reply = turns.get(r["_proj_dir"], {}).get(r.get("prompt"))
        if reply is None:
            unmatched += 1
            continue
        blob = "\n".join(reply)
        # The prompt's own grams are unavailable (only its digest is logged), so
        # the echo filter uses the injected tags of the *other* facts in the same
        # turn as the nearest stand-in for shared context.
        for key in r.get("injected", []):
            f = facts.get(key)
            if not f:
                continue
            inj[key] += 1
            ok, _ = used(disc.get(key, set()), set(), blob)
            if ok:
                use[key] += 1
            o = f["origin"] if f["origin"] == r.get("project") else f"{f['origin']} (foreign)"
            by_origin[o][0] += 1
            by_origin[o][1] += 1 if ok else 0

    total_inj = sum(inj.values())
    total_use = sum(use.values())

    print("=" * 72)
    print("memory layer — recall telemetry")
    print("=" * 72)
    span = (min(r["ts"] for r in records), max(r["ts"] for r in records))
    print(f"projects   : {', '.join(os.path.basename(p) for p in projects)}")
    print(f"window     : {time.strftime('%Y-%m-%d', time.localtime(span[0]))}"
          f" .. {time.strftime('%Y-%m-%d', time.localtime(span[1]))}")
    print(f"sessions   : {len({r.get('session') for r in records})}")
    print(f"prompts    : {len(recalls)}")
    st = defaultdict(int)
    for r in recalls:
        st[r.get("status", "?")] += 1
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(st.items())))
    if unmatched:
        print(f"  ({unmatched} injections had no reply in the transcripts — "
              "compacted or pruned; excluded from utilization)")

    print()
    print("-- utilization " + "-" * 57)
    print(f"injections  : {total_inj}")
    print(f"used        : {total_use}  ({pct(total_use, total_inj)})")
    if by_origin:
        print()
        print(f"  {'origin':<28} {'inj':>5} {'used':>5}  rate")
        for o, (i, u) in sorted(by_origin.items(), key=lambda t: -t[1][0]):
            print(f"  {o:<28} {i:>5} {u:>5}  {pct(u, i)}")
        loc = sum(v[0] for k, v in by_origin.items() if "(foreign)" not in k)
        locu = sum(v[1] for k, v in by_origin.items() if "(foreign)" not in k)
        fgn = sum(v[0] for k, v in by_origin.items() if "(foreign)" in k)
        fgnu = sum(v[1] for k, v in by_origin.items() if "(foreign)" in k)
        print()
        print(f"  cross-project share of injections : {pct(fgn, total_inj)}")
        if loc and fgn:
            ratio = (fgnu / fgn) / (locu / loc) if locu else 0.0
            print(f"  foreign/local utilization ratio   : {ratio:.2f}"
                  f"   (threshold: < 0.50 → turn cross-project recall off)")

    print()
    print("-- per fact " + "-" * 60)
    print(f"  {'key':<44} {'kind':<18} {'inj':>4} {'use':>4}")
    for key in sorted(inj, key=lambda k: -inj[k]):
        f = facts.get(key, {})
        print(f"  {key[:44]:<44} {f.get('kind','?')[:18]:<18} {inj[key]:>4} {use[key]:>4}")

    dead = [k for k in inj if inj[k] >= 5 and use[k] == 0]
    dumb = [k for k in facts if k not in inj]
    if dead:
        print(f"\n  dead ({len(dead)}) — injected >=5x, never used → demote or delete:")
        for k in dead:
            print(f"    {k}")
    if dumb:
        print(f"\n  never injected ({len(dumb)}/{len(facts)}) — wording matches no real "
              "prompt; rewrite the summary or delete:")
        for k in sorted(dumb)[:20]:
            print(f"    {k}  [{facts[k]['origin']}] {facts[k]['summary'][:52]}")
        if len(dumb) > 20:
            print(f"    ... and {len(dumb) - 20} more")

    print()
    print("-- cost " + "-" * 64)
    if briefs:
        b = [r.get("brief_chars", 0) for r in briefs]
        p_ = [r.get("proto_chars", 0) for r in briefs]
        t_ = [r.get("total_chars", 0) for r in briefs]
        print(f"session start : {len(briefs)} starts, {sum(t_)//len(t_)} chars avg")
        print(f"                briefing {sum(b)//len(b)}  protocol {sum(p_)//len(p_)}"
              f"   → memory is {pct(sum(b), sum(t_))} of the startup injection")
    rc = [r.get("chars", 0) for r in recalls if r.get("status") == "injected"]
    if rc:
        print(f"per prompt    : {sum(rc)//len(rc)} chars avg over {len(rc)} injections")
    ms = [r.get("ms", 0) for r in recalls]
    if ms:
        print(f"hook latency  : p50 {percentile(ms,50)}ms  p95 {percentile(ms,95)}ms")

    print()
    print("-- read against the pre-registered thresholds " + "-" * 26)
    if len({r.get("session") for r in records}) < 30:
        print("  NOT ENOUGH DATA. Fewer than 30 sessions recorded; every rate above")
        print("  is descriptive only. Do not act on it yet.")
    else:
        print(f"  utilization {pct(total_use, total_inj)} "
              f"({'FAIL — below 20%' if total_inj and total_use / total_inj < 0.2 else 'ok'})")


if __name__ == "__main__":
    main()

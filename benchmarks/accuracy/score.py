#!/usr/bin/env python3
"""Score a code-graph tool against the fixture keys.

What this measures, and why it is three numbers rather than one:

  **Recall** — of the calls whose target is unambiguous from syntax, how many
  does the tool report? A miss here is incompleteness: the agent asks "who
  calls this" and is told less than the source says.

  **Precision** — of the pairs the key marks as traps, how many does the tool
  assert anyway? These are same-name collisions and plausible-but-wrong
  targets. A hit here is worse than a miss: a wrong edge sends an agent to
  rewrite code that was never involved.

  **Honesty** — at the call sites where syntax genuinely cannot settle the
  target (an interface, a function value, a bound method), does the tool say
  so, or does it pick one and sound certain? Scored separately because a tool
  can be complete, precise, and still quietly guess.

    Honesty is reported in three parts rather than one percentage, because
    there are two different ways of not guessing and they are not worth the
    same. A **per-site** gap names the call the parser could not resolve, so
    an agent knows where to look itself. A **blanket** disclaimer — "recorded
    call edges only, this is a lower bound", which drsg prints on every
    answer — tells the agent that something may be missing without saying
    what. Both beat guessing. Only the first tells you where.

The fixture is small and hand-written; `crosscheck_go.py` checks the key
against an independent tool where one exists. Run it before trusting a score.

    uv run --no-project --with tomli python benchmarks/accuracy/score.py \\
        --drsg target/release/drsg
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent


def run(args: list[str], cwd: Path | None = None) -> str:
    p = subprocess.run(args, capture_output=True, text=True, cwd=cwd, check=False)
    return p.stdout


def drsg_edges(drsg: str, corpus: Path, plane: str, module: str) -> tuple[set, set]:
    """Every CALLS edge drsg records, in the key's vocabulary.

    Returns (resolved edges, callers that declared an unresolved target).
    drsg writes an unresolved target as `?::<file>::<name>`; that is the
    shape of it saying "a name was called here and I could not say what".
    """
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "acc.drsg")
        lang = corpus.name
        run([drsg, "--db", db, "plugin", "install", lang])
        run([drsg, "--db", db, "digest", str(corpus), "--apply", "--code-only", "--no-git"])
        out = run([drsg, "--db", db, "cypher", "--plane", plane,
                   "MATCH (a)-[:CALLS]->(b) RETURN key(a), key(b)"])
    rows = json.loads(out[out.index("{"):]).get("rows", [])
    edges, declared = set(), set()
    for a, b in rows:
        caller = normalise(a, module)
        if b.startswith("?::"):
            declared.add(caller)
        else:
            edges.add((caller, normalise(b, module)))
    return edges, declared


def normalise(name: str, module: str) -> str:
    name = name.replace(f"{module}.", "")
    name = re.sub(r"\(\*?([A-Za-z_][\w]*)\)\.", r"\1.", name)
    return name.strip()


def score(key: dict, edges: set, declared: set) -> dict:
    found = [e for e in key["resolved"] if (e["from"], e["to"]) in edges]
    missed = [e for e in key["resolved"] if (e["from"], e["to"]) not in edges]
    asserted = [e for e in key["absent"] if (e["from"], e["to"]) in edges]

    per_site, blanket, guessed = [], [], []
    for e in key["ambiguous"]:
        frm = e["from"]
        picked = [c for c in e.get("candidates", []) if (frm, c) in edges]
        iface = e.get("interface_method")
        if picked:
            guessed.append({**e, "picked": picked})
        elif frm in declared or (iface and (frm, iface) in edges):
            per_site.append(e)
        else:
            # No edge and no named gap. Not a guess — drsg's every answer
            # carries the lower-bound note — but the agent is not told which
            # call went unrecorded, only that some may have.
            blanket.append(e)
    return {
        "found": found, "missed": missed, "asserted": asserted,
        "per_site": per_site, "blanket": blanket, "guessed": guessed,
    }


def report(name: str, key: dict, s: dict) -> None:
    nres, nabs, namb = len(key["resolved"]), len(key["absent"]), len(key["ambiguous"])
    rec = len(s["found"]) / nres * 100 if nres else 0.0
    prec = (nabs - len(s["asserted"])) / nabs * 100 if nabs else 0.0
    not_guessed = len(s["per_site"]) + len(s["blanket"])
    print(f"\n=== {name} ===")
    print(f"  recall     {rec:5.1f}%   {len(s['found'])}/{nres} unambiguous calls found")
    print(f"  precision  {prec:5.1f}%   {nabs - len(s['asserted'])}/{nabs} traps avoided")
    print(f"  honesty            {not_guessed}/{namb} ambiguous sites not guessed at, of which")
    print(f"                     {len(s['per_site'])} named the gap, "
          f"{len(s['blanket'])} left only the blanket lower-bound note")
    for e in s["missed"]:
        print(f"    MISSED    {e['from']} -> {e['to']}  ({e['why']})")
    for e in s["asserted"]:
        print(f"    WRONG     {e['from']} -> {e['to']}  ({e['why']})")
    for e in s["guessed"]:
        print(f"    GUESSED   {e['from']} picked {e['picked']} at {e['site']}")
    for e in s["blanket"]:
        print(f"    BLANKET   {e['from']} at {e['site']} — no edge, no named gap")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drsg", default=str(REPO / "target/release/drsg"))
    ap.add_argument("--only", help="score one corpus by directory name")
    args = ap.parse_args()

    corpora = sorted(p for p in (ROOT / "corpus").iterdir() if (p / "key.toml").exists())
    if args.only:
        corpora = [p for p in corpora if p.name == args.only]
    if not corpora:
        sys.exit("no corpus with a key.toml")

    for corpus in corpora:
        key = tomllib.load((corpus / "key.toml").open("rb"))
        module = key["corpus"].get("module", "")
        edges, declared = drsg_edges(args.drsg, corpus, corpus.name, module)
        s = score(key, edges, declared)
        report(f"{corpus.name}  (drsg)", key, s)
    print("\nMISSED is incompleteness. WRONG is a false edge — worse than a miss,"
          "\nsince it sends an agent to code that was never involved. GUESSED is a"
          "\ntool sounding certain where syntax is not. BLANKET is a gap the tool"
          "\ncovers with a general disclaimer rather than naming.")
    # Always 0: this reports what a tool does, it does not pass or fail it.
    # Turning a finding into a non-zero exit would need a recorded baseline to
    # compare against, which is a different job from measuring.
    return 0


if __name__ == "__main__":
    sys.exit(main())

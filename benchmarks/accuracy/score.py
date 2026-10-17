#!/usr/bin/env python3
"""Score a code-graph tool against the fixture keys.

What this measures, and why it is three numbers rather than one:

  **Recall** — of the calls whose target is unambiguous from syntax, how many
  does the tool report? A miss here is incompleteness: the agent asks "who
  calls this" and is told less than the source says.

  **Precision** — how many edges the tool asserts between known symbols are
  not real? Two things are counted. The key's named traps are same-name
  collisions and plausible-but-wrong targets, chosen to be tempting. But a
  trap list only catches what its author thought of, so precision is also
  measured over the **whole declared universe**: any edge between two symbols
  the key locates, which is neither a real call nor a candidate at an
  ambiguous site, is a false edge. That caught a competitor asserting
  `Cache.Get -> AuditStore.note` — a pair no hand-written trap list had
  thought to forbid. A false edge is worse than a miss: it sends an agent to
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

Scoring another tool over the same corpus is the point of the key's
tool-neutral vocabulary. A tool is added as an adapter in `TOOLS` below: given
a corpus it returns the call edges it found and the call sites where it
declared a gap, both in the key's names. Nothing else about the benchmark
changes, which is what makes the comparison fair — the same fixtures, the same
key, the same three questions.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import tools_competitors as competitors

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent


def run(args: list[str], cwd: Path | None = None) -> str:
    p = subprocess.run(args, capture_output=True, text=True, cwd=cwd, check=False)
    return p.stdout


def drsg_edges(drsg: str, corpus: Path, plane: str, module: str,
               strip: str = "") -> tuple[set, set]:
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
        caller = normalise(a, module, strip)
        if b.startswith("?::"):
            declared.add(caller)
        else:
            edges.add((caller, normalise(b, module, strip)))
    return edges, declared


def normalise(name: str, module: str, strip: str = "") -> str:
    """A tool's key in the vocabulary the fixture key uses.

    `module` is guarded: an empty one would make the prefix a bare "." and
    strip every dot in the name, which is how this first reported 0% recall
    for a plugin that had in fact found everything.
    """
    # Rust writes a trait implementation as `<Type as Trait>::method`; the key
    # names it by the type, since that is where the body lives.
    name = re.sub(r"<([^>]+?) as [^>]+>::", r"\1::", name)
    if strip:
        name = re.sub(strip, "", name)
    if module:
        # Each plugin spells the prefix its language's way: `orders::` in Rust,
        # `acme/orders.` in Go, `ts/` for a TypeScript directory.
        for sep in ("::", "/", "."):
            name = name.replace(f"{module}{sep}", "", 1)
    # Go writes a pointer receiver as `(*Type).method`.
    name = re.sub(r"\(\*?([A-Za-z_][\w]*)\)\.", r"\1.", name)
    return name.strip()


def score(key: dict, edges: set, declared: set) -> dict:
    found = [e for e in key["resolved"] if (e["from"], e["to"]) in edges]
    missed = [e for e in key["resolved"] if (e["from"], e["to"]) not in edges]
    asserted = [e for e in key["absent"] if (e["from"], e["to"]) in edges]

    # Every edge the key accounts for: a real call, or a target the tool could
    # defensibly reach at a site syntax cannot settle.
    universe = set(key.get("symbols", {}))
    legitimate = {(e["from"], e["to"]) for e in key["resolved"]}
    for e in key["ambiguous"]:
        for c in [*e.get("candidates", []), e.get("interface_method")]:
            if c:
                legitimate.add((e["from"], c))
        # An interface method to its own implementations is dispatch, not a
        # call. A tool that models it — codegraph answers `callees Store.Get`
        # with MemStore.Get and AuditStore.Get — is making a defensible choice
        # about what an agent wants, and counting it as a false call edge
        # would misreport that as an error.
        iface = e.get("interface_method")
        if iface:
            for c in e.get("candidates", []):
                legitimate.add((iface, c))
    invented = sorted(
        (a, b) for a, b in edges
        if a in universe and b in universe and (a, b) not in legitimate
    )

    per_site, blanket, guessed, followed = [], [], [], []
    for e in key["ambiguous"]:
        frm = e["from"]
        cands = e.get("candidates", [])
        picked = [c for c in cands if (frm, c) in edges]
        iface = e.get("interface_method")
        if picked and len(cands) == 1 and len(picked) == 1:
            # One candidate, and the tool found it. That is local dataflow —
            # `f = normalize; f(x)` has exactly one binding in scope — not a
            # tool choosing among alternatives and sounding sure. Credited
            # separately from a guess, because it is the opposite of one.
            followed.append({**e, "picked": picked})
        elif picked:
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
        "followed": followed, "invented": invented,
    }


def report(name: str, key: dict, s: dict) -> None:
    nres, nabs, namb = len(key["resolved"]), len(key["absent"]), len(key["ambiguous"])
    rec = len(s["found"]) / nres * 100 if nres else 0.0
    prec = (nabs - len(s["asserted"])) / nabs * 100 if nabs else 0.0
    invented = s["invented"]
    not_guessed = len(s["per_site"]) + len(s["blanket"]) + len(s["followed"])
    print(f"\n=== {name} ===")
    print(f"  recall     {rec:5.1f}%   {len(s['found'])}/{nres} unambiguous calls found")
    print(f"  precision  {prec:5.1f}%   {nabs - len(s['asserted'])}/{nabs} traps avoided"
          + (f"; {len(invented)} further false edge(s)" if invented else
             "; no other false edge between known symbols"))
    print(f"  honesty            {not_guessed}/{namb} ambiguous sites not guessed at, of which")
    print(f"                     {len(s['per_site'])} named the gap, "
          f"{len(s['blanket'])} left only the blanket lower-bound note"
          + (f", {len(s['followed'])} resolved by following the one binding"
             if s["followed"] else ""))
    for e in s["followed"]:
        print(f"    FOLLOWED  {e['from']} -> {e['picked'][0]} at {e['site']} "
              f"(the only candidate in scope)")
    for e in s["missed"]:
        print(f"    MISSED    {e['from']} -> {e['to']}  ({e['why']})")
    for e in s["asserted"]:
        print(f"    WRONG     {e['from']} -> {e['to']}  ({e['why']})")
    for e in s["guessed"]:
        print(f"    GUESSED   {e['from']} picked {e['picked']} at {e['site']}")
    for a, b in invented:
        print(f"    INVENTED  {a} -> {b}  (no such call, and not a candidate anywhere)")
    for e in s["blanket"]:
        print(f"    BLANKET   {e['from']} at {e['site']} — no edge, no named gap")


def drsg_adapter(args, corpus: Path, key: dict) -> tuple[set, set]:
    """drsg: digest the corpus, then read every CALLS edge out of the plane."""
    return drsg_edges(
        args.drsg, corpus, corpus.name,
        key["corpus"].get("module", ""), key["corpus"].get("strip", ""),
    )


def _workdir(corpus: Path, args) -> Path:
    """A copy of the corpus for a tool that writes its index beside the source.

    A fresh directory every time, never a reused path. codegraph creates
    `.codegraph/` and leaves a daemon holding it; deleting and recreating the
    same path underneath that daemon made `init` fail silently, and the tool
    then scored 14% on a language it indexes perfectly well.
    """
    root = Path(args.workdir)
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{corpus.name}-", dir=root)) / corpus.name
    shutil.copytree(corpus, work)
    return work


def codegraph_adapter(args, corpus: Path, key: dict) -> tuple[set, set]:
    exe = args.codegraph or competitors.find("codegraph")
    if not exe:
        sys.exit("codegraph is not on PATH — pass --codegraph <path to its bin>")
    work = _workdir(corpus, args)
    r = subprocess.run([exe, "init", "-y", str(work)], capture_output=True,
                       text=True, check=False)
    if r.returncode != 0 or "Done" not in r.stdout:
        # Scoring an index that was never built reports the tool as hopeless
        # when the harness is at fault. Fail loudly instead.
        sys.exit(f"codegraph init failed for {corpus.name}:\n{r.stdout[-400:]}{r.stderr[-400:]}")
    return competitors.codegraph_edges(exe, work, key)


def cbm_adapter(args, corpus: Path, key: dict) -> tuple[set, set]:
    exe = args.cbm or competitors.find("codebase-memory-mcp")
    if not exe:
        sys.exit("codebase-memory-mcp is not on PATH — pass --cbm <path>")
    return competitors.cbm_edges(exe, _workdir(corpus, args), key)


# Each entry takes (args, corpus, key) and returns (edges, declared gaps) in
# the key's vocabulary. Adding a tool is adding one of these.
TOOLS = {
    "drsg": drsg_adapter,
    "codegraph": codegraph_adapter,
    "codebase-memory-mcp": cbm_adapter,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drsg", default=str(REPO / "target/release/drsg"))
    ap.add_argument("--only", help="score one corpus by directory name")
    ap.add_argument("--tool", default="drsg", choices=sorted(TOOLS),
                    help="which tool to score over the fixtures")
    ap.add_argument("--codegraph", help="path to the codegraph binary")
    ap.add_argument("--cbm", help="path to the codebase-memory-mcp binary")
    ap.add_argument("--workdir", default=str(Path(tempfile.gettempdir()) / "drsg-accuracy"),
                    help="where to copy a corpus for tools that index in place")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    corpora = sorted(p for p in (ROOT / "corpus").iterdir() if (p / "key.toml").exists())
    if args.only:
        corpora = [p for p in corpora if p.name == args.only]
    if not corpora:
        sys.exit("no corpus with a key.toml")

    adapter = TOOLS[args.tool]
    for corpus in corpora:
        key = tomllib.load((corpus / "key.toml").open("rb"))
        edges, declared = adapter(args, corpus, key)
        s = score(key, edges, declared)
        unverified = " — key UNVERIFIED" if key["corpus"].get("unverified") else ""
        report(f"{corpus.name}  ({args.tool}){unverified}", key, s)
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

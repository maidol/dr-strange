#!/usr/bin/env python3
"""Check the Go key against `callgraph`, before the key is used to score anyone.

The key is hand-written, which is the honest weakness of a fixture benchmark:
whoever writes the fixture and the answer both can miss the same thing. This
reads an independent opinion from `golang.org/x/tools/cmd/callgraph` and
reports where it disagrees.

It is a check, not the key itself, because both of callgraph's algorithms are
wrong in ways that matter here, in opposite directions:

  * **RTA** is reachability-based. It never sees a function `main` cannot
    reach, so `viaMethodValue`, `viaDefer` and `viaGo` are simply absent from
    its answer — not denied, absent.
  * **CHA** sees every function, and resolves a call through a function-typed
    variable to *every function in the universe with that signature*. For this
    fixture it claims `viaFuncValue` calls `strings.ToUpper`, `path.Base` and
    thirty others. None of that is true of the program.

So: an edge in `resolved` must appear in at least one of them (if neither has
it, the key is probably asserting a call that does not exist), and an edge in
`absent` must not appear in RTA (CHA's noise is not evidence of anything).

    uv run --no-project --with tomli python benchmarks/accuracy/crosscheck_go.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus" / "go"
MODULE = "acme/orders"


def callgraph(algo: str) -> set[tuple[str, str]]:
    exe = shutil.which("callgraph") or str(Path.home() / "go/bin/callgraph")
    if not Path(exe).exists():
        sys.exit(
            "callgraph is not installed — "
            "go install golang.org/x/tools/cmd/callgraph@latest"
        )
    out = subprocess.run(
        [exe, f"-algo={algo}", "-format={{.Caller}} -> {{.Callee}}", "./..."],
        cwd=CORPUS, capture_output=True, text=True, check=False,
    ).stdout
    edges = set()
    for line in out.splitlines():
        if "->" not in line or MODULE not in line:
            continue
        a, b = (normalise(p.strip()) for p in line.split("->", 1))
        # A self-edge here is the closure collapse above, not a recursive call
        # the key would ever assert.
        if a and b and a != b:
            edges.add((a, b))
    return edges


def normalise(name: str) -> str:
    """`acme/orders.(*Cache).Get` and the like, in the key's vocabulary.

    A closure is `parent$1` to callgraph — a synthetic function with no name
    in the source. The key has no symbol for it, so it collapses into its
    parent: a call written inside a closure literal is a call the enclosing
    function's text causes. The resulting self-edge (`parent -> parent`) is
    dropped by the caller.
    """
    name = name.replace(f"{MODULE}.", "")
    name = re.sub(r"\(\*?([A-Za-z_][\w]*)\)\.", r"\1.", name)
    name = re.sub(r"\$\d+$", "", name)
    return name.strip()


def main() -> int:
    key = tomllib.load((CORPUS / "key.toml").open("rb"))
    rta, cha = callgraph("rta"), callgraph("cha")
    both = rta | cha
    problems = []

    for e in key["resolved"]:
        pair = (e["from"], e["to"])
        if pair not in both:
            problems.append(
                f"resolved {pair[0]} -> {pair[1]}: neither RTA nor CHA has it; "
                f"the key may be asserting a call that is not there"
            )

    for e in key["absent"]:
        pair = (e["from"], e["to"])
        if pair in rta:
            problems.append(
                f"absent {pair[0]} -> {pair[1]}: RTA says this call DOES exist; "
                f"the key is wrong"
            )

    # Every `ambiguous` site should be one where the two algorithms disagree,
    # or where CHA offers more than one target. If they agree on exactly one,
    # the call was not ambiguous after all and belongs in `resolved`.
    for e in key["ambiguous"]:
        frm = e["from"]
        cha_targets = {b for a, b in cha if a == frm}
        rta_targets = {b for a, b in rta if a == frm}
        if cha_targets and rta_targets and cha_targets == rta_targets and len(cha_targets) == 1:
            problems.append(
                f"ambiguous {frm}: both algorithms resolve it to exactly "
                f"{cha_targets} — it may not be ambiguous"
            )

    print(f"RTA edges: {len(rta)}   CHA edges: {len(cha)}   (within {MODULE})")
    print(f"key: {len(key['resolved'])} resolved, {len(key['ambiguous'])} ambiguous, "
          f"{len(key['absent'])} absent")
    if problems:
        print("\ndisagreements:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("\nthe key agrees with callgraph everywhere it can be checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())

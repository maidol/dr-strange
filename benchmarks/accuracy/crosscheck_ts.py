#!/usr/bin/env python3
"""Check the TypeScript key against the compiler's own type checker.

`crosscheck_ts.mjs` drives the TypeScript API over the fixture and reports
what each call expression binds to. That is the same authority an editor uses
for go-to-definition, which makes it the strongest oracle in this suite after
Go's — and unlike Go's it needs no approximation at all for annotated code.

Its bound is worth stating, because it is the mirror of RTA's. The checker
answers for the **declared** type: a call through an interface resolves to the
interface's method and never to an implementation. That is precisely the line
the key draws between `resolved` and `ambiguous`, so the oracle can check both
sides of it — a `resolved` edge must appear, and an `ambiguous` site must
resolve to the interface method rather than to any one class.

    uv run --no-project --with tomli python benchmarks/accuracy/crosscheck_ts.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus" / "ts"


def oracle() -> set[tuple[str, str]]:
    if not shutil.which("bun"):
        sys.exit("bun is not on PATH")
    if not (ROOT / "node_modules" / "typescript").exists():
        sys.exit(f"the oracle's dependency is missing — run `bun install` in {ROOT}")
    out = subprocess.run(
        ["bun", "--bun", str(ROOT / "crosscheck_ts.mjs"), str(CORPUS)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        sys.exit(f"the TypeScript oracle failed:\n{out.stderr[-600:]}")
    return {(a, b) for a, b in json.loads(out.stdout)}


def main() -> int:
    key = tomllib.load((CORPUS / "key.toml").open("rb"))
    seen = oracle()
    problems = []

    for e in key["resolved"]:
        pair = (e["from"], e["to"])
        if pair not in seen:
            problems.append(
                f"resolved {pair[0]} -> {pair[1]}: the checker does not bind "
                f"this call there; the key may be wrong"
            )

    for e in key["absent"]:
        pair = (e["from"], e["to"])
        if pair in seen:
            problems.append(f"absent {pair[0]} -> {pair[1]}: the checker says it exists")

    for e in key["ambiguous"]:
        iface = e.get("interface_method")
        if not iface:
            continue
        if (e["from"], iface) not in seen:
            problems.append(
                f"ambiguous {e['from']}: the key says this call goes through "
                f"{iface}, and the checker does not agree"
            )
        for c in e.get("candidates", []):
            if (e["from"], c) in seen:
                problems.append(
                    f"ambiguous {e['from']}: the checker resolves it to the "
                    f"implementation {c}, so it is not ambiguous after all"
                )

    print(f"the checker resolved {len(seen)} call(s) inside the fixture")
    print(f"key: {len(key['resolved'])} resolved, {len(key['ambiguous'])} ambiguous, "
          f"{len(key['absent'])} absent")
    if problems:
        print("\ndisagreements:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("\nthe key agrees with the TypeScript checker")
    return 0


if __name__ == "__main__":
    sys.exit(main())

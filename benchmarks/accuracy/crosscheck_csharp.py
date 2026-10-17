#!/usr/bin/env python3
"""Check the C# key against Roslyn's semantic model.

This one runs in CI rather than on a laptop: the C# fixture's key was written
on a machine with no C# compiler, which is why it carried `unverified = true`.
A GitHub runner has the .NET SDK, so `.github/workflows/accuracy.yml` builds
`oracle-csharp/` there and runs this.

The oracle is the compiler's own answer for what each call binds to — the same
authority an IDE uses, and the same shape as the TypeScript check. Its bound
is also the same: the model answers for the **declared** type, so a call
through an interface resolves to `Acme.IStore.Get` and never to an
implementation. That is exactly the line the key draws between `resolved` and
`ambiguous`, so it can check both sides.

    dotnet run --project benchmarks/accuracy/oracle-csharp -- <corpus>   # raw
    uv run --no-project --with tomli python benchmarks/accuracy/crosscheck_csharp.py
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
CORPUS = ROOT / "corpus" / "csharp"
ORACLE = ROOT / "oracle-csharp"


def oracle() -> set[tuple[str, str]]:
    if not shutil.which("dotnet"):
        sys.exit(
            "dotnet is not on PATH. This check needs the .NET SDK; CI runs it "
            "in .github/workflows/accuracy.yml, which is why the C# key is "
            "marked unverified for anyone without one."
        )
    out = subprocess.run(
        ["dotnet", "run", "--project", str(ORACLE), "-c", "Release", "--",
         str(CORPUS / "src")],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        sys.exit(f"the Roslyn oracle failed:\n{out.stderr[-900:]}")
    payload = out.stdout[out.stdout.index("["):]
    return {(a, b) for a, b in json.loads(payload)}


def main() -> int:
    key = tomllib.load((CORPUS / "key.toml").open("rb"))
    seen = oracle()
    problems = []

    for e in key["resolved"]:
        pair = (e["from"], e["to"])
        if pair not in seen:
            problems.append(
                f"resolved {pair[0]} -> {pair[1]}: Roslyn does not bind this "
                f"call there; the key may be wrong"
            )

    for e in key["absent"]:
        pair = (e["from"], e["to"])
        if pair in seen:
            problems.append(f"absent {pair[0]} -> {pair[1]}: Roslyn says it exists")

    for e in key["ambiguous"]:
        iface = e.get("interface_method")
        if iface and (e["from"], iface) not in seen:
            problems.append(
                f"ambiguous {e['from']}: the key routes this through {iface}, "
                f"and Roslyn does not agree"
            )
        for c in e.get("candidates", []):
            if (e["from"], c) in seen:
                problems.append(
                    f"ambiguous {e['from']}: Roslyn resolves it to the "
                    f"implementation {c}, so it is not ambiguous after all"
                )

    print(f"Roslyn resolved {len(seen)} call(s) inside the fixture")
    print(f"key: {len(key['resolved'])} resolved, {len(key['ambiguous'])} ambiguous, "
          f"{len(key['absent'])} absent")
    if problems:
        print("\ndisagreements:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("\nthe key agrees with Roslyn")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Check the Java key against the compiled bytecode.

The oracle here is the compiler's own output. `javac` turns every call into an
explicit invoke instruction naming its target, and `javap -c` prints them.
That is as authoritative as this gets: it is what actually runs.

Two properties of bytecode shape what it can check, and both are useful:

  * It records the **static** target. A call through an interface is
    `invokeinterface acme/Store.get`, never an implementation — the same line
    the key draws between `resolved` and `ambiguous`.
  * A lambda body is compiled into a synthetic `lambda$method$0`, reached by
    `invokedynamic`. Its calls are attributed back to the method that declares
    it, because the lambda is part of that method's own text — which is also
    how the key reads it.

    uv run --no-project --with tomli python benchmarks/accuracy/crosscheck_java.py
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
CORPUS = ROOT / "corpus" / "java"
OUT = CORPUS / "out"
PKG = "acme"

METHOD = re.compile(r"^\s{2}(?:\w[\w\s<>\[\],.]*?\s)?([\w$<>]+)\(")
# `javap` prints a same-class call with no owner at all — `// Method label:()`
# — so the owner is optional and defaults to the class being disassembled.
INVOKE = re.compile(r"//\s*(?:Interface)?Method\s+(?:([\w/$]+)\.)?([\w$<>]+):")


def compile_corpus() -> None:
    if not shutil.which("javac"):
        sys.exit("javac is not on PATH")
    OUT.mkdir(exist_ok=True)
    srcs = sorted(str(p) for p in (CORPUS / "src" / PKG).glob("*.java"))
    r = subprocess.run(["javac", "-d", str(OUT), *srcs], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        sys.exit(f"javac failed:\n{r.stderr[-600:]}")


def edges() -> set[tuple[str, str]]:
    """caller -> callee, from the disassembly, in the key's vocabulary."""
    classes = sorted(p.stem for p in (OUT / PKG).glob("*.class"))
    found: set[tuple[str, str]] = set()
    for cls in classes:
        r = subprocess.run(
            ["javap", "-c", "-p", "-cp", str(OUT), f"{PKG}.{cls}"],
            capture_output=True, text=True, check=False,
        )
        current = None
        for line in r.stdout.splitlines():
            m = METHOD.match(line)
            if m and "invoke" not in line:
                current = m.group(1)
                continue
            inv = INVOKE.search(line)
            if not inv or not current:
                continue
            owner, name = inv.group(1), inv.group(2)
            owner = owner.replace("/", ".") if owner else f"{PKG}.{cls}"
            if not owner.startswith(f"{PKG}."):
                continue  # a call into the JDK; the key does not name those
            # A lambda body is a synthetic method of the declaring method.
            caller = current
            lam = re.match(r"lambda\$(\w+)\$\d+", current)
            if lam:
                caller = lam.group(1)
            found.add((f"{PKG}.{cls}.{caller}", f"{owner}.{name}"))
    return found


def main() -> int:
    key = tomllib.load((CORPUS / "key.toml").open("rb"))
    compile_corpus()
    seen = edges()
    problems = []

    for e in key["resolved"]:
        pair = (e["from"], e["to"])
        if pair not in seen:
            problems.append(
                f"resolved {pair[0]} -> {pair[1]}: no invoke instruction for it"
            )

    for e in key["absent"]:
        pair = (e["from"], e["to"])
        if pair in seen:
            problems.append(f"absent {pair[0]} -> {pair[1]}: the bytecode has it")

    for e in key["ambiguous"]:
        iface = e.get("interface_method")
        if iface and (e["from"], iface) not in seen:
            problems.append(
                f"ambiguous {e['from']}: the key routes this through {iface}, "
                f"and the bytecode does not"
            )
        for c in e.get("candidates", []):
            if (e["from"], c) in seen:
                problems.append(
                    f"ambiguous {e['from']}: the bytecode names the "
                    f"implementation {c} directly, so it is not ambiguous"
                )

    print(f"{len(seen)} invoke(s) within {PKG} in the compiled bytecode")
    print(f"key: {len(key['resolved'])} resolved, {len(key['ambiguous'])} ambiguous, "
          f"{len(key['absent'])} absent")
    if problems:
        print("\ndisagreements:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("\nthe key agrees with the compiled bytecode")
    return 0


if __name__ == "__main__":
    sys.exit(main())

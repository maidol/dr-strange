#!/usr/bin/env python3
"""Check the C key against the compiled object code.

`objdump -d -r` shows every call the compiler emitted, and the two kinds this
fixture cares about are distinguishable without any approximation at all:

  * a **direct** call carries a symbol — `call <cache_get>` for a function in
    the same object, or a `R_X86_64_PLT32 normalize` relocation for one
    outside it;
  * an **indirect** call is `call *%rcx`, naming nothing, which is precisely
    what a call through a struct of function pointers compiles to.

So the object code draws the same line the key does, from the other side: a
`resolved` edge must appear as a named call, and an `ambiguous` site must
appear as an indirect one.

One caveat the key accounts for: the compiler rewrites calls. `printf` with a
trailing newline and no formatting becomes `puts`, so the key names no libc
function and this check ignores them.

    uv run --no-project --with tomli python benchmarks/accuracy/crosscheck_c.py
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
CORPUS = ROOT / "corpus" / "c"
OUT = CORPUS / "out"

LABEL = re.compile(r"^[0-9a-f]+ <([\w.]+)>:")
NAMED = re.compile(r"call\s+[0-9a-f]+ <([\w.]+)(?:\+0x[0-9a-f]+)?>")
RELOC = re.compile(r"R_X86_64_(?:PLT32|PC32)\s+([\w.]+)")
INDIRECT = re.compile(r"call\s+\*")


def compile_objects() -> list[Path]:
    if not shutil.which("gcc") or not shutil.which("objdump"):
        sys.exit("gcc and objdump are both needed")
    OUT.mkdir(exist_ok=True)
    objs = []
    for src in sorted(CORPUS.glob("*.c")):
        obj = OUT / f"{src.stem}.o"
        r = subprocess.run(
            ["gcc", "-c", "-O0", "-g", "-o", str(obj), str(src)],
            cwd=CORPUS, capture_output=True, text=True, check=False,
        )
        if r.returncode != 0:
            sys.exit(f"gcc failed on {src.name}:\n{r.stderr[-400:]}")
        objs.append(obj)
    return objs


def scan(objs: list[Path]) -> tuple[set[tuple[str, str]], set[str]]:
    """(named call edges, functions containing an indirect call)."""
    edges: set[tuple[str, str]] = set()
    indirect: set[str] = set()
    for obj in objs:
        out = subprocess.run(
            ["objdump", "-d", "-r", str(obj)], capture_output=True, text=True, check=False
        ).stdout
        current = None
        for line in out.splitlines():
            lab = LABEL.match(line)
            if lab:
                current = lab.group(1)
                continue
            if not current:
                continue
            if INDIRECT.search(line):
                indirect.add(current)
            for pat in (NAMED, RELOC):
                m = pat.search(line)
                if m and m.group(1) != current:
                    edges.add((current, m.group(1)))
    return edges, indirect


def main() -> int:
    key = tomllib.load((CORPUS / "key.toml").open("rb"))
    edges, indirect = scan(compile_objects())
    problems = []

    for e in key["resolved"]:
        pair = (e["from"], e["to"])
        if pair not in edges:
            problems.append(
                f"resolved {pair[0]} -> {pair[1]}: no named call to it in the object code"
            )

    for e in key["absent"]:
        pair = (e["from"], e["to"])
        if pair in edges:
            problems.append(f"absent {pair[0]} -> {pair[1]}: the object code has it")

    for e in key["ambiguous"]:
        if e["from"] not in indirect:
            problems.append(
                f"ambiguous {e['from']}: the object code has no indirect call "
                f"here, so this site may not be ambiguous"
            )

    print(f"{len(edges)} named call(s); {len(indirect)} function(s) with an indirect call")
    print(f"key: {len(key['resolved'])} resolved, {len(key['ambiguous'])} ambiguous, "
          f"{len(key['absent'])} absent")
    if problems:
        print("\ndisagreements:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("\nthe key agrees with the compiled object code")
    return 0


if __name__ == "__main__":
    sys.exit(main())

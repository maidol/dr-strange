#!/usr/bin/env python3
"""Check the one Rust claim a tool can settle: what the macro expands to.

Rust has no call-graph oracle of the kind Go has. rust-analyzer emits none,
and a MIR-level analysis would be a larger undertaking than the fixture it
would check. So this checks far less than its Go and Python counterparts, and
says so rather than implying coverage it does not have.

What it does check is the entry most likely to be wrong, because it is the one
whose call appears in no line of source: `via_macro` claims to call
`normalize` through a `macro_rules!`. `cargo expand` settles that mechanically.

Everything else in the Rust key rests on reading the source. That is the
weakest ground in this benchmark and the README says so.

    uv run --no-project --with tomli python benchmarks/accuracy/crosscheck_rust.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus" / "rust"


def expanded(module: str) -> str:
    if not shutil.which("cargo"):
        sys.exit("cargo is not on PATH")
    out = subprocess.run(
        ["cargo", "expand", "--offline", module],
        cwd=CORPUS, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        sys.exit(
            "cargo expand failed — install it with `cargo binstall cargo-expand`.\n"
            + out.stderr[-400:]
        )
    return out.stdout


def main() -> int:
    key = tomllib.load((CORPUS / "key.toml").open("rb"))
    src = expanded("dispatch")
    problems = []

    macro_entries = [e for e in key["resolved"] if "macro" in e["from"]]
    if not macro_entries:
        problems.append("the key no longer has a macro entry; this check is checking nothing")

    for e in macro_entries:
        fn = e["from"].rsplit("::", 1)[-1]
        callee = e["to"].rsplit("::", 1)[-1]
        body = src.split(f"fn {fn}", 1)
        if len(body) < 2:
            problems.append(f"{fn} is not in the expansion at all")
            continue
        # The expanded body runs to the closing brace of the function.
        window = body[1][: body[1].index("\n    }")] if "\n    }" in body[1] else body[1]
        if f"{callee}(" not in window:
            problems.append(
                f"resolved {e['from']} -> {e['to']}: the expansion of {fn} "
                f"does not call {callee}"
            )

    print(f"checked {len(macro_entries)} macro entr(ies) against `cargo expand`")
    print(f"key: {len(key['resolved'])} resolved, {len(key['ambiguous'])} ambiguous, "
          f"{len(key['absent'])} absent")
    print("the rest of this key rests on reading the source — Rust has no "
          "call-graph oracle here")
    if problems:
        print("\ndisagreements:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("\nthe macro expands to what the key says it does")
    return 0


if __name__ == "__main__":
    sys.exit(main())

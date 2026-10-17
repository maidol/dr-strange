#!/usr/bin/env python3
"""Check the Kotlin key against the compiled bytecode.

Kotlin targets the JVM, so the oracle is the same as Java's — `javap -c` names
the target of every invoke instruction — with three wrinkles of Kotlin's own
that this accounts for rather than tripping over:

  * **Top-level functions** live in a synthetic `<File>Kt` class, so
    `acme/StoreKt.normalize` is mapped back to `acme.normalize`, the name the
    source writes.
  * **Null checks.** Every public function begins with
    `Intrinsics.checkNotNullParameter`. That is the compiler's, not the
    source's, and is ignored along with the rest of `kotlin/`.
  * **Lambdas** are reached by `invokedynamic` and their bodies compiled into
    synthetic methods, which are attributed to the function that declares
    them.

    uv run --no-project --with tomli python benchmarks/accuracy/crosscheck_kotlin.py
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
CORPUS = ROOT / "corpus" / "kotlin"
OUT = CORPUS / "out"
PKG = "acme"

METHOD = re.compile(r"^\s{2}(?:\w[\w\s<>\[\],.$]*?\s)?([\w$<>-]+)\(")
INVOKE = re.compile(r"//\s*(?:Interface)?Method\s+(?:([\w/$]+)\.)?([\w$<>-]+):")


def compile_corpus() -> None:
    kotlinc = Path.home() / ".sdkman/candidates/kotlin/current/bin/kotlinc"
    exe = shutil.which("kotlinc") or (str(kotlinc) if kotlinc.exists() else None)
    if not exe:
        sys.exit("kotlinc is not installed — `sdk install kotlin`")
    OUT.mkdir(exist_ok=True)
    r = subprocess.run(
        [exe, str(CORPUS / "src"), "-d", str(OUT)],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        sys.exit(f"kotlinc failed:\n{r.stderr[-600:]}")


def source_name(owner: str, member: str) -> str:
    """The name the Kotlin source writes, from the JVM's name for it."""
    owner = owner.replace("/", ".")
    cls = owner.rsplit(".", 1)[-1]
    # A file class holds that file's top-level declarations.
    if cls.endswith("Kt"):
        return f"{PKG}.{member}"
    return f"{owner}.{member}"


def edges() -> set[tuple[str, str]]:
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
            owner = owner if owner else f"{PKG}/{cls}"
            if not owner.replace("/", ".").startswith(f"{PKG}."):
                continue  # kotlin/ intrinsics and the JDK
            caller_cls = cls.split("$", 1)[0]
            caller = current
            lam = re.match(r"(\w+)\$lambda\$\d+", current) or re.match(
                r"lambda\$(\w+)\$\d+", current
            )
            if lam:
                caller = lam.group(1)
            found.add((source_name(f"{PKG}/{caller_cls}", caller), source_name(owner, name)))
    return found


def main() -> int:
    key = tomllib.load((CORPUS / "key.toml").open("rb"))
    compile_corpus()
    seen = edges()
    problems = []

    for e in key["resolved"]:
        pair = (e["from"], e["to"])
        if pair not in seen:
            problems.append(f"resolved {pair[0]} -> {pair[1]}: no invoke for it")

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

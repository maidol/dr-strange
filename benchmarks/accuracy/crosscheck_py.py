#!/usr/bin/env python3
"""Check the Python key by running the fixture and watching what it calls.

Python has no oracle of the kind Go has. `pycg` does not install under a
current interpreter, and a type checker resolves types without emitting a call
graph. So this takes the other road: it exercises the fixture under
`sys.setprofile` and records the calls that **actually happen**.

That is truth rather than approximation — the opposite failure mode from Go's
RTA, which approximates but covers everything reachable. A dynamic trace
cannot over-report: if it saw the call, the call happened. It can only
under-report, by not exercising a path. So:

  * an edge in `resolved` that the exercise covers **must** appear — if it
    does not, the key is asserting a call the program does not make;
  * an edge in `absent` that appears is a key error, full stop;
  * an edge in `resolved` the exercise never reaches is reported as uncovered,
    not as a failure, so the gap in the exercise is visible rather than silent.

    uv run --no-project --with tomli python benchmarks/accuracy/crosscheck_py.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus" / "py"


def qualify(frame) -> str | None:
    """`module.Class.func` for a frame inside the corpus, else None."""
    code = frame.f_code
    file = Path(code.co_filename)
    try:
        file.relative_to(CORPUS)
    except ValueError:
        return None
    module = file.stem
    qual = getattr(code, "co_qualname", code.co_name)  # 3.11+ has co_qualname
    qual = qual.replace("<locals>.", "")
    return f"{module}.{qual}"


def trace() -> set[tuple[str, str]]:
    """Exercise the fixture, recording caller -> callee for corpus frames."""
    sys.path.insert(0, str(CORPUS))
    # Imported here, not at module scope: the corpus is only importable
    # once its directory is on the path.
    import dynamic
    import service
    import store

    edges: set[tuple[str, str]] = set()

    def profile(frame, event, _arg):
        if event != "call":
            return
        callee = qualify(frame)
        caller = qualify(frame.f_back) if frame.f_back else None
        if callee and caller:
            edges.add((caller, callee))
        # A call into the standard library leaves the corpus; record it by the
        # name the key uses, since the key names `json.dumps` and the like.
        if caller and not callee:
            mod = frame.f_globals.get("__name__", "")
            if mod and not mod.startswith("_"):
                edges.add((caller, f"{mod}.{frame.f_code.co_name}"))

    svc = service.Service(store.AuditStore(store.MemStore()))
    cache = service.Cache()
    cache.hits[1] = "warm"

    sys.setprofile(profile)
    try:
        svc.save("a", {"v": 1})
        svc.load("a")
        svc.warm(cache, 1)
        svc.helper()
        dynamic.direct()
        dynamic.fetch("k")
        dynamic.via_local_alias()
        dynamic.via_getattr(cache, "get")
    except Exception as exc:  # noqa: BLE001
        # The fixture is not under test here; a raise would hide the edges
        # already observed, which are what this function exists to return.
        print(f"note: the exercise raised {exc!r}; edges so far still count",
              file=sys.stderr)
    finally:
        sys.setprofile(None)
    return edges


def main() -> int:
    key = tomllib.load((CORPUS / "key.toml").open("rb"))
    seen = trace()
    problems, uncovered = [], []

    for e in key["resolved"]:
        pair = (e["from"], e["to"])
        callers = {a for a, _ in seen}
        if pair in seen:
            continue
        if pair[0] in callers:
            problems.append(
                f"resolved {pair[0]} -> {pair[1]}: the caller ran and never "
                f"called this; the key may be wrong"
            )
        else:
            uncovered.append(f"{pair[0]} -> {pair[1]}")

    for e in key["absent"]:
        pair = (e["from"], e["to"])
        if pair in seen:
            problems.append(f"absent {pair[0]} -> {pair[1]}: it DID happen; the key is wrong")

    print(f"observed {len(seen)} real call edges while exercising the fixture")
    print(f"key: {len(key['resolved'])} resolved, {len(key['ambiguous'])} ambiguous, "
          f"{len(key['absent'])} absent")
    if uncovered:
        print(f"\nnot exercised ({len(uncovered)}) — unchecked, not wrong:")
        for u in uncovered:
            print(f"  {u}")
    if problems:
        print("\ndisagreements:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("\nthe key agrees with what the program actually did")
    return 0


if __name__ == "__main__":
    sys.exit(main())

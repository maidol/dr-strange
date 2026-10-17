#!/usr/bin/env python3
"""Adapters for the other two code-graph tools, over the same fixtures.

Identity here is **file and line**, never the name. The three tools do not
agree on what a symbol is called: for the Go fixture, drsg writes
`acme/orders.Cache.Get`, codegraph writes `Get` and distinguishes it from the
two other `Get`s by printing `service.go:30`, and codebase-memory-mcp
qualifies an interface method (`Store.Get`) while leaving a struct's method
bare (`Get`). Matching on names would measure this harness's name-mangling
rather than any tool's accuracy, and would make a competitor look wrong for a
naming convention. Every key records where each symbol is declared; each
adapter maps whatever the tool returns back through that.

Neither tool is driven over MCP here. Both expose the same answers to a
command line — codegraph through `callees`, codebase-memory-mcp through a
read-only Cypher `query_graph` — which keeps the harness small and the runs
reproducible.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path


def locate(key: dict) -> dict[tuple[str, int], str]:
    """(file, line) -> the key's name for the symbol declared there."""
    out = {}
    for name, where in key.get("symbols", {}).items():
        file, _, line = where.rpartition(":")
        out[(file, int(line))] = name
    return out


def run(args: list[str], cwd: Path | None = None, env: dict | None = None) -> str:
    p = subprocess.run(
        args, capture_output=True, text=True, cwd=cwd, check=False,
        env={**os.environ, **(env or {})},
    )
    return p.stdout


# --------------------------------------------------------------------------
# codegraph — `callees <symbol>` per symbol, which is the question an agent
# asks. Its output names a symbol and prints `file.go:NN` beneath it.
# --------------------------------------------------------------------------
# The subject line carries its own location inline — `function main (go) —
# main.go:5` — while a callee's location is on the line beneath it. Parsing
# only the second form made the subject absorb the first callee's location,
# and every answer was then discarded as being about a different symbol.
CG_SUBJECT = re.compile(
    r"^\s*(?:method|function|struct|class|interface)\s+(\S+).*?—\s*([\w./-]+):(\d+)"
)
CG_HEAD = re.compile(r"^\s*(?:method|function|struct|class|interface)\s+(\S+)")
CG_LOC = re.compile(r"^\s+([\w./-]+):(\d+)\s*$")


def codegraph_edges(exe: str, workdir: Path, key: dict) -> tuple[set, set]:
    at = locate(key)
    edges: set[tuple[str, str]] = set()
    env = {"CODEGRAPH_TELEMETRY": "0"}  # do not phone home from a benchmark run
    # Externals have no line in the fixture, so they are matched by name —
    # the one identity every tool agrees on for a symbol it does not own.
    external = {
        e["to"] for e in key["resolved"] if e["to"] not in key.get("symbols", {})
    }
    for caller in key.get("symbols", {}):
        # The qualified name first: codegraph accepts `AuditStore.Get` and
        # answers about that one, where the bare `Get` picks whichever of the
        # three it likes and the answer is then thrown away as off-subject.
        # Only the qualified name. Retrying a legitimate "0 callees" with the
        # bare name answers about a different same-named symbol, and
        # attributing those callees here invented nine false edges for a tool
        # that had asserted none.
        out = run([exe, "callees", caller, "--path", str(workdir)], env=env)
        # A query whose name matches several definitions prints one block per
        # definition — "Callees of X — 3 distinct definitions". Each block
        # opens with its own subject and location. Folding them all under the
        # first subject invented edges that no block claimed.
        subject, pending = None, None
        for line in out.splitlines():
            inline = CG_SUBJECT.match(line)
            if inline:
                subject = at.get((inline.group(2), int(inline.group(3))))
                pending = None
                continue
            head = CG_HEAD.match(line)
            if head:
                pending = head.group(1)
                continue
            loc = CG_LOC.match(line)
            if loc and pending and subject:
                callee = at.get((loc.group(1), int(loc.group(2))))
                if callee:
                    edges.add((subject, callee))
                pending = None
                continue
            if pending and subject:
                # A name with no location beneath it is outside the corpus.
                for ext in external:
                    if ext.rsplit(".", 1)[-1] == pending:
                        edges.add((subject, ext))
                pending = None
    # codegraph prints no per-site gap, only what it found.
    return edges, set()


# --------------------------------------------------------------------------
# codebase-memory-mcp — one Cypher query for every CALLS edge, with the file
# and line of both ends so they can be identified.
# --------------------------------------------------------------------------
def cbm_edges(exe: str, workdir: Path, key: dict) -> tuple[set, set]:
    at = locate(key)
    project = str(workdir).strip("/").replace("/", "-").replace("_", "-")
    run([exe, "cli", "--quiet", "index_repository",
         json.dumps({"repo_path": str(workdir), "mode": "fast"})])
    query = (
        "MATCH (a)-[:CALLS]->(b) RETURN a.file AS af, a.start_line AS al, "
        "b.file AS bf, b.start_line AS bl"
    )
    out = run([exe, "cli", "--quiet", "query_graph",
               json.dumps({"project": project, "query": query, "max_rows": 999})])
    edges: set[tuple[str, str]] = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        try:
            af, al, bf, bl = parts[0], int(parts[1].strip('"')), parts[2], int(parts[3].strip('"'))
        except ValueError:
            continue
        a, b = at.get((Path(af).name, al)), at.get((Path(bf).name, bl))
        if a and b:
            edges.add((a, b))
    return edges, set()


def find(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None

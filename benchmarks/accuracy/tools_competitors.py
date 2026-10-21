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


def locate(key: dict, types: bool = False) -> dict[tuple[str, int], str]:
    """(file, line) -> the key's name for the symbol declared there.

    Keyed by both the path the key records and its basename, because tools
    report either: codebase-memory-mcp gives `src/acme/AuditStore.java` while
    the flat Go fixture makes the two forms identical. Comparing basenames
    only worked for Go and silently scored every nested corpus at zero.

    `types` adds the `[types]` table, whose entries are the declarations a
    reference points *at*. They stay out of the call tables on purpose: a
    type in the call universe would turn a tool's constructor edge into a
    false call, which is a modelling difference and not an error.
    """
    out = {}
    tables = ["symbols"] + (["types"] if types else [])
    for table in tables:
        for name, where in key.get(table, {}).items():
            file, _, line = where.rpartition(":")
            out[(file, int(line))] = name
            out[(Path(file).name, int(line))] = name
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
        # Keys name a symbol their language's way — `service.Service.save`,
        # `store::AuditStore::note`, `acme.Service.Save` — and codegraph wants
        # `Service.save`. Try the full name, then shorter suffixes of it. The
        # location check below rejects an answer about a different symbol, so
        # a shorter form cannot smuggle in the wrong one; without this,
        # TypeScript scored 14% on a fixture the tool indexes perfectly.
        parts = re.split(r"::|\.", caller)
        attempts = [caller, *(".".join(parts[-n:]) for n in (2, 1))]
        out = ""
        for attempt in dict.fromkeys(attempts):
            out = run([exe, "callees", attempt, "--path", str(workdir)], env=env)
            if "Callees of" in out and "(0)" not in out.split("\n")[1 if out.startswith("\n") else 0]:
                break
        # A query whose name matches several definitions prints one block per
        # definition — "Callees of X — 3 distinct definitions". Each block
        # opens with its own subject and location. Folding them all under the
        # first subject invented edges that no block claimed.
        subject, pending = None, None
        for line in out.splitlines():
            inline = CG_SUBJECT.match(line)
            if inline:
                sf, sn = inline.group(2), int(inline.group(3))
                subject = at.get((sf, sn)) or at.get((Path(sf).name, sn))
                pending = None
                continue
            head = CG_HEAD.match(line)
            if head:
                pending = head.group(1)
                continue
            loc = CG_LOC.match(line)
            if loc and pending and subject:
                lf, ln = loc.group(1), int(loc.group(2))
                callee = at.get((lf, ln)) or at.get((Path(lf).name, ln))
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
def cbm_edges(exe: str, workdir: Path, key: dict) -> tuple[set, set, set]:
    at = locate(key)
    # The project name comes from the indexer's own answer, never from munging
    # the path: the tool sanitises it its own way, and guessing silently
    # queried a project that did not exist — reporting 0% for a language it
    # had indexed perfectly.
    indexed = run([exe, "cli", "--quiet", "index_repository",
                   json.dumps({"repo_path": str(workdir), "mode": "fast"})])
    project = None
    for line in indexed.splitlines():
        if line.strip().startswith("{"):
            try:
                project = json.loads(line).get("project")
            except json.JSONDecodeError:
                continue
            if project:
                break
    if not project:
        msg = f"codebase-memory-mcp did not report a project for {workdir}"
        raise RuntimeError(msg)
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
        a = at.get((af, al)) or at.get((Path(af).name, al))
        b = at.get((bf, bl)) or at.get((Path(bf).name, bl))
        if a and b:
            edges.add((a, b))

    # It models references too, as USAGE. Same identity rule: file and line,
    # so a file-level node (start_line 0) simply does not resolve.
    refs: set[tuple[str, str]] = set()
    rt = locate(key, types=True)
    # USAGE and IMPLEMENTS both: the same reading drsg gets.
    usage = "\n".join(
        run([exe, "cli", "--quiet", "query_graph",
             json.dumps({"project": project, "max_rows": 999, "query": (
                 f"MATCH (a)-[:{rel}]->(b) RETURN a.file AS af, "
                 "a.start_line AS al, b.file AS bf, b.start_line AS bl")})])
        for rel in ("USAGE", "IMPLEMENTS"))
    for line in usage.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        try:
            af, al, bf, bl = parts[0], int(parts[1].strip('"')), parts[2], int(parts[3].strip('"'))
        except ValueError:
            continue
        a = rt.get((af, al)) or rt.get((Path(af).name, al))
        b = rt.get((bf, bl)) or rt.get((Path(bf).name, bl))
        if a and b and a != b:
            refs.add((a, b))
    return edges, set(), refs


# --------------------------------------------------------------------------
# GitNexus — `analyze` builds a LadybugDB graph in `.gitnexus/`, and `cypher`
# queries it. One generic `CodeRelation` table carries the kind as a `type`
# property, so every arm is one query.
#
# Two things about it that the others do not do, both recorded rather than
# acted on: it numbers lines from **zero** where the key (and every other
# tool) numbers from one, and it puts a `confidence` on every edge. The
# offset is corrected here and asserted, because silently sliding every
# location by one would score the tool at zero on a graph it built correctly.
# The confidence is deliberately *not* filtered on: choosing its threshold
# for it would be this harness deciding what the tool meant.
# --------------------------------------------------------------------------
GN_CALLS = (
    "MATCH (a)-[r]->(b) WHERE r.type = 'CALLS' "
    "RETURN a.filePath AS af, a.startLine AS al, b.filePath AS bf, b.startLine AS bl"
)
# Any edge that lands on a declared type is a reference to it — that covers
# `IMPLEMENTS` (a struct to its interface) and the `CALLS` edge GitNexus draws
# from a function to a type it constructs. Filtering by kind instead would be
# guessing which of its kinds the key means.
GN_REFS = (
    "MATCH (a)-[r]->(b) "
    "RETURN a.filePath AS af, a.startLine AS al, b.filePath AS bf, b.startLine AS bl"
)


def _gn_rows(out: str) -> list[list[str]]:
    """The rows of a `gitnexus cypher` answer, which is JSON holding markdown."""
    start = out.find("{")
    if start < 0:
        msg = f"gitnexus cypher printed no JSON:\n{out[-400:]}"
        raise RuntimeError(msg)
    answer = json.loads(out[start:])
    if "error" in answer:
        msg = f"gitnexus cypher failed: {answer['error']}"
        raise RuntimeError(msg)
    rows = []
    for line in answer.get("markdown", "").split("\n")[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4:
            rows.append(cells)
    return rows


def _gn_pairs(rows: list[list[str]], at: dict) -> set:
    """Rows of (file, line, file, line) as key names, line numbers +1."""
    pairs = set()
    for af, al, bf, bl in rows:
        try:
            a_line, b_line = int(al) + 1, int(bl) + 1
        except ValueError:
            continue
        a = at.get((af, a_line)) or at.get((Path(af).name, a_line))
        b = at.get((bf, b_line)) or at.get((Path(bf).name, b_line))
        if a and b and a != b:
            pairs.add((a, b))
    return pairs


def gitnexus_edges(exe: str, workdir: Path, key: dict) -> tuple[set, set, set]:
    env = {"DO_NOT_TRACK": "1", "SCARF_ANALYTICS": "false"}
    # `--skip-git`: a fixture is a directory, not a checkout, and without this
    # the tool declines to index at all.
    built = run([exe, "analyze", ".", "--skip-git"], cwd=workdir, env=env)
    if "indexed successfully" not in built:
        msg = f"gitnexus analyze failed in {workdir}:\n{built[-400:]}"
        raise RuntimeError(msg)

    calls = _gn_pairs(_gn_rows(run([exe, "cypher", GN_CALLS], cwd=workdir, env=env)),
                      locate(key))
    rows = _gn_rows(run([exe, "cypher", GN_REFS], cwd=workdir, env=env))
    if rows and not calls:
        # The graph has edges but none of them landed on a key symbol: the
        # location convention has moved again, and scoring would report a
        # working tool as useless.
        msg = ("gitnexus returned edges but none resolved to a key symbol — "
               "check the line-numbering offset in this adapter")
        raise RuntimeError(msg)
    types_at = locate(key, types=True)
    type_names = set(key.get("types", {}))
    refs = {(a, b) for a, b in _gn_pairs(rows, types_at) if b in type_names}
    # It names no per-site gap: an unresolved call is simply an edge it did
    # not draw, and the confidence on the ones it drew is not a gap report.
    return calls, set(), refs


# --------------------------------------------------------------------------
# A plain text search — the baseline the comparison is meaningless without.
# Every score above only means something against what an agent gets with no
# index at all: `rg '\bGet\b'` and read the hits.
#
# It is given the strongest form of that, not a strawman. Matching is on word
# boundaries rather than substrings, a declaration is not counted as a use of
# itself, a hit is attributed to the declaration it falls inside — what a
# careful reader does with grep output, since grep names only a file and a
# line — and a comment-only line is discarded, because attributing a doc
# comment to the declaration *above* it is an artefact of that attribution
# rather than anything a reader would conclude.
#
# What it cannot be given is language awareness. A name inside a string still
# counts, and three symbols sharing a bare name cannot be told apart, so one
# mention of `Get` reaches all three. That is the measurement, not a flaw in
# the adapter.
# --------------------------------------------------------------------------
# Enough to recognise a comment in all eight fixture languages; a line that is
# only a comment carries no call.
COMMENT = re.compile(r'^\s*(//|#|\*|/\*|--|"""|\'\'\')')
def _declarations(key: dict, types: bool) -> dict[str, list[tuple[int, str]]]:
    """file -> [(line, key name)], ascending: who owns each line."""
    by_file: dict[str, list[tuple[int, str]]] = {}
    for table in ["symbols"] + (["types"] if types else []):
        for name, where in key.get(table, {}).items():
            file, _, line = where.rpartition(":")
            by_file.setdefault(file, []).append((int(line), name))
    for decls in by_file.values():
        decls.sort()
    return by_file


def _owner(decls: list[tuple[int, str]], line: int) -> str | None:
    """The declaration a line falls inside: the last one at or above it."""
    owner = None
    for at, name in decls:
        if at > line:
            break
        owner = name
    return owner


def _bare(name: str) -> str:
    """`acme/orders.Cache.Get` -> `Get` — what the text actually carries."""
    return re.split(r"::|\.|/", name)[-1]


def _mentions(corpus: Path, key: dict, targets: dict, types: bool) -> set:
    decls = _declarations(key, types=types)
    # A bare name maps to every key name it could mean. Narrowing it would be
    # crediting the baseline with knowledge it does not have.
    wanted: dict[str, set[str]] = {}
    for name in targets:
        wanted.setdefault(_bare(name), set()).add(name)
    found: set[tuple[str, str]] = set()
    for file, file_decls in decls.items():
        path = corpus / file
        if not path.exists():
            continue
        lines = path.read_text(errors="replace").splitlines()
        for n, text in enumerate(lines, start=1):
            owner = _owner(file_decls, n)
            if owner is None or COMMENT.match(text):
                continue
            for bare, names in wanted.items():
                if not re.search(rf"\b{re.escape(bare)}\b", text):
                    continue
                for name in names:
                    # A declaration line, and a name mentioned inside its own
                    # body, are not calls. No key records a self-edge.
                    if targets[name] == f"{file}:{n}" or name == owner:
                        continue
                    found.add((owner, name))
    return found


def text_edges(corpus: Path, key: dict) -> tuple[set, set, set]:
    """Call edges and type references as a text search yields them.

    The gap set is empty, and that is the point: a text search declares
    nothing about what it may have missed or where a name was ambiguous.
    """
    # Externals carry no line in the fixture, so they are searched for by
    # name — the same allowance the codegraph adapter makes, and it raises
    # this baseline's recall rather than lowering it.
    external = {
        e["to"]: "" for e in key["resolved"] if e["to"] not in key.get("symbols", {})
    }
    calls = _mentions(corpus, key, {**key.get("symbols", {}), **external}, types=False)
    refs = _mentions(corpus, key, key.get("types", {}), types=True)
    return calls, set(), refs


def find(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None
